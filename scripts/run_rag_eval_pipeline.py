from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULT_CASES = Path("tests/metadata_and_retrieval_cases.json")
DEFAULT_REPORT = Path("tests/metadata_and_retrieval_report.json")
DEFAULT_BASE_URL = "http://127.0.0.1:8000"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the RAG evaluation pipeline: execute tests, then summarize the report."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Server base URL, default http://127.0.0.1:8000")
    parser.add_argument("--cases", default=str(DEFAULT_CASES), help="Path to test cases json")
    parser.add_argument("--report", default=str(DEFAULT_REPORT), help="Path to write report json")
    parser.add_argument("--only-category", choices=["retrieval", "metadata_filter"], help="Run only one category")
    parser.add_argument("--only-id", action="append", help="Run only the specified case id, can be repeated")
    return parser.parse_args()


def build_runner_command(args: argparse.Namespace) -> list[str]:
    cmd = [
        sys.executable,
        "scripts/run_metadata_tests.py",
        "--base-url",
        args.base_url,
        "--cases",
        args.cases,
        "--report",
        args.report,
    ]
    if args.only_category:
        cmd.extend(["--only-category", args.only_category])
    for case_id in args.only_id or []:
        cmd.extend(["--only-id", case_id])
    return cmd


def build_summary_command(report_path: str) -> list[str]:
    return [
        sys.executable,
        ".codex/skills/rag-eval-pipeline/scripts/summarize_report.py",
        report_path,
    ]


def main() -> int:
    args = parse_args()

    print("== RAG Eval: Run Tests ==")
    runner = subprocess.run(build_runner_command(args), check=False)

    report_path = Path(args.report)
    if not report_path.exists():
        print("\nReport was not generated. Skipping summary.")
        return runner.returncode

    print("\n== RAG Eval: Summarize Report ==")
    summary = subprocess.run(build_summary_command(args.report), check=False)

    if summary.returncode != 0:
        return summary.returncode
    return runner.returncode


if __name__ == "__main__":
    raise SystemExit(main())
