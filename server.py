from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

import chromadb
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI
from pydantic import BaseModel
from sentence_transformers import CrossEncoder, SentenceTransformer

from core.config import (
    DEFAULT_COLLECTION_NAME,
    DEFAULT_DB_DIR,
    DEFAULT_DOCS_DIR,
    DEFAULT_META,
    EMBEDDING_MODEL_NAME,
    GENERATION_MODEL_NAME,
    RERANK_MODEL_NAME,
    ZHIPU_BASE_URL,
)
from core.parsers import split_into_chunks
from core.store import (
    embed_chunks_batch,
    expand_query,
    retrieve,
    retrieve_hybrid_multi,
    retrieve_with_metadata,
)
from core.rag_chain import dedupe_sources, rerank, rerank_with_scores, RERANK_LOW_CONFIDENCE_THRESHOLD
from tools.checker import check_file, load_config

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
load_dotenv()

# ── 审计日志 ──────────────────────────────────────────────────────────────────
_AUDIT_LOG_PATH = Path("audit.log")
logging.basicConfig(level=logging.INFO)
_audit_logger = logging.getLogger("audit")
_audit_handler = logging.FileHandler(_AUDIT_LOG_PATH, encoding="utf-8")
_audit_handler.setFormatter(logging.Formatter("%(message)s"))
_audit_logger.addHandler(_audit_handler)
_audit_logger.propagate = False


def _write_audit(event: str, data: dict) -> None:
    """写入审计日志（NDJSON 格式，每行一条记录）。"""
    record = {"ts": datetime.now().isoformat(), "event": event, **data}
    _audit_logger.info(json.dumps(record, ensure_ascii=False))


_state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"Loading embedding model: {EMBEDDING_MODEL_NAME}")
    _state["embedding_model"] = SentenceTransformer(EMBEDDING_MODEL_NAME, device="cpu")

    print(f"Loading rerank model: {RERANK_MODEL_NAME}")
    _state["cross_encoder"] = CrossEncoder(RERANK_MODEL_NAME, device="cpu")

    chroma_client = chromadb.PersistentClient(path=str(Path(DEFAULT_DB_DIR)))
    _state["chroma_client"] = chroma_client
    _state["collection_name"] = DEFAULT_COLLECTION_NAME

    _state["llm_client"] = AsyncOpenAI(
        api_key=os.environ.get("ZHIPU_API_KEY"),
        base_url=ZHIPU_BASE_URL,
    )

    print("Server ready → http://127.0.0.1:8000")
    yield
    _state.clear()


app = FastAPI(lifespan=lifespan)


class QueryRequest(BaseModel):
    question: str
    retrieve_top_k: int = 5
    rerank_top_k: int = 3
    # 过滤字段（与 _META_FIELDS 对齐）
    product_line: str = ""
    version: str = ""
    department: str = ""
    confidentiality: str = ""
    doc_type: str = ""
    model_type: str = ""
    module: str = ""
    status: str = ""


def _normalize_version(v: str) -> str:
    """版本号归一化：去除首尾空白，如果不以 V/v 开头则加上 V。"""
    v = v.strip()
    if v and not v.upper().startswith("V"):
        v = "V" + v
    return v


def _build_where(req: "QueryRequest") -> dict | None:
    """将 QueryRequest 中有値的元数据字段转为 ChromaDB where 过滤器。"""
    filter_fields = (
        "product_line", "version", "department", "confidentiality",
        "doc_type", "model_type", "module", "status",
    )
    conditions = []
    for field in filter_fields:
        val = getattr(req, field, "")
        if not val:
            continue
        if field == "version":
            val = _normalize_version(val)
        conditions.append({field: {"$eq": val}})
    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


async def _rewrite_query_with_llm(llm_client: AsyncOpenAI, query: str) -> list[str]:
    """生成检索友好的等价问法，失败时返回空列表。"""
    prompt = (
        "请将用户问题改写为 1-2 条用于检索的等价问法。"
        "仅返回 JSON 数组字符串，不要解释。"
        f"\n用户问题: {query}"
    )
    try:
        resp = await asyncio.wait_for(
            llm_client.chat.completions.create(
                model=GENERATION_MODEL_NAME,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            ),
            timeout=0.35,
        )
        text = (resp.choices[0].message.content or "").strip()
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            return []
        return [str(x).strip() for x in parsed if str(x).strip()][:2]
    except Exception:
        return []


async def _stream_rag(request: QueryRequest) -> AsyncIterator[str]:
    try:
        embedding_model: SentenceTransformer = _state["embedding_model"]
        cross_encoder: CrossEncoder = _state["cross_encoder"]
        collection = _state["chroma_client"].get_or_create_collection(name=_state["collection_name"])
        llm_client: AsyncOpenAI = _state["llm_client"]

        where = _build_where(request)

        llm_rewrites_task = asyncio.create_task(_rewrite_query_with_llm(llm_client, request.question))
        llm_rewrites = await llm_rewrites_task
        expanded_queries = expand_query(request.question, llm_rewrites=llm_rewrites)

        # ── 混合检索（多查询扩展 + 向量/BM25 + RRF 融合）──────────────────────
        evidence_list: list[dict] = await asyncio.to_thread(
            retrieve_hybrid_multi,
            collection,
            embedding_model,
            expanded_queries,
            request.retrieve_top_k,
            where,
        )

        if not evidence_list:
            yield f"data: {json.dumps({'type': 'sources', 'sources': []}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'chunk', 'content': '知识库中未找到与该问题相关的内容，请尝试换一种问法或检查筛选条件。'}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            _write_audit("query", {
                "question": request.question,
                "filters": {k: v for k, v in request.model_dump().items() if k not in ("question", "retrieve_top_k", "rerank_top_k") and v},
                "low_confidence": True,
                "max_rerank_score": 0.0,
                "evidence_sources": [],
            })
            return

        # ── 重排序（带分数，用于置信度判断）────────────────────────────────────
        rerank_pool_k = max(request.rerank_top_k * 4, 12)
        reranked_candidates, max_score = await asyncio.to_thread(
            rerank_with_scores, cross_encoder, request.question, evidence_list, rerank_pool_k
        )
        reranked_evidence = dedupe_sources(reranked_candidates, request.rerank_top_k)

        low_confidence = max_score < RERANK_LOW_CONFIDENCE_THRESHOLD

        # 发送证据卡片（含来源、版本、部门、责任人等）
        yield f"data: {json.dumps({'type': 'sources', 'sources': reranked_evidence, 'low_confidence': low_confidence}, ensure_ascii=False)}\n\n"

        # 审计日志（记录置信度供缺口分析）
        _write_audit("query", {
            "question": request.question,
            "expanded_queries": expanded_queries,
            "filters": {k: v for k, v in request.model_dump().items() if k not in ("question", "retrieve_top_k", "rerank_top_k") and v},
            "evidence_sources": [e.get("source", "") for e in reranked_evidence],
            "max_rerank_score": max_score,
            "low_confidence": low_confidence,
        })

        prompt = f"""你是一位知识助手，请根据用户的问题和下列知识片段生成准确的回答。
每条片段都附有来源信息，回答时可引用来源文档名称。

用户问题: {request.question}

知识片段:
{chr(10).join(f"[来源: {e.get('source', '未知')} | 版本: {e.get('version', '-')} | 部门: {e.get('department', '-')}]" + chr(10) + e['content'] for e in reranked_evidence)}

请基于上述内容作答，不要编造信息。如果引用了某个片段，请注明来源文档名。"""

        stream = await llm_client.chat.completions.create(
            model=GENERATION_MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield f"data: {json.dumps({'type': 'chunk', 'content': delta}, ensure_ascii=False)}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"
    except Exception as exc:
        logging.exception("Query stream failed")
        yield f"data: {json.dumps({'type': 'chunk', 'content': f'查询失败：{type(exc).__name__}: {exc}'}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"


@app.post("/api/query")
async def query_endpoint(request: QueryRequest) -> StreamingResponse:
    return StreamingResponse(
        _stream_rag(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/ingest")
async def ingest_endpoint(
    files: list[UploadFile],
    product_line:    str = Form(""),
    version:         str = Form(""),
    department:      str = Form(""),
    confidentiality: str = Form("公开"),
    doc_type:        str = Form(""),
    model_type:      str = Form(""),
    module:          str = Form(""),
    status:          str = Form("已发布"),
    owner:           str = Form(""),
    effective_date:  str = Form(""),
    doc_id:          str = Form(""),
) -> dict:
    """上传文档并写入向量库，支持设置完整元数据字段。"""
    import hashlib
    import shutil
    import tempfile

    embedding_model: SentenceTransformer = _state["embedding_model"]
    collection = _state["chroma_client"].get_or_create_collection(name=_state["collection_name"])
    extra_metadata = {k: v for k, v in {
        "product_line":    product_line,
        "version":         _normalize_version(version) if version else "",
        "department":      department,
        "confidentiality": confidentiality,
        "doc_type":        doc_type,
        "model_type":      model_type,
        "module":          module,
        "status":          status,
        "owner":           owner,
        "effective_date":  effective_date,
        "doc_id":          doc_id,
    }.items() if v}

    ingested = []
    for upload in files:
        filename = upload.filename or "doc.md"
        suffix = Path(filename).suffix or ".md"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            shutil.copyfileobj(upload.file, tmp)
            tmp_path = Path(tmp.name)

        try:
            file_chunks = split_into_chunks(tmp_path)
            records = []
            for idx, chunk in enumerate(file_chunks):
                chunk_id = hashlib.sha256(f"{filename}:{idx}".encode()).hexdigest()
                content_hash = hashlib.sha256(chunk.encode()).hexdigest()
                metadata = {
                    "source": filename,
                    "chunk_index": idx,
                    "content_hash": content_hash,
                    **DEFAULT_META,
                    **extra_metadata,
                }
                records.append({"id": chunk_id, "document": chunk, "metadata": metadata})

            if records:
                embeddings = await asyncio.to_thread(
                    embed_chunks_batch, embedding_model, [r["document"] for r in records]
                )
                collection.upsert(
                    ids=[r["id"] for r in records],
                    documents=[r["document"] for r in records],
                    embeddings=embeddings,
                    metadatas=[r["metadata"] for r in records],
                )
            ingested.append({"file": filename, "chunks": len(records)})
            _write_audit("ingest", {"file": filename, "chunks": len(records), "metadata": extra_metadata})
        finally:
            tmp_path.unlink(missing_ok=True)

    return {
        "ingested": ingested,
        "total_chunks": sum(r["chunks"] for r in ingested),
    }


@app.post("/api/check")
async def check_endpoint(files: list[UploadFile]) -> dict:
    """接收上传的文件，逐个执行规则检查后返回问题列表。"""
    import tempfile, shutil

    cfg = load_config(None)
    results = []

    for upload in files:
        suffix = Path(upload.filename or "doc.md").suffix or ".md"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            shutil.copyfileobj(upload.file, tmp)
            tmp_path = Path(tmp.name)

        try:
            result = await asyncio.to_thread(check_file, tmp_path, cfg)
            result.file_path = upload.filename or tmp_path.name
        finally:
            tmp_path.unlink(missing_ok=True)

        results.append(result)
        _write_audit("check", {"file": upload.filename, "errors": result.error_count, "warnings": result.warning_count})

    return {
        "files": [r.to_dict() for r in results],
        "summary": {
            "total_files":    len(results),
            "total_errors":   sum(r.error_count   for r in results),
            "total_warnings": sum(r.warning_count for r in results),
            "total_infos":    sum(r.info_count    for r in results),
        },
    }


@app.get("/api/meta-options")
async def meta_options_endpoint() -> dict:
    """返回知识库中已有的产品线和版本去重列表，供前端下拉框使用。"""
    collection = _state["chroma_client"].get_or_create_collection(name=_state["collection_name"])
    results = await asyncio.to_thread(collection.get, include=["metadatas"])
    metadatas = results.get("metadatas") or []
    product_lines = sorted({m.get("product_line", "") for m in metadatas if m.get("product_line", "")})
    versions = sorted({m.get("version", "") for m in metadatas if m.get("version", "")})
    return {"product_lines": product_lines, "versions": versions}


@app.get("/api/admin/gaps")
async def gaps_endpoint(limit: int = 50) -> dict:
    """汇总低置信度命中的查询，用于知识缺口分析。

    读取 audit.log，筛选 low_confidence=true 的 query 事件，返回最近 limit 条。
    """
    if not _AUDIT_LOG_PATH.exists():
        return {"gaps": [], "total": 0}

    gaps = []
    with open(_AUDIT_LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("event") == "query" and record.get("low_confidence"):
                gaps.append({
                    "ts":              record.get("ts", ""),
                    "question":        record.get("question", ""),
                    "max_rerank_score": record.get("max_rerank_score", 0),
                    "filters":         record.get("filters", {}),
                })

    gaps.sort(key=lambda r: r["ts"], reverse=True)
    return {"gaps": gaps[:limit], "total": len(gaps)}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
