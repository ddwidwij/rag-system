---
name: rag-eval-pipeline
description: Generate retrieval and metadata-filter test plans, create or update test cases and execution scripts, run the local RAG evaluation against /api/query, and analyze the report. Use when the user wants an end-to-end workflow for RAG retrieval accuracy checks, metadata filter validation, automated execution, or failure analysis in this repository.
---

# RAG Eval Pipeline

Use this skill when the task is to turn a RAG accuracy idea into runnable evaluation artifacts and a concrete report.

## Scope

This skill covers four outputs:

1. Test plan framing for retrieval accuracy and metadata-filter accuracy
2. A runnable case file under `tests/`
3. A runnable execution script under `scripts/`
4. A report plus a concise failure analysis

## Default Workflow

1. Inspect the local API contract before writing tests.
   Read only the relevant parts of `server.py`, `main.py`, `core/store.py`, and the frontend filter fields if needed.
2. Create or update the case file.
   Default path: `tests/metadata_and_retrieval_cases.json`
3. Create or update the runner script.
   Default path: `scripts/run_metadata_tests.py`
4. Validate syntax before execution.
   Use `python3 -m py_compile` on any changed Python files.
5. Run the evaluation against the local service.
   Default base URL: `http://127.0.0.1:8000`
   If sandbox blocks localhost access, rerun with escalation instead of stopping.
6. Write the raw report.
   Default path: `tests/metadata_and_retrieval_report.json`
7. Summarize the report with the helper script in this skill.
   Use `scripts/summarize_report.py` from this skill against the report file.
8. Return a short analysis.
   Always include pass rate, per-category pass rate, top failure cases, and the next code/data fixes.

## One-Command Entry

Use the repository wrapper when the user wants the whole flow in one command:

```bash
python3 scripts/run_rag_eval_pipeline.py
```

It runs `scripts/run_metadata_tests.py` first and then summarizes `tests/metadata_and_retrieval_report.json`.

## Case File Rules

- Keep one JSON file with a top-level `cases` array.
- Separate `retrieval` and `metadata_filter` cases with the `category` field.
- Each case should contain:
  - `id`
  - `category`
  - `question`
  - `filters`
  - `expected_sources`
  - `forbidden_sources`
  - `expected_keywords`
  - `notes`
- Prefer repo-relative paths in `expected_sources` and `forbidden_sources`.
- Use exact metadata values that the backend actually stores. Do not assume UI labels equal stored values.

## Runner Rules

- Prefer stdlib-only Python unless the repo already standardizes on another dependency.
- The runner should call `/api/query`, parse the SSE stream, and score:
  - `top1_matches_expected`
  - `top3_contains_expected`
  - `top3_excludes_forbidden`
  - `answer_contains_keywords`
- The runner should emit a machine-readable report JSON.
- Exit non-zero if any case fails.

## Execution Notes

- Check whether the local server is listening before assuming the runner is broken.
- If the request fails with sandbox-localhost restrictions, request escalation and rerun the same command.
- If the server is up but the run hangs, probe one case with `curl -N` to distinguish API stall from script bugs.

## Analysis Rules

- Treat retrieval quality and metadata-filter quality separately.
- Findings come before summaries.
- Focus on these failure modes:
  - wrong document family ranked ahead of the target
  - metadata value mismatch, especially `version`, `doc_type`, and `department`
  - answer generation missing required facts even when source ranking is correct
  - empty results caused by metadata normalization issues
- Use the helper summarizer in this skill for the first-pass report digest.

## Bundled Helper

- Report summarizer: `scripts/summarize_report.py`
  Use it after every run:

```bash
python3 .codex/skills/rag-eval-pipeline/scripts/summarize_report.py tests/metadata_and_retrieval_report.json
```

- Pipeline entrypoint: `scripts/run_rag_eval_pipeline.py`
  Use it for the combined flow:

```bash
python3 scripts/run_rag_eval_pipeline.py --only-category metadata_filter
```

## Expected Deliverables

- Updated `tests/metadata_and_retrieval_cases.json`
- Updated `scripts/run_metadata_tests.py`
- Fresh `tests/metadata_and_retrieval_report.json`
- A concise report analysis with concrete next fixes
