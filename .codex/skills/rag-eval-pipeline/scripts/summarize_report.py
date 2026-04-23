from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python3 summarize_report.py <report.json>")
        return 1

    report_path = Path(sys.argv[1])
    if not report_path.exists():
        print(f"report not found: {report_path}")
        return 1

    data = json.loads(report_path.read_text(encoding="utf-8"))
    summary = data.get("summary", {})
    results = data.get("results", [])

    by_category: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "passed": 0})
    failed_checks: dict[str, int] = defaultdict(int)
    failed_cases: list[dict] = []

    for result in results:
        category = result.get("category", "unknown")
        by_category[category]["total"] += 1
        by_category[category]["passed"] += int(bool(result.get("passed")))

        if not result.get("passed"):
            failed_cases.append(result)
            for check_name, ok in result.get("checks", {}).items():
                if not ok:
                    failed_checks[check_name] += 1

    print("Summary")
    print(f"  selected_cases: {summary.get('selected_cases', len(results))}")
    print(f"  passed_cases:   {summary.get('passed_cases', 0)}")
    print(f"  failed_cases:   {summary.get('failed_cases', 0)}")
    print(f"  pass_rate:      {summary.get('pass_rate', 0)}%")

    print("\nBy Category")
    for category in sorted(by_category):
        total = by_category[category]["total"]
        passed = by_category[category]["passed"]
        rate = round((passed / total) * 100, 2) if total else 0.0
        print(f"  {category}: {passed}/{total} ({rate}%)")

    print("\nTop Failed Checks")
    for name, count in sorted(failed_checks.items(), key=lambda item: (-item[1], item[0])):
        print(f"  {name}: {count}")

    print("\nFailed Cases")
    for result in failed_cases:
        failed = [name for name, ok in result.get("checks", {}).items() if not ok]
        print(f"  {result.get('case_id')}: {result.get('category')} | top1={result.get('top1_source') or '-'}")
        print(f"    failed_checks={', '.join(failed)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
