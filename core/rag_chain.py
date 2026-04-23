"""rag_chain.py — Rerank 与答案生成"""
from __future__ import annotations

from typing import List, Tuple

from openai import OpenAI
from sentence_transformers import CrossEncoder

from .config import GENERATION_MODEL_NAME

# 重排序得分低于此阈值时，视为低置信度命中
RERANK_LOW_CONFIDENCE_THRESHOLD = 0.1


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
    """
    if not evidence_list:
        return [], 0.0
    pairs = [(query, e["content"]) for e in evidence_list]
    scores = cross_encoder.predict(pairs).tolist()
    for e, s in zip(evidence_list, scores):
        e["rerank_score"] = round(float(s), 4)
    ranked = sorted(evidence_list, key=lambda e: e["rerank_score"], reverse=True)[:top_k]
    max_score = ranked[0]["rerank_score"] if ranked else 0.0
    return ranked, max_score


def dedupe_sources(
    evidence_list: List[dict],
    top_k: int,
) -> List[dict]:
    """在重排后按 source 去重，保留每个文档分数最高的 chunk。"""
    deduped: List[dict] = []
    seen_sources: set[str] = set()
    for evidence in evidence_list:
        source = evidence.get("source", "")
        if source in seen_sources:
            continue
        seen_sources.add(source)
        deduped.append(evidence)
        if len(deduped) >= top_k:
            break
    return deduped


def generate_answer(client: OpenAI, query: str, chunks: List[str]) -> str:
    """基于检索到的 chunk 调用 LLM 生成答案"""
    prompt = f"""你是一位知识助手，请根据用户的问题和下列片段生成准确的回答。

用户问题: {query}

相关片段:
{chr(10).join(chunks)}

请基于上述内容作答，不要编造信息。"""

    response = client.chat.completions.create(
        model=GENERATION_MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""
