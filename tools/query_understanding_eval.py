"""query_understanding_eval.py

三层对比评测：
  基线    — 原始问题 + 用户手填过滤
  P1（规则） — + 实体抄取/意图识别/自动过滤
  P2+P3（LLM）— + 结构化改写双变体 + 复杂问题分解

用法:
    # P1 规则层（离线）
    TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 python tools/query_understanding_eval.py

    # P2+P3 全量（需要 ZHIPU_API_KEY）
    python tools/query_understanding_eval.py --llm
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb
from dotenv import load_dotenv
from openai import AsyncOpenAI
from sentence_transformers import SentenceTransformer

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.config import DEFAULT_COLLECTION_NAME, DEFAULT_DB_DIR, EMBEDDING_MODEL_NAME, ZHIPU_BASE_URL
from core.store import expand_query, retrieve_hybrid_multi
from core.query_understanding import (
    extract_entities,
    classify_intent,
    build_suggested_filters,
    should_decompose,
    build_query_plan,
)

# ── 测试用例加载 ──────────────────────────────────────────────────────────────
CASES_PATH = ROOT_DIR / "tests" / "metadata_and_retrieval_cases.json"
TOP_K = 5


@dataclass
class Case:
    case_id: str
    category: str
    question: str
    user_filters: dict        # 用户手填的过滤（metadata_filter 测试用）
    expected_sources: list[str]
    forbidden_sources: list[str]
    expected_keywords: list[str]


def _load_cases() -> list[Case]:
    data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases = []
    for c in data.get("cases", []):
        cases.append(Case(
            case_id=c["id"],
            category=c.get("category", "retrieval"),
            question=c["question"],
            user_filters=c.get("filters", {}),
            expected_sources=c.get("expected_sources", []),
            forbidden_sources=c.get("forbidden_sources", []),
            expected_keywords=c.get("expected_keywords", []),
        ))
    return cases


# ── ChromaDB where 构建 ──────────────────────────────────────────────────────
def _dict_to_where(d: dict) -> dict | None:
    if not d:
        return None
    conditions = [{k: {"$eq": v}} for k, v in d.items() if v]
    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


def _merge_where(user_where: dict | None, auto_where: dict | None) -> dict | None:
    if not auto_where:
        return user_where
    if not user_where:
        return auto_where
    user_fields = set()
    if "$and" in user_where:
        for c in user_where["$and"]:
            user_fields |= {k for k in c if not k.startswith("$")}
    else:
        user_fields = {k for k in user_where if not k.startswith("$")}

    def _flatten(w: dict) -> list[dict]:
        if "$and" in w:
            return list(w["$and"])
        return [w]

    user_conds = _flatten(user_where)
    extra = [
        c for c in _flatten(auto_where)
        if not ({k for k in c if not k.startswith("$")} & user_fields)
    ]
    all_conds = user_conds + extra
    if len(all_conds) == 1:
        return all_conds[0]
    return {"$and": all_conds}


# ── 单条评测 ─────────────────────────────────────────────────────────────────
def _top_sources(results: list[dict], k: int) -> list[str]:
    seen, out = set(), []
    for r in results[:k]:
        s = r.get("source", "")
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _eval_one(
    case: Case,
    collection: Any,
    model: SentenceTransformer,
    llm_plan: Any | None = None,   # 如果传入则为 P2+P3 的 QueryPlan
) -> dict:
    user_where = _dict_to_where(case.user_filters)

    # ── 基线：原始问题 + 用户手填过滤 ──────────────────────────────────────────
    t0 = time.perf_counter()
    base_queries = expand_query(case.question, llm_rewrites=[], max_variants=5)
    base_results = retrieve_hybrid_multi(collection, model, base_queries, TOP_K, user_where)
    base_time = (time.perf_counter() - t0) * 1000

    # ── 查询理解层：规则（不调用 LLM）──────────────────────────────────────────
    entities = extract_entities(case.question)
    intent   = classify_intent(case.question)

    # 只在用户 **没有** 手填任何过滤时才补充自动过滤（retrieval 类），
    # metadata_filter 类用例用户已有过滤，仍补充未覆盖的字段
    user_fields = set(case.user_filters.keys())
    auto_where  = build_suggested_filters(entities, intent, user_fields)
    merged_where = _merge_where(user_where, auto_where if auto_where else None)

    t1 = time.perf_counter()
    qu_results = retrieve_hybrid_multi(collection, model, base_queries, TOP_K, merged_where)
    qu_time = (time.perf_counter() - t1) * 1000

    base_sources = _top_sources(base_results, TOP_K)
    qu_sources   = _top_sources(qu_results,   TOP_K)

    def _check(sources: list[str]) -> dict:
        top1_hit = bool(case.expected_sources) and any(
            s in sources[:1] for s in case.expected_sources
        )
        top_k_hit = bool(case.expected_sources) and any(
            s in sources for s in case.expected_sources
        )
        forbidden_free = not any(s in sources for s in case.forbidden_sources)
        return {"top1_hit": top1_hit, "topk_hit": top_k_hit, "forbidden_free": forbidden_free}

    base_check = _check(base_sources)
    qu_check   = _check(qu_sources)

    # ── P2+P3 LLM 层（可选）────────────────────────────────────────────────────
    llm_check   = None
    llm_sources: list[str] = []
    llm_meta: dict = {}
    if llm_plan is not None:
        llm_extras = llm_plan.rewrite_variants[1:] + llm_plan.sub_queries
        llm_queries = expand_query(llm_plan.rewritten_query, llm_rewrites=llm_extras)
        llm_merged_where = _merge_where(user_where, llm_plan.suggested_filters if llm_plan.suggested_filters else None)
        t2 = time.perf_counter()
        llm_results = retrieve_hybrid_multi(collection, model, llm_queries, TOP_K, llm_merged_where)
        llm_time = (time.perf_counter() - t2) * 1000
        llm_sources = _top_sources(llm_results, TOP_K)
        llm_check   = _check(llm_sources)
        llm_meta = {
            "rewritten_query":   llm_plan.rewritten_query,
            "rewrite_variants":  llm_plan.rewrite_variants,
            "sub_queries":       llm_plan.sub_queries,
            "is_complex":        llm_plan.is_complex,
            "expanded_queries":  llm_queries,
            "sources":           llm_sources,
            "latency_ms":        round(llm_time, 1),
            **(llm_check or {}),
        }

    return {
        "id":             case.case_id,
        "category":       case.category,
        "question":       case.question,
        "intent":         intent,
        "entities": {
            "model_types": entities.model_types,
            "versions":    entities.versions,
            "fault_codes": entities.fault_codes,
            "doc_ids":     entities.doc_ids,
        },
        "auto_where":     auto_where,
        "merged_where":   merged_where,
        "base": {**base_check, "sources": base_sources, "latency_ms": round(base_time, 1)},
        "qu":   {**qu_check,   "sources": qu_sources,   "latency_ms": round(qu_time, 1)},
        "llm":  llm_meta,
        "improved_p1":   (not base_check["top1_hit"] and qu_check["top1_hit"]),
        "degraded_p1":   (base_check["top1_hit"] and not qu_check["top1_hit"]),
        "improved_p2p3": llm_check is not None and (not qu_check["top1_hit"] and llm_check["top1_hit"]),
        "degraded_p2p3": llm_check is not None and (qu_check["top1_hit"] and not llm_check["top1_hit"]),
        # 向下兼容
        "improved": (not base_check["top1_hit"] and qu_check["top1_hit"]),
        "degraded":  (base_check["top1_hit"] and not qu_check["top1_hit"]),
    }


# ── 汇总输出 ──────────────────────────────────────────────────────────────────
def _print_table(results: list[dict], has_llm: bool = False) -> None:
    if has_llm:
        header = (f"{'ID':<5} {'类别':<16} {'意图':<12}"
                  f" {'基线Top1':>8} {'P1-Top1':>8} {'P2P3-Top1':>9}"
                  f" {'基线无禁':>8} {'P1-无禁':>8} {'P2P3-无禁':>9}")
    else:
        header = f"{'ID':<5} {'类别':<16} {'意图':<12} {'基线Top1':>8} {'QU-Top1':>8} {'基线无禁':>8} {'QU-无禁':>8} {'变化':>6}"
    print(header)
    print("─" * len(header))
    for r in results:
        p1_hit  = r["qu"]["top1_hit"]
        p1_forb = r["qu"]["forbidden_free"]
        if has_llm and r["llm"]:
            p2p3_hit  = r["llm"].get("top1_hit", "-")
            p2p3_forb = r["llm"].get("forbidden_free", "-")
            print(
                f"{r['id']:<5} {r['category']:<16} {r['intent']:<12}"
                f" {'✓' if r['base']['top1_hit'] else '✗':>8}"
                f" {'✓' if p1_hit else '✗':>8}"
                f" {'✓' if p2p3_hit else ('✗' if p2p3_hit is False else '-'):>9}"
                f" {'✓' if r['base']['forbidden_free'] else '✗':>8}"
                f" {'✓' if p1_forb else '✗':>8}"
                f" {'✓' if p2p3_forb else ('✗' if p2p3_forb is False else '-'):>9}"
            )
        else:
            change = "↑提升" if r["improved_p1"] else ("↓退步" if r["degraded_p1"] else "  —  ")
            print(
                f"{r['id']:<5} {r['category']:<16} {r['intent']:<12}"
                f" {'✓' if r['base']['top1_hit'] else '✗':>8}"
                f" {'✓' if p1_hit else '✗':>8}"
                f" {'✓' if r['base']['forbidden_free'] else '✗':>8}"
                f" {'✓' if p1_forb else '✗':>8}"
                f" {change:>6}"
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", action="store_true", help="启用 P2+P3 LLM 层评测（需要 ZHIPU_API_KEY）")
    args = parser.parse_args()

    load_dotenv()
    cases = _load_cases()
    print(f"加载测试用例: {len(cases)} 条\n")

    client = chromadb.PersistentClient(path=str(ROOT_DIR / DEFAULT_DB_DIR))
    collection = client.get_or_create_collection(name=DEFAULT_COLLECTION_NAME)
    model = SentenceTransformer(EMBEDDING_MODEL_NAME, device="cpu")

    # ── P2+P3：异步调用 LLM 为每条用例生成 QueryPlan ─────────────────────
    llm_plans: list[Any] = [None] * len(cases)
    if args.llm:
        api_key = os.environ.get("ZHIPU_API_KEY", "")
        if not api_key:
            print("[ERROR] 未设置 ZHIPU_API_KEY，跳过 LLM 层评测")
        else:
            async def _build_all_plans() -> list[Any]:
                llm_client = AsyncOpenAI(
                    api_key=api_key,
                    base_url=ZHIPU_BASE_URL,
                )
                print(f"\n[P2+P3] 正在为 {len(cases)} 条用例调用 LLM 改写/分解...")
                tasks = [
                    build_query_plan(
                        llm_client, c.question,
                        existing_filter_fields=set(c.user_filters.keys())
                    )
                    for c in cases
                ]
                return await asyncio.gather(*tasks)

            llm_plans = list(asyncio.run(_build_all_plans()))
            print(f"[P2+P3] 完成 {len(llm_plans)} 条 QueryPlan\n")

    has_llm = args.llm and any(p is not None for p in llm_plans)
    results = [_eval_one(c, collection, model, llm_plan=p) for c, p in zip(cases, llm_plans)]

    # ── 分类统计 ──────────────────────────────────────────────────────────────
    all_r  = results
    ret_r  = [r for r in results if r["category"] == "retrieval"]
    meta_r = [r for r in results if r["category"] == "metadata_filter"]

    def _stats(group: list[dict], label: str) -> None:
        n = len(group)
        if not n:
            return
        base_top1 = sum(r["base"]["top1_hit"] for r in group)
        qu_top1   = sum(r["qu"]["top1_hit"]   for r in group)
        base_forb = sum(r["base"]["forbidden_free"] for r in group)
        qu_forb   = sum(r["qu"]["forbidden_free"]   for r in group)
        print(f"\n【{label}】 共 {n} 条")
        print(f"  Top-1 命中率:  基线 {base_top1}/{n} ({base_top1/n:.0%})  →  P1规则 {qu_top1}/{n} ({qu_top1/n:.0%})  "
              f"({qu_top1-base_top1:+d})")
        print(f"  无禁止文档率:  基线 {base_forb}/{n} ({base_forb/n:.0%})  →  P1规则 {qu_forb}/{n} ({qu_forb/n:.0%})")
        if has_llm:
            llm_top1 = sum(r["llm"].get("top1_hit", False) for r in group if r["llm"])
            llm_forb = sum(r["llm"].get("forbidden_free", False) for r in group if r["llm"])
            llm_n    = sum(1 for r in group if r["llm"])
            print(f"  Top-1 命中率:  P2+P3(LLM) {llm_top1}/{llm_n} ({llm_top1/llm_n:.0%})  ({llm_top1-qu_top1:+d} vs P1)")
            print(f"  无禁止文档率:  P2+P3(LLM) {llm_forb}/{llm_n} ({llm_forb/llm_n:.0%})")
            p1p2_improved = sum(r["improved_p2p3"] for r in group)
            p1p2_degraded = sum(r["degraded_p2p3"] for r in group)
            print(f"  P2+P3 相比 P1: 提升 {p1p2_improved} 条  退步 {p1p2_degraded} 条")

    _stats(all_r,  "全部用例")
    _stats(ret_r,  "retrieval（纯检索）")
    _stats(meta_r, "metadata_filter（含筛选）")

    print("\n" + "═" * 90)
    _print_table(results, has_llm=has_llm)

    # ── P2+P3 改善/退步详情 ────────────────────────────────────────────────────
    if has_llm:
        improved_cases = [r for r in results if r.get("improved_p2p3")]
        if improved_cases:
            print("\n── P2+P3 比 P1 提升命中的用例 ──")
            for r in improved_cases:
                print(f"\n  [{r['id']}] {r['question']}")
                print(f"    P1来源:    {r['qu']['sources'][:2]}")
                print(f"    P2+P3来源: {r['llm']['sources'][:2]}")
                print(f"    改写变体: {r['llm'].get('rewrite_variants', [])}")
                if r['llm'].get('sub_queries'):
                    print(f"    子问题:   {r['llm']['sub_queries']}")
        degraded_cases = [r for r in results if r.get("degraded_p2p3")]
        if degraded_cases:
            print("\n── P2+P3 退步的用例 ──")
            for r in degraded_cases:
                print(f"\n  [{r['id']}] {r['question']}")
                print(f"    P1来源:    {r['qu']['sources'][:2]}")
                print(f"    P2+P3来源: {r['llm']['sources'][:2]}")

    # ── P1 未命中详情 ──────────────────────────────────────────────────────────
    missing_p1 = [r for r in results if not r["qu"]["top1_hit"] and bool(r.get("auto_where"))]
    if missing_p1:
        print("\n── P1 层未命中且有自动过滤的用例 ──")
        for r in missing_p1:
            print(f"  [{r['id']}] {r['question']}")
            print(f"    auto_where={r['auto_where']}  来源={r['qu']['sources'][:2]}")


if __name__ == "__main__":
    main()
