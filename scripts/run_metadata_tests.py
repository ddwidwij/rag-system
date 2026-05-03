from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CASES_PATH = Path("tests/metadata_and_retrieval_cases.json")
DEFAULT_REPORT_PATH = Path("tests/metadata_and_retrieval_report.json")
DEFAULT_BASE_URL = "http://127.0.0.1:8000"


@dataclass
class CaseResult:
    case_id: str
    category: str
    passed: bool
    score: int
    max_score: int
    top1_source: str
    top3_sources: list[str]
    answer_preview: str
    chunks_preview: str
    checks: dict[str, bool]
    details: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run retrieval and metadata filter tests against /api/query.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Server base URL, default http://127.0.0.1:8000")
    parser.add_argument("--cases", default=str(DEFAULT_CASES_PATH), help="Path to test cases json")
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH), help="Path to write json report")
    parser.add_argument("--only-category", choices=["retrieval", "metadata_filter"], help="Run only one category")
    parser.add_argument("--only-id", action="append", help="Run only the specified case id, can be repeated")
    return parser.parse_args()


def load_cases(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["cases"]


def should_run(case: dict[str, Any], args: argparse.Namespace) -> bool:
    if args.only_category and case.get("category") != args.only_category:
        return False
    if args.only_id and case.get("id") not in set(args.only_id):
        return False
    return True


def post_query(base_url: str, payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/query",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    sources: list[dict[str, Any]] = []
    answer_parts: list[str] = []

    with urllib.request.urlopen(req, timeout=120) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line.startswith("data: "):
                continue
            try:
                event = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            event_type = event.get("type")
            if event_type == "sources":
                sources = event.get("sources", []) or []
            elif event_type == "chunk":
                answer_parts.append(event.get("content", ""))
            elif event_type == "done":
                break

    return sources, "".join(answer_parts)


def normalize_source(path: str) -> str:
    return path.replace("\\", "/").strip()


def contains_any_source(actual_sources: list[str], expected_sources: list[str]) -> bool:
    actual_set = {normalize_source(s) for s in actual_sources}
    for source in expected_sources:
        if normalize_source(source) in actual_set:
            return True
    return False


def contains_forbidden_source(actual_sources: list[str], forbidden_sources: list[str]) -> bool:
    actual_set = {normalize_source(s) for s in actual_sources}
    for source in forbidden_sources:
        if normalize_source(source) in actual_set:
            return True
    return False


def keywords_hit(answer: str, keywords: list[str]) -> tuple[bool, list[str]]:
    if not keywords:
        return True, []
    missing = [kw for kw in keywords if kw not in answer]
    return not missing, missing


def evaluate_case(case: dict[str, Any], sources: list[dict[str, Any]], answer: str) -> CaseResult:
    top_sources = [normalize_source((src or {}).get("source", "")) for src in sources]
    top1 = top_sources[0] if top_sources else ""
    top3 = top_sources[:3]

    # 拼接所有检索到的 chunk 文本，用于验证答案是否可从检索结果中得出
    chunks_text = " ".join((src or {}).get("content", "") for src in sources)

    expected_sources = case.get("expected_sources", [])
    forbidden_sources = case.get("forbidden_sources", [])
    expected_keywords = case.get("expected_keywords", [])
    expected_chunk_keywords = case.get("expected_chunk_keywords", [])

    top1_ok = True if not expected_sources else normalize_source(top1) in {normalize_source(s) for s in expected_sources}
    top3_ok = True if not expected_sources else contains_any_source(top3, expected_sources)
    forbidden_ok = not contains_forbidden_source(top3, forbidden_sources)
    keywords_ok, missing_keywords = keywords_hit(answer, expected_keywords)
    chunk_kw_ok, missing_chunk_keywords = keywords_hit(chunks_text, expected_chunk_keywords)

    checks = {
        "top1_matches_expected": top1_ok,
        "top3_contains_expected": top3_ok,
        "top3_excludes_forbidden": forbidden_ok,
        "answer_contains_keywords": keywords_ok,
        "chunks_contain_keywords": chunk_kw_ok,
    }

    score = sum(1 for ok in checks.values() if ok)
    max_score = len(checks)

    return CaseResult(
        case_id=case["id"],
        category=case["category"],
        passed=all(checks.values()),
        score=score,
        max_score=max_score,
        top1_source=top1,
        top3_sources=top3,
        answer_preview=answer[:200],
        chunks_preview=chunks_text[:300],
        checks=checks,
        details={
            "expected_sources": expected_sources,
            "forbidden_sources": forbidden_sources,
            "expected_keywords": expected_keywords,
            "missing_keywords": missing_keywords,
            "expected_chunk_keywords": expected_chunk_keywords,
            "missing_chunk_keywords": missing_chunk_keywords,
            "filters": case.get("filters", {}),
            "question": case["question"],
            "notes": case.get("notes", ""),
        },
    )


def write_report(path: Path, results: list[CaseResult], summary: dict[str, Any]) -> None:
    payload = {
        "summary": summary,
        "results": [
            {
                "case_id": r.case_id,
                "category": r.category,
                "passed": r.passed,
                "score": r.score,
                "max_score": r.max_score,
                "top1_source": r.top1_source,
                "top3_sources": r.top3_sources,
                "answer_preview": r.answer_preview,
                "chunks_preview": r.chunks_preview,
                "checks": r.checks,
                "details": r.details,
            }
            for r in results
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def print_result(result: CaseResult) -> None:
    status = "PASS" if result.passed else "FAIL"
    print(f"[{status}] {result.case_id} ({result.category}) {result.score}/{result.max_score}")
    print(f"  top1: {result.top1_source or '-'}")
    if result.top3_sources:
        print(f"  top3: {', '.join(result.top3_sources)}")
    for name, ok in result.checks.items():
        if not ok:
            print(f"  failed_check: {name}")
    if result.details.get("missing_keywords"):
        print(f"  missing_keywords(answer): {', '.join(result.details['missing_keywords'])}")
    if result.details.get("missing_chunk_keywords"):
        print(f"  missing_keywords(chunks): {', '.join(result.details['missing_chunk_keywords'])}")


def main() -> int:
    args = parse_args()
    cases = [case for case in load_cases(Path(args.cases)) if should_run(case, args)]
    if not cases:
        print("No cases selected.")
        return 1

    results: list[CaseResult] = []
    for case in cases:
        payload = {
            "question": case["question"],
            "retrieve_top_k": 20,
            "rerank_top_k": 12,
            **case.get("filters", {}),
        }
        try:
            sources, answer = post_query(args.base_url, payload)
        except urllib.error.URLError as exc:
            print(f"Query failed for {case['id']}: {exc}")
            return 2
        result = evaluate_case(case, sources, answer)
        results.append(result)
        print_result(result)

    passed = sum(1 for r in results if r.passed)
    summary = {
        "base_url": args.base_url,
        "selected_cases": len(results),
        "passed_cases": passed,
        "failed_cases": len(results) - passed,
        "pass_rate": round(passed / len(results) * 100, 2),
    }
    write_report(Path(args.report), results, summary)

    print("\nSummary")
    print(f"  selected_cases: {summary['selected_cases']}")
    print(f"  passed_cases:   {summary['passed_cases']}")
    print(f"  failed_cases:   {summary['failed_cases']}")
    print(f"  pass_rate:      {summary['pass_rate']}%")
    print(f"  report:         {args.report}")
    return 0 if summary["failed_cases"] == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
