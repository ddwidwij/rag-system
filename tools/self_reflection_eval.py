"""self_reflection_eval.py — 自我反思与修正模块验收脚本

运行：
    python tools/self_reflection_eval.py

验收目标：
  ── 检索自检 critique_evidence ──────────────────────────────────────────────
  [C1]  全部通过 → passed=True, issues=[]
  [C2]  低置信度 → issues 含 "low_confidence"
  [C3]  类型不符 → issues 含 "type_mismatch"（intent=fault_code 但证据 doc_type=spec）
  [C4]  实体未命中 → issues 含 "entity_not_found"（故障码不在证据文本中）
  [C5]  多维失败 → low_confidence + entity_not_found 同时出现

  ── 重试决策 should_retry ───────────────────────────────────────────────────
  [R1]  有触发条件 + 未超限 → True
  [R2]  无触发条件匹配 → False
  [R3]  已达最大重试次数 → False

  ── 二次检索参数 build_retry_query ─────────────────────────────────────────
  [Q1]  exact_match_boost → 故障码前置到查询串
  [Q2]  tighten_filter → 追加 doc_type 过滤，查询串不变
  [Q3]  expand_query → 使用 sub_queries[0]，移除 doc_type
  [Q4]  relax_filter → 移除 doc_type，使用 raw_question
  [Q5]  relax_filter + $and 嵌套 where → 正确移除 doc_type 条件

  ── 回答质量自检 critique_answer ────────────────────────────────────────────
  [A1]  全部关键词覆盖 → passed=True, coverage=1.0
  [A2]  关键词全缺 → passed=False
  [A3]  覆盖率恰好 0.5（2/4）→ passed=True
  [A4]  无关键词（通用问题）→ passed=True, coverage=1.0
  [A5]  机型+版本+编号混合，部分覆盖 >= 0.5 → passed=True

  ── import 通烟测 ───────────────────────────────────────────────────────────
  [I1]  server.py 可正常 import（集成验证）
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import dataclass, field
from core.query_understanding import QueryEntities, QueryPlan
from core.router import RetryPolicy
from core.self_reflection import (
    critique_evidence,
    should_retry,
    build_retry_query,
    critique_answer,
    EvidenceCritiqueResult,
    AnswerCritiqueResult,
)
from core.rag_chain import RERANK_LOW_CONFIDENCE_THRESHOLD

# ── 辅助构建器 ────────────────────────────────────────────────────────────────

def _plan(
    *,
    raw: str = "VIS-1002 故障码原因",
    rewritten: str = "",
    intent: str = "fault_code",
    fault_codes: list[str] | None = None,
    model_types: list[str] | None = None,
    doc_ids: list[str] | None = None,
    versions: list[str] | None = None,
    sub_queries: list[str] | None = None,
    rewrite_variants: list[str] | None = None,
    is_complex: bool = False,
    suggested_filters: dict | None = None,
) -> QueryPlan:
    ents = QueryEntities(
        fault_codes=fault_codes or [],
        model_types=model_types or [],
        doc_ids=doc_ids or [],
        versions=versions or [],
    )
    return QueryPlan(
        raw_question=raw,
        rewritten_query=rewritten or raw,
        rewrite_variants=rewrite_variants or [rewritten or raw],
        sub_queries=sub_queries or [],
        intent=intent,
        entities=ents,
        suggested_filters=suggested_filters or {},
        is_complex=is_complex,
    )


def _evidence(*, content: str = "正常内容", doc_type: str = "fault_code") -> dict:
    return {"content": content, "doc_type": doc_type, "source": "test.md",
            "rerank_score": 0.9}


HIGH_SCORE = RERANK_LOW_CONFIDENCE_THRESHOLD + 0.1
LOW_SCORE  = RERANK_LOW_CONFIDENCE_THRESHOLD - 0.05


# ── 测试用例定义 ──────────────────────────────────────────────────────────────

@dataclass
class Case:
    id:   str
    desc: str
    fn:   object   # callable → bool


CASES: list[Case] = []


def _case(case_id: str, desc: str):
    def decorator(fn):
        CASES.append(Case(id=case_id, desc=desc, fn=fn))
        return fn
    return decorator


# ── C：critique_evidence ──────────────────────────────────────────────────────

@_case("C1", "全部通过：高分 + 类型匹配 + 实体命中")
def _():
    p = _plan(fault_codes=["VIS-1002"])
    ev = [_evidence(content="VIS-1002 故障定义与处理方案", doc_type="fault_code")]
    r = critique_evidence(p, ev, HIGH_SCORE)
    return r.passed and not r.issues


@_case("C2", "低置信度 → issues 含 low_confidence")
def _():
    p = _plan(fault_codes=["VIS-1002"])
    ev = [_evidence(content="VIS-1002 内容", doc_type="fault_code")]
    r = critique_evidence(p, ev, LOW_SCORE)
    return not r.passed and "low_confidence" in r.issues and not r.score_ok


@_case("C3", "类型不符 → issues 含 type_mismatch")
def _():
    p = _plan(intent="fault_code")
    ev = [_evidence(content="规格参数表", doc_type="spec")]
    r = critique_evidence(p, ev, HIGH_SCORE)
    return "type_mismatch" in r.issues and not r.type_ok


@_case("C4", "实体未命中 → issues 含 entity_not_found")
def _():
    p = _plan(fault_codes=["VIS-9999"])
    ev = [_evidence(content="关于 WPS-3000 规格的内容", doc_type="fault_code")]
    r = critique_evidence(p, ev, HIGH_SCORE)
    return "entity_not_found" in r.issues and "VIS-9999" in r.missing_entities


@_case("C5", "多维失败：low_confidence + entity_not_found")
def _():
    p = _plan(fault_codes=["ERR-0001"])
    ev = [_evidence(content="无关内容", doc_type="fault_code")]
    r = critique_evidence(p, ev, LOW_SCORE)
    return "low_confidence" in r.issues and "entity_not_found" in r.issues


# ── R：should_retry ───────────────────────────────────────────────────────────

@_case("R1", "有触发条件 + 未超限 → True")
def _():
    critique = EvidenceCritiqueResult(
        passed=False, issues=["low_confidence"], type_ok=True,
        score_ok=False, matched_entities=[], missing_entities=[], max_score=0.0,
    )
    policy = RetryPolicy(max_retries=1, trigger_conditions=["low_confidence"], strategy="relax_filter")
    return should_retry(critique, policy, attempt=0) is True


@_case("R2", "无触发条件匹配 → False")
def _():
    critique = EvidenceCritiqueResult(
        passed=False, issues=["type_mismatch"], type_ok=False,
        score_ok=True, matched_entities=[], missing_entities=[], max_score=0.5,
    )
    policy = RetryPolicy(max_retries=1, trigger_conditions=["low_confidence"], strategy="relax_filter")
    return should_retry(critique, policy, attempt=0) is False


@_case("R3", "已达最大重试次数 → False")
def _():
    critique = EvidenceCritiqueResult(
        passed=False, issues=["low_confidence"], type_ok=True,
        score_ok=False, matched_entities=[], missing_entities=[], max_score=0.0,
    )
    policy = RetryPolicy(max_retries=1, trigger_conditions=["low_confidence"], strategy="relax_filter")
    return should_retry(critique, policy, attempt=1) is False  # attempt >= max_retries


# ── Q：build_retry_query ──────────────────────────────────────────────────────

@_case("Q1", "exact_match_boost → 故障码前置到查询串")
def _():
    p = _plan(raw="这个故障怎么处理", fault_codes=["VIS-1002"])
    critique = EvidenceCritiqueResult(
        passed=False, issues=["entity_not_found"], type_ok=True, score_ok=True,
        matched_entities=[], missing_entities=["VIS-1002"], max_score=0.5,
    )
    q, f = build_retry_query(p, critique, "exact_match_boost", None)
    return "VIS-1002" in q and "这个故障怎么处理" in q


@_case("Q2", "tighten_filter → 追加 doc_type，查询串不变")
def _():
    p = _plan(raw="WPS-3000 版本说明", intent="release_note", model_types=["WPS-3000"], versions=["V2.1"])
    critique = EvidenceCritiqueResult(
        passed=False, issues=["type_mismatch"], type_ok=False, score_ok=True,
        matched_entities=[], missing_entities=[], max_score=0.5,
    )
    q, f = build_retry_query(p, critique, "tighten_filter", None)
    return f is not None and f.get("doc_type") == "release_note" and "WPS-3000" in q


@_case("Q3", "expand_query → 使用 sub_queries[0]，移除 doc_type")
def _():
    p = _plan(
        raw="WPS-3000 和 WPS-3200 差异",
        sub_queries=["WPS-3000 规格", "WPS-3200 规格"],
        is_complex=True,
    )
    critique = EvidenceCritiqueResult(
        passed=False, issues=["low_confidence"], type_ok=True, score_ok=False,
        matched_entities=[], missing_entities=[], max_score=0.05,
    )
    current_filters = {"doc_type": "spec", "model_type": "WPS-3000"}
    q, f = build_retry_query(p, critique, "expand_query", current_filters)
    return q == "WPS-3000 规格" and (f is None or "doc_type" not in f)


@_case("Q4", "relax_filter → 移除 doc_type，使用 raw_question")
def _():
    p = _plan(raw="接口测试流程", intent="general")
    critique = EvidenceCritiqueResult(
        passed=False, issues=["low_confidence"], type_ok=True, score_ok=False,
        matched_entities=[], missing_entities=[], max_score=0.05,
    )
    current_filters = {"doc_type": "spec"}
    q, f = build_retry_query(p, critique, "relax_filter", current_filters)
    return q == "接口测试流程" and f is None


@_case("Q5", "relax_filter + $and 嵌套 where → 正确移除 doc_type 条件")
def _():
    p = _plan(raw="查询文档")
    critique = EvidenceCritiqueResult(
        passed=False, issues=["low_confidence"], type_ok=True, score_ok=False,
        matched_entities=[], missing_entities=[], max_score=0.05,
    )
    current_filters = {"$and": [{"doc_type": {"$eq": "spec"}}, {"version": {"$eq": "V2.1"}}]}
    q, f = build_retry_query(p, critique, "relax_filter", current_filters)
    # doc_type 条件应被移除，version 条件应保留
    if f is None:
        return False
    remaining_str = str(f)
    return "doc_type" not in remaining_str and "version" in remaining_str


# ── A：critique_answer ────────────────────────────────────────────────────────

@_case("A1", "全部关键词覆盖 → passed=True, coverage=1.0")
def _():
    p = _plan(fault_codes=["VIS-1002"], model_types=["WPS-3000"])
    ev = [_evidence()]
    answer = "根据文档，WPS-3000 的 VIS-1002 故障码原因是传感器异常。"
    r = critique_answer(p, ev, answer)
    return r.passed and r.coverage_rate == 1.0


@_case("A2", "关键词全缺 → passed=False")
def _():
    p = _plan(fault_codes=["VIS-9999"], model_types=["WPS-9999"])
    ev = [_evidence()]
    answer = "该问题暂无相关信息。"
    r = critique_answer(p, ev, answer)
    return not r.passed and r.coverage_rate == 0.0


@_case("A3", "覆盖率恰好 0.5（2/4 关键词）→ passed=True")
def _():
    p = _plan(
        fault_codes=["VIS-1002", "ERR-999"],
        model_types=["WPS-3000", "WPS-9999"],
    )
    ev = [_evidence()]
    answer = "VIS-1002 和 WPS-3000 相关说明如下。"
    r = critique_answer(p, ev, answer)
    return r.passed and r.coverage_rate == 0.5


@_case("A4", "无关键词（通用问题）→ passed=True, coverage=1.0")
def _():
    p = _plan(raw="接口测试是什么", intent="general")
    ev = [_evidence(content="接口测试是指...")]
    answer = "接口测试是针对系统接口的功能性验证。"
    r = critique_answer(p, ev, answer)
    return r.passed and r.coverage_rate == 1.0


@_case("A5", "机型+版本+编号混合，覆盖 >= 0.5 → passed=True")
def _():
    p = _plan(
        fault_codes=["VIS-1002"],
        model_types=["WPS-3000"],
        versions=["V2.1"],
        doc_ids=["8D-2024-0088"],
    )
    ev = [_evidence()]
    # 覆盖 VIS-1002 / WPS-3000 / V2.1，缺 8D-2024-0088 → 3/4 = 0.75
    answer = "WPS-3000 V2.1 版本中，VIS-1002 故障码定义如下。"
    r = critique_answer(p, ev, answer)
    return r.passed and r.coverage_rate >= 0.5


# ── I：server.py import 烟测 ──────────────────────────────────────────────────

@_case("I1", "server.py 可正常 import（集成验证）")
def _():
    try:
        import server  # noqa: F401
        return True
    except Exception as e:
        print(f"    import error: {e}")
        return False


# ── 运行器 ────────────────────────────────────────────────────────────────────

def run_eval() -> None:
    total = len(CASES)
    passed = 0
    failed: list[str] = []

    col = 80
    print(f"\n{'='*col}")
    print(f"{'ID':<6} {'结果':<6} 描述")
    print(f"{'-'*col}")

    for case in CASES:
        try:
            ok = bool(case.fn())
        except Exception as exc:
            ok = False
            failed.append(f"{case.id}: 异常 {exc}")
        if ok:
            passed += 1
            print(f"{case.id:<6} {'✓':<6} {case.desc}")
        else:
            failed.append(f"{case.id}: {case.desc}")
            print(f"{case.id:<6} {'✗':<6} {case.desc}")

    print(f"{'='*col}")
    accuracy = passed / total * 100
    print(f"\n通过: {passed}/{total} ({accuracy:.0f}%)")
    if failed:
        print("失败项:")
        for f in failed:
            print(f"  ✗ {f}")

    all_pass = passed == total
    print(f"\n{'✓ 验收通过' if all_pass else '✗ 验收未通过'}")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    run_eval()
