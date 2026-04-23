from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import chromadb
from sentence_transformers import SentenceTransformer

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.config import (
    DEFAULT_COLLECTION_NAME,
    DEFAULT_DB_DIR,
    EMBEDDING_MODEL_NAME,
)
from core.store import expand_query, retrieve_hybrid, retrieve_hybrid_multi


@dataclass
class EvalCase:
    case_id: str
    query: str
    expected_sources: List[str]
    where: Dict[str, Any] | None = None


def _clean_text(text: str) -> str:
    return " ".join((text or "").replace("\n", " ").split())


def _load_synonyms(path: Path) -> Dict[str, List[str]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, List[str]] = {}
    for k, v in data.items():
        if not isinstance(k, str):
            continue
        key = k.strip()
        if not key:
            continue
        vals: List[str] = []
        if isinstance(v, list):
            vals = [str(x).strip() for x in v if str(x).strip()]
        elif isinstance(v, dict):
            terms = v.get("terms", [])
            if isinstance(terms, list):
                for item in terms:
                    if isinstance(item, str):
                        text = item.strip()
                    elif isinstance(item, dict):
                        text = str(item.get("text", "")).strip()
                    else:
                        text = ""
                    if text:
                        vals.append(text)
        if vals:
            out[key] = vals
    return out


def _pick_replacement(group: List[str], hit: str) -> str | None:
    candidates = [x for x in group if x != hit]
    if not candidates:
        return None
    # Prefer longer term to increase lexical difference.
    return sorted(candidates, key=len, reverse=True)[0]


def _build_query_from_text(text: str, hit: str, replacement: str, window: int = 18) -> str:
    idx = text.find(hit)
    if idx < 0:
        return ""
    start = max(0, idx - window)
    end = min(len(text), idx + len(hit) + window)
    snippet = text[start:end]
    query = snippet.replace(hit, replacement, 1)
    return _clean_text(query)


def generate_cases(
    output_path: Path,
    db_dir: Path,
    collection_name: str,
    synonyms_path: Path,
    size: int,
    seed: int,
) -> int:
    rng = random.Random(seed)
    client = chromadb.PersistentClient(path=str(db_dir))
    collection = client.get_or_create_collection(name=collection_name)
    raw = collection.get(include=["documents", "metadatas"])

    docs = raw.get("documents", []) or []
    metas = raw.get("metadatas", []) or []
    if not docs:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("", encoding="utf-8")
        return 0

    synonyms = _load_synonyms(synonyms_path)
    groups: List[List[str]] = [[k, *v] for k, v in synonyms.items()]

    order = list(range(len(docs)))
    rng.shuffle(order)

    cases: List[EvalCase] = []
    per_source_count: Dict[str, int] = {}

    for idx in order:
        if len(cases) >= size:
            break
        doc = _clean_text(str(docs[idx]))
        meta = metas[idx] or {}
        source = str(meta.get("source", "")).strip()
        if not source:
            continue
        if per_source_count.get(source, 0) >= 2:
            continue
        if len(doc) < 20:
            continue

        picked = False
        for group in groups:
            hit = next((term for term in group if term and term in doc), None)
            if not hit:
                continue
            replacement = _pick_replacement(group, hit)
            if not replacement:
                continue
            query = _build_query_from_text(doc, hit, replacement)
            if not query or len(query) < 6:
                continue
            case_id = f"T{len(cases) + 1:03d}"
            cases.append(EvalCase(case_id=case_id, query=query, expected_sources=[source]))
            per_source_count[source] = per_source_count.get(source, 0) + 1
            picked = True
            break

        if not picked and len(cases) < size:
            # Fallback case to reach target size.
            query = _clean_text(doc[:36])
            if len(query) >= 6:
                case_id = f"T{len(cases) + 1:03d}"
                cases.append(EvalCase(case_id=case_id, query=query, expected_sources=[source]))
                per_source_count[source] = per_source_count.get(source, 0) + 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for c in cases[:size]:
            row = {
                "id": c.case_id,
                "query": c.query,
                "expected_sources": c.expected_sources,
                "where": c.where or None,
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return min(len(cases), size)


def load_cases(path: Path) -> List[EvalCase]:
    cases: List[EvalCase] = []
    if not path.exists():
        return cases
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        cases.append(
            EvalCase(
                case_id=str(obj.get("id", f"T{len(cases) + 1:03d}")),
                query=str(obj.get("query", "")).strip(),
                expected_sources=[str(x) for x in obj.get("expected_sources", []) if str(x).strip()],
                where=obj.get("where") if isinstance(obj.get("where"), dict) else None,
            )
        )
    return [c for c in cases if c.query and c.expected_sources]


def _top_sources(items: List[Dict[str, Any]], top_k: int) -> List[str]:
    out: List[str] = []
    for e in items[:top_k]:
        s = str(e.get("source", "")).strip()
        if s and s not in out:
            out.append(s)
    return out


def run_eval(
    cases: List[EvalCase],
    db_dir: Path,
    collection_name: str,
    embedding_model_name: str,
    top_k: int,
    pass_uplift: float,
) -> Dict[str, Any]:
    client = chromadb.PersistentClient(path=str(db_dir))
    collection = client.get_or_create_collection(name=collection_name)
    embedding_model = SentenceTransformer(embedding_model_name, device="cpu")

    pre_hits = 0
    post_hits = 0
    pre_time = 0.0
    post_time = 0.0
    details: List[Dict[str, Any]] = []

    for c in cases:
        t0 = time.perf_counter()
        pre = retrieve_hybrid(collection, embedding_model, c.query, top_k, c.where)
        pre_time += time.perf_counter() - t0

        expanded = expand_query(c.query, llm_rewrites=[], max_variants=5)
        t1 = time.perf_counter()
        post = retrieve_hybrid_multi(collection, embedding_model, expanded, top_k, c.where)
        post_time += time.perf_counter() - t1

        pre_sources = _top_sources(pre, top_k)
        post_sources = _top_sources(post, top_k)

        pre_hit = any(s in pre_sources for s in c.expected_sources)
        post_hit = any(s in post_sources for s in c.expected_sources)
        pre_hits += int(pre_hit)
        post_hits += int(post_hit)

        details.append({
            "id": c.case_id,
            "query": c.query,
            "expected_sources": c.expected_sources,
            "pre_hit": pre_hit,
            "post_hit": post_hit,
            "pre_sources": pre_sources,
            "post_sources": post_sources,
            "expanded_queries": expanded,
        })

    total = len(cases)
    pre_rate = pre_hits / total if total else 0.0
    post_rate = post_hits / total if total else 0.0
    uplift = post_rate - pre_rate

    return {
        "total_cases": total,
        "pre_hits": pre_hits,
        "post_hits": post_hits,
        "pre_hit_rate": round(pre_rate, 4),
        "post_hit_rate": round(post_rate, 4),
        "uplift": round(uplift, 4),
        "pass_threshold": pass_uplift,
        "pass": uplift >= pass_uplift,
        "avg_pre_latency_ms": round((pre_time / total) * 1000, 2) if total else 0.0,
        "avg_post_latency_ms": round((post_time / total) * 1000, 2) if total else 0.0,
        "details": details,
    }


def save_report(report: Dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"synonym_eval_report_{ts}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同义词专项评测：自动生成 100 条并对比改造前后命中率")
    parser.add_argument("--db-dir", default=DEFAULT_DB_DIR)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION_NAME)
    parser.add_argument("--embedding-model", default=EMBEDDING_MODEL_NAME)
    parser.add_argument("--dataset", default="tests/synonym_eval_cases.jsonl")
    parser.add_argument("--synonyms", default="core/synonyms.json")
    parser.add_argument("--size", type=int, default=100)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--pass-uplift", type=float, default=0.15)
    parser.add_argument("--generate-only", action="store_true")
    parser.add_argument("--run-only", action="store_true")
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument("--force-regenerate", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    dataset_path = Path(args.dataset)
    db_dir = Path(args.db_dir)
    synonyms_path = Path(args.synonyms)

    do_generate = not args.run_only
    do_run = not args.generate_only

    if do_generate and (args.force_regenerate or not dataset_path.exists()):
        n = generate_cases(
            output_path=dataset_path,
            db_dir=db_dir,
            collection_name=args.collection,
            synonyms_path=synonyms_path,
            size=args.size,
            seed=args.seed,
        )
        print(f"[generate] dataset={dataset_path} cases={n}")

    if do_run:
        cases = load_cases(dataset_path)
        if not cases:
            raise SystemExit(f"No cases found: {dataset_path}")

        if len(cases) > args.size:
            cases = cases[: args.size]

        report = run_eval(
            cases=cases,
            db_dir=db_dir,
            collection_name=args.collection,
            embedding_model_name=args.embedding_model,
            top_k=args.top_k,
            pass_uplift=args.pass_uplift,
        )
        report_path = save_report(report, Path(args.report_dir))

        print("[result] total_cases=", report["total_cases"])
        print("[result] pre_hit_rate=", report["pre_hit_rate"])
        print("[result] post_hit_rate=", report["post_hit_rate"])
        print("[result] uplift=", report["uplift"])
        print("[result] pass_threshold=", report["pass_threshold"])
        print("[result] pass=", report["pass"])
        print("[result] avg_pre_latency_ms=", report["avg_pre_latency_ms"])
        print("[result] avg_post_latency_ms=", report["avg_post_latency_ms"])
        print("[result] report=", report_path)


if __name__ == "__main__":
    main()
