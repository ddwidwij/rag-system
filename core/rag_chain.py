"""rag_chain.py — Rerank 与答案生成"""
from __future__ import annotations

import re
from typing import List, Tuple

import jieba
from openai import OpenAI
from sentence_transformers import CrossEncoder

from .config import GENERATION_MODEL_NAME

# 重排序得分低于此阈值时，视为低置信度命中
RERANK_LOW_CONFIDENCE_THRESHOLD = 0.1
_RERANK_STOPWORDS = {
    "什么", "哪些", "多少", "如何", "怎么", "以及", "并且", "相关", "问题", "情况",
    "功能", "影响", "根本原因", "主要", "当前", "采取", "永久", "进行", "是否",
}
_RERANK_EXACT_PATTERNS = (
    re.compile(r"\b(?:QE|8D)-\d{4}-\d{4}\b", re.IGNORECASE),
    re.compile(r"\b[A-Z]{2,6}-\d{3,5}\b", re.IGNORECASE),
    re.compile(r"\bV\d+(?:\.\d+){1,2}\b", re.IGNORECASE),
)


def _extract_exact_terms(query: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for pattern in _RERANK_EXACT_PATTERNS:
        for match in pattern.finditer(query):
            term = match.group(0).strip()
            norm = term.upper()
            if norm in seen:
                continue
            seen.add(norm)
            terms.append(term)
    return terms


def _extract_model_terms(text: str) -> list[str]:
    return [m.group(0).upper() for m in _RERANK_EXACT_PATTERNS[1].finditer(text or "")]


def _doc_type_query_bonus(query: str, evidence: dict) -> float:
    doc_type = str(evidence.get("doc_type", "")).lower()
    if not doc_type:
        return 0.0

    bonus = 0.0
    if any(sig in query for sig in ("判定标准", "测试规范", "定义与规范", "测试项")):
        if doc_type == "test_spec":
            bonus += 1.0
        elif doc_type == "test_case":
            bonus -= 0.5
    if any(sig in query for sig in ("测试用例", "前置条件", "预期输出", "回归测试")):
        if doc_type == "test_case":
            bonus += 1.0
        elif doc_type == "test_spec":
            bonus -= 0.3
    return bonus


def _tokenize_overlap_terms(text: str) -> list[str]:
    pieces = [part.strip() for part in jieba.lcut(text) if part.strip()]
    result: list[str] = []
    seen: set[str] = set()
    for piece in pieces:
        norm = piece.lower()
        if len(piece) < 2 or norm in _RERANK_STOPWORDS:
            continue
        if re.fullmatch(r"[\W_]+", piece):
            continue
        if norm in seen:
            continue
        seen.add(norm)
        result.append(piece)
    return result


def _count_token_hits(tokens: list[str], text: str) -> int:
    if not tokens or not text:
        return 0
    lowered = text.lower()
    return sum(1 for token in tokens if token.lower() in lowered)


def _compute_rerank_bonus(query: str, evidence: dict) -> float:
    source = f"{evidence.get('source', '')} {evidence.get('doc_id', '')} {evidence.get('version', '')} {evidence.get('doc_type', '')}"
    content = evidence.get("content", "")
    exact_terms = _extract_exact_terms(query)
    query_models = _extract_model_terms(query)
    query_tokens = _tokenize_overlap_terms(query)

    bonus = 0.0
    source_lower = source.lower()
    content_lower = content.lower()

    for term in exact_terms:
        norm = term.lower()
        if norm in source_lower:
            bonus += 1.0
        if norm in content_lower:
            bonus += 0.8

    source_hits = _count_token_hits(query_tokens, source)
    content_hits = _count_token_hits(query_tokens, content)
    bonus += min(source_hits * 0.18, 0.9)
    bonus += min(content_hits * 0.06, 0.9)

    if query_models:
        evidence_models = set(_extract_model_terms(f"{source} {content}"))
        query_model_set = set(query_models)
        if query_model_set & evidence_models:
            bonus += 0.6
        else:
            query_prefixes = {item.split("-")[0] for item in query_model_set}
            evidence_prefixes = {item.split("-")[0] for item in evidence_models}
            if query_prefixes & evidence_prefixes:
                bonus -= 1.0
    bonus += _doc_type_query_bonus(query, evidence)
    return round(max(min(bonus, 2.5), -1.5), 4)


def rerank(
    cross_encoder: CrossEncoder,
    query: str,
    retrieved_chunks: List[str],
    top_k: int,
) -> List[str]:
    """对检索结果用 Cross-Encoder 重排序，返回最相关的 top_k 个 chunk（纯文本列表）。"""
    if not retrieved_chunks:
        return []
    pairs = [(query, chunk) for chunk in retrieved_chunks]
    scores = cross_encoder.predict(pairs)
    scored = sorted(zip(retrieved_chunks, scores), key=lambda x: x[1], reverse=True)
    return [chunk for chunk, _ in scored[:top_k]]


def rerank_with_scores(
    cross_encoder: CrossEncoder,
    query: str,
    evidence_list: List[dict],
    top_k: int,
) -> Tuple[List[dict], float]:
    """对带元数据的证据列表重排序，返回 (reranked_evidence, max_score)。

    max_score 用于判断是否为低置信度命中。
    在 chunk 前缀中注入来源路径，帮助 cross-encoder 感知文档上下文。
    """
    if not evidence_list:
        return [], 0.0
    pairs = [
        (query, f"[来源: {e.get('source', '')}]\n{e['content']}")
        for e in evidence_list
    ]
    scores = cross_encoder.predict(pairs).tolist()
    for e, s in zip(evidence_list, scores):
        e["rerank_score"] = round(float(s) + _compute_rerank_bonus(query, e), 4)
    ranked = sorted(evidence_list, key=lambda e: e["rerank_score"], reverse=True)[:top_k]
    max_score = ranked[0]["rerank_score"] if ranked else 0.0
    return ranked, max_score


def dedupe_sources(
    evidence_list: List[dict],
    top_k: int,
    max_per_source: int = 5,
) -> List[dict]:
    """在重排后按 source 去重，每个文档最多保留 max_per_source 个 chunk，总数不超过 top_k。

    max_per_source=5 保证同一文档可以贡献多个章节块，同时防止单文档占满所有名额。
    """
    deduped: List[dict] = []
    source_count: dict[str, int] = {}
    for evidence in evidence_list:
        source = evidence.get("source", "")
        count = source_count.get(source, 0)
        if count >= max_per_source:
            continue
        source_count[source] = count + 1
        deduped.append(evidence)
        if len(deduped) >= top_k:
            break
    return deduped


def generate_answer(client: OpenAI, query: str, chunks: List[str]) -> str:
    """基于检索到的 chunk 调用 LLM 生成答案"""
    prompt = f"""你是一位专业知识助手，请根据用户的问题和下列参考片段生成详尽、准确的回答。

用户问题: {query}

参考片段:
{chr(10).join(chunks)}

回答要求：
1. 直接引用原文中的具体数值、技术参数、产品型号、编号、代码等精确信息，不要省略或模糊化。
2. 如原文包含具体数字（如 512 路、±0.5 mA、1000V 等），请原样输出。
3. 如原文包含编号或标识符（如 BUG-1180、ELEC-001、PRB-C01-200 等），请原样引用。
4. 回答应完整覆盖问题涉及的各个方面，不要遗漏关键细节。
5. 仅依据上述片段内容作答，不要编造原文未提及的信息。"""

    response = client.chat.completions.create(
        model=GENERATION_MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""
