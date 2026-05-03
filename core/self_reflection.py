"""self_reflection.py — 检索结果自检、二次检索策略构建与回答质量自检

三层能力：
  1. critique_evidence()    — 检索结果自检（低置信 / 类型不符 / 实体未命中）
  2. should_retry()         — 决定是否触发二次检索
  3. build_retry_query()    — 按策略构建二次检索参数
  4. critique_answer()      — 回答质量自检（关键词覆盖率）
  5. llm_judge_relevance()  — LLM 语义判断问题与答案是否相关

设计原则：
  - 纯函数（除 llm_judge_relevance 外），无 I/O，方便单元测试
  - 仅使用已有 QueryPlan / RetryPolicy 数据结构
  - 首版全部为规则实现，接口预留 LLM 扩展点
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from core.query_understanding import QueryPlan, _INTENT_TO_DOC_TYPE
from core.rag_chain import RERANK_LOW_CONFIDENCE_THRESHOLD
from core.router import RetryPolicy


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class EvidenceCritiqueResult:
    """检索结果自检报告。

    passed              — True 表示证据质量足够，无需重试
    issues              — 发现的问题标签列表
                          可选值: "low_confidence", "type_mismatch", "entity_not_found"
    type_ok             — intent 与 Top-3 证据 doc_type 是否一致
    score_ok            — max_rerank_score 是否达到置信阈值
    matched_entities    — 在证据中找到的关键实体
    missing_entities    — 未在证据中找到的关键实体
    max_score           — 最高 rerank 分数（便于前端展示）
    """
    passed:           bool
    issues:           list[str]
    type_ok:          bool
    score_ok:         bool
    matched_entities: list[str]
    missing_entities: list[str]
    max_score:        float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AnswerCritiqueResult:
    """回答质量自检报告（关键词覆盖率）。

    passed            — 覆盖率 >= 0.5 或无关键词时视为通过
    coverage_rate     — 关键词覆盖率 0.0 ~ 1.0
    covered_keywords  — 答案中已出现的关键词
    missing_keywords  — 答案中未出现的关键词
    """
    passed:           bool
    coverage_rate:    float
    covered_keywords: list[str]
    missing_keywords: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


# ── 1. 检索结果自检 ───────────────────────────────────────────────────────────

def critique_evidence(
    plan: QueryPlan,
    evidence_list: list[dict],
    max_score: float,
) -> EvidenceCritiqueResult:
    """对检索结果做三维自检：置信度 / 文档类型 / 实体覆盖。

    Args:
        plan:          当前查询计划（含 intent、entities）
        evidence_list: rerank 后的证据列表（每项含 content / doc_type 等字段）
        max_score:     最高 rerank 分数

    Returns:
        EvidenceCritiqueResult，issues 为空则 passed=True
    """
    issues: list[str] = []

    # ── 维度 1：置信度 ────────────────────────────────────────────────────────
    score_ok = max_score >= RERANK_LOW_CONFIDENCE_THRESHOLD
    if not score_ok:
        issues.append("low_confidence")

    # ── 维度 2：doc_type vs intent 一致性 ────────────────────────────────────
    expected_doc_type = _INTENT_TO_DOC_TYPE.get(plan.intent)
    type_ok = True
    if expected_doc_type and evidence_list:
        top_doc_types = [e.get("doc_type", "") for e in evidence_list[:3]]
        if expected_doc_type not in top_doc_types:
            type_ok = False
            issues.append("type_mismatch")

    # ── 维度 3：关键实体覆盖 ──────────────────────────────────────────────────
    ents = plan.entities
    # 精确编号优先；版本号不强求（版本可能写在正文中多种形式）
    key_entities: list[str] = ents.fault_codes + ents.doc_ids + ents.model_types
    evidence_text = " ".join(e.get("content", "") for e in evidence_list).upper()

    matched: list[str] = []
    missing: list[str] = []
    for ent in key_entities:
        if ent.upper() in evidence_text:
            matched.append(ent)
        else:
            missing.append(ent)

    if missing:
        issues.append("entity_not_found")

    return EvidenceCritiqueResult(
        passed=len(issues) == 0,
        issues=issues,
        type_ok=type_ok,
        score_ok=score_ok,
        matched_entities=matched,
        missing_entities=missing,
        max_score=max_score,
    )


# ── 2. 是否触发重试 ───────────────────────────────────────────────────────────

def should_retry(
    critique: EvidenceCritiqueResult,
    retry_policy: RetryPolicy,
    attempt: int,
) -> bool:
    """判断是否应触发二次检索。

    条件：
      - 未超过最大重试次数
      - critique 中存在与 retry_policy.trigger_conditions 匹配的问题
    """
    if attempt >= retry_policy.max_retries:
        return False
    return any(issue in retry_policy.trigger_conditions for issue in critique.issues)


# ── 3. 构建二次检索参数 ───────────────────────────────────────────────────────

def build_retry_query(
    plan: QueryPlan,
    critique: EvidenceCritiqueResult,
    strategy: str,
    current_filters: dict[str, Any] | None,
) -> tuple[str, dict[str, Any] | None]:
    """按重试策略构建新的检索查询串和过滤条件。

    Args:
        plan:            原始查询计划
        critique:        自检报告（含 missing_entities）
        strategy:        重试策略名称
        current_filters: 当前 ChromaDB where 条件

    Returns:
        (new_query, new_filters) — 用于第二次 retrieve_hybrid_multi 调用
    """
    ents = plan.entities

    if strategy == "exact_match_boost":
        # 把精确标识符前置，强化精确召回
        prefix_parts = (
            ents.fault_codes
            + ents.doc_ids
            + ents.model_types
            + ents.versions
        )
        # 优先补充 missing 实体
        prefix = " ".join(
            p for p in prefix_parts if p in critique.missing_entities or not critique.missing_entities
        ) or " ".join(prefix_parts)
        new_query = f"{prefix} {plan.raw_question}".strip() if prefix else plan.raw_question
        return new_query, current_filters

    elif strategy == "tighten_filter":
        # 按 intent 推断 doc_type，追加进过滤条件
        expected_doc_type = _INTENT_TO_DOC_TYPE.get(plan.intent)
        new_filters = dict(current_filters) if current_filters else {}
        if expected_doc_type:
            new_filters["doc_type"] = expected_doc_type
        return plan.rewritten_query, new_filters or None

    elif strategy == "expand_query":
        # 使用子问题/备选改写展开；同时放宽 doc_type 过滤
        if plan.sub_queries:
            new_query = plan.sub_queries[0]
        elif len(plan.rewrite_variants) > 1:
            new_query = plan.rewrite_variants[1]
        else:
            new_query = plan.raw_question
        new_filters = _drop_doc_type(current_filters)
        return new_query, new_filters

    else:  # relax_filter（默认）
        # 去除 doc_type 约束，用原始问题兜底
        new_filters = _drop_doc_type(current_filters)
        return plan.raw_question, new_filters


def _drop_doc_type(filters: dict[str, Any] | None) -> dict[str, Any] | None:
    """从 ChromaDB where 条件中移除 doc_type 约束（放宽过滤）。"""
    if not filters:
        return None
    if "$and" in filters:
        remaining = [c for c in filters["$and"] if "doc_type" not in c]
        if not remaining:
            return None
        if len(remaining) == 1:
            return remaining[0]
        return {"$and": remaining}
    # 单字段条件
    if "doc_type" in filters:
        rest = {k: v for k, v in filters.items() if k != "doc_type"}
        return rest or None
    return filters


# ── 4. 回答质量自检 ───────────────────────────────────────────────────────────

def critique_answer(
    plan: QueryPlan,
    evidence_list: list[dict],
    answer: str,
) -> AnswerCritiqueResult:
    """检查 LLM 生成答案是否覆盖了关键实体。

    关键词来源：
      - plan.entities 中的 fault_codes / doc_ids / model_types / versions
      - 若无关键词（通用问题），直接视为通过

    覆盖率 >= 0.5 视为通过；首版规则实现，预留 LLM 自评扩展点。
    """
    ents = plan.entities
    keywords: list[str] = (
        ents.fault_codes
        + ents.doc_ids
        + ents.model_types
        + ents.versions
    )

    if not keywords:
        # 无关键词：通用问题，不做覆盖率限制
        return AnswerCritiqueResult(
            passed=True,
            coverage_rate=1.0,
            covered_keywords=[],
            missing_keywords=[],
        )

    answer_lower = answer.lower()
    covered: list[str] = []
    missing: list[str] = []
    for kw in keywords:
        if kw.lower() in answer_lower:
            covered.append(kw)
        else:
            missing.append(kw)

    coverage_rate = round(len(covered) / len(keywords), 2)
    return AnswerCritiqueResult(
        passed=coverage_rate >= 0.5,
        coverage_rate=coverage_rate,
        covered_keywords=covered,
        missing_keywords=missing,
    )


# ── 5. LLM 语义相关性判断 ──────────────────────────────────────────────────────

@dataclass
class LLMRelevanceResult:
    """LLM 对「问题 ↔ 答案」语义相关性的判断结果。

    relevant        — True 表示答案与问题相关
    confidence      — 置信度标签: "high" | "medium" | "low"
    reason          — LLM 给出的简短判断理由（一句话）
    raw_response    — LLM 原始回复（供调试）
    """
    relevant:     bool
    confidence:   str
    reason:       str
    raw_response: str

    def to_dict(self) -> dict:
        return asdict(self)


async def llm_judge_relevance(
    llm_client: Any,
    model: str,
    question: str,
    answer: str,
) -> LLMRelevanceResult:
    """用 LLM 判断「答案」是否回答了「问题」。

    Args:
        llm_client: AsyncOpenAI 实例
        model:      生成模型名称
        question:   用户原始问题
        answer:     RAG 系统生成的答案

    Returns:
        LLMRelevanceResult，relevant=True 表示答案与问题相关。
    """
    prompt = (
        "请判断下面的【答案】是否充分回答了【问题】。\n"
        "要求：\n"
        "1. 仅回复 JSON，格式为 {\"relevant\": true/false, \"confidence\": \"high|medium|low\", \"reason\": \"一句话理由\"}\n"
        "2. relevant=true 表示答案基本回答了问题；false 表示答案与问题无关或严重缺失。\n"
        "3. confidence 反映你的判断把握程度。\n\n"
        f"【问题】{question}\n\n"
        f"【答案】{answer}"
    )
    try:
        resp = await llm_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=128,
        )
        raw = (resp.choices[0].message.content or "").strip()
        # 提取 JSON（LLM 有时会在 JSON 前后加说明文字）
        import re, json as _json
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            data = _json.loads(m.group(0))
            return LLMRelevanceResult(
                relevant=bool(data.get("relevant", False)),
                confidence=str(data.get("confidence", "low")),
                reason=str(data.get("reason", "")),
                raw_response=raw,
            )
    except Exception:
        pass
    # 解析失败时保守返回 relevant=True，避免误触发重试
    return LLMRelevanceResult(
        relevant=True,
        confidence="low",
        reason="LLM 判断失败，跳过",
        raw_response="",
    )
