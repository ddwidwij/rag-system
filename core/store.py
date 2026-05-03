"""store.py — 向量库构建、元数据管理与检索"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import jieba
import chromadb
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from .config import DEFAULT_META, META_FIELDS

# ── jieba 初始化：加载技术领域自定义词典 ─────────────────────────────────────
_JIEBA_READY = False


def _ensure_jieba() -> None:
    """懒加载，只初始化一次。"""
    global _JIEBA_READY
    if _JIEBA_READY:
        return
    _user_dict = Path(__file__).with_name("user_dict.txt")
    if _user_dict.exists():
        jieba.load_userdict(str(_user_dict))
    jieba.initialize()
    _JIEBA_READY = True


def _tokenize(text: str) -> List[str]:
    """用 jieba 进行词语级分词，回退字符级保证兼容性。"""
    _ensure_jieba()
    return jieba.lcut(text)


from .parsers import (
    SUPPORTED_SUFFIXES,
    extract_dita_metadata,
    get_parse_track,
    split_into_chunks,
)


_SYNONYMS_PATH = Path(__file__).with_name("synonyms.json")
_WEAK_TERMS = {
    "问题", "情况", "东西", "内容", "信息", "设置", "处理",
    "thing", "issue", "info", "data", "set",
}

_EXACT_TERM_PATTERNS = (
    re.compile(r"\b(?:QE|8D)-\d{4}-\d{4}\b", re.IGNORECASE),
    re.compile(r"\b[A-Z]{2,6}-\d{3,5}\b", re.IGNORECASE),
    re.compile(r"\bV\d+(?:\.\d+){1,2}\b", re.IGNORECASE),
    re.compile(r"\b[A-Z]{2,}(?:-[A-Z0-9]+)+\b", re.IGNORECASE),
)
_RE_MODEL_IDENTIFIER = re.compile(r"\b[A-Z]{2,6}-\d{3,5}\b", re.IGNORECASE)

# 提取中文技术词组（用于源路径加权，区分同类文档如不同模块 FMEA）
_RE_CHINESE_TECH_PHRASE = re.compile(r'[\u4e00-\u9fff]{3,8}')


def _load_synonyms(path: Path = _SYNONYMS_PATH) -> Dict[str, List[str]]:
    """加载同义词词典，文件不存在或格式错误时返回空字典。"""
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
        if not isinstance(k, str) or not isinstance(v, list):
            continue
        values = [str(x).strip() for x in v if str(x).strip()]
        if values:
            out[k.strip()] = values
    return out


def _normalize_synonym_groups(path: Path = _SYNONYMS_PATH) -> List[List[Dict[str, Any]]]:
    """将同义词配置标准化为带权重的词组结构。"""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []

    groups: List[List[Dict[str, Any]]] = []

    def _default_weight(term: str, is_canonical: bool) -> float:
        if is_canonical:
            return 1.0
        if term.lower() in _WEAK_TERMS:
            return 0.2
        return 0.8

    for canonical, payload in data.items():
        if not isinstance(canonical, str) or not canonical.strip():
            continue
        group: List[Dict[str, Any]] = [{
            "text": canonical.strip(),
            "weight": _default_weight(canonical.strip(), True),
        }]

        if isinstance(payload, list):
            for x in payload:
                text = str(x).strip()
                if not text:
                    continue
                group.append({"text": text, "weight": _default_weight(text, False)})
        elif isinstance(payload, dict):
            canonical_weight = payload.get("weight")
            if isinstance(canonical_weight, (int, float)):
                group[0]["weight"] = float(canonical_weight)

            terms = payload.get("terms", [])
            if isinstance(terms, list):
                for item in terms:
                    if isinstance(item, str):
                        text = item.strip()
                        if not text:
                            continue
                        group.append({"text": text, "weight": _default_weight(text, False)})
                    elif isinstance(item, dict):
                        text = str(item.get("text", "")).strip()
                        if not text:
                            continue
                        weight = item.get("weight")
                        if not isinstance(weight, (int, float)):
                            weight = _default_weight(text, False)
                        group.append({"text": text, "weight": float(weight)})

        # 同组内按词权重+长度排序，优先行业术语
        uniq: Dict[str, Dict[str, Any]] = {}
        for t in group:
            text = t["text"]
            if text not in uniq or t["weight"] > uniq[text]["weight"]:
                uniq[text] = t
        normalized = sorted(
            uniq.values(),
            key=lambda x: (float(x.get("weight", 0.0)), len(str(x.get("text", "")))),
            reverse=True,
        )
        if len(normalized) >= 2:
            groups.append(normalized)
    return groups


def _extract_exact_terms(query: str) -> List[str]:
    """从问题中提取需要精确匹配加权的标识符。"""
    found: List[str] = []
    seen: set[str] = set()
    for pattern in _EXACT_TERM_PATTERNS:
        for match in pattern.finditer(query):
            term = match.group(0).strip()
            norm = term.upper()
            if len(term) < 3 or norm in seen:
                continue
            seen.add(norm)
            found.append(term)
    # 中文技术词组：提取查询中连续中文字符的 4-6 字滑窗，
    # 用于源路径加权（区分如"高压测试链路"vs"视觉模块"等同类文档）
    idx = 0
    while idx < len(query):
        ch = query[idx]
        if '\u4e00' <= ch <= '\u9fff':
            # 找到连续中文段落终点
            end = idx
            while end < len(query) and '\u4e00' <= query[end] <= '\u9fff':
                end += 1
            run = query[idx:end]
            # 提取 4-6 字滑窗
            for length in range(4, min(7, len(run) + 1)):
                for start in range(len(run) - length + 1):
                    phrase = run[start:start + length]
                    if phrase not in seen:
                        seen.add(phrase)
                        found.append(phrase)
            idx = end
        else:
            idx += 1
    return found


def _contains_exact_term(text: str, term: str) -> bool:
    if not text or not term:
        return False
    pattern = re.compile(rf"(?<![A-Z0-9]){re.escape(term)}(?![A-Z0-9])", re.IGNORECASE)
    return bool(pattern.search(text))


def _extract_model_terms(text: str) -> List[str]:
    return [m.group(0).upper() for m in _RE_MODEL_IDENTIFIER.finditer(text or "")]


def _model_match_adjustment(query: str, evidence: Dict[str, Any]) -> float:
    query_models = _extract_model_terms(query)
    if not query_models:
        return 0.0

    evidence_text = " ".join(
        str(evidence.get(field, "")) for field in ("source", "model_type", "content")
    )
    evidence_models = _extract_model_terms(evidence_text)
    if not evidence_models:
        return 0.0

    query_set = set(query_models)
    evidence_set = set(evidence_models)
    if query_set & evidence_set:
        return 1.0

    query_prefixes = {item.split("-")[0] for item in query_set}
    evidence_prefixes = {item.split("-")[0] for item in evidence_set}
    if query_prefixes & evidence_prefixes:
        return -1.6
    return 0.0


def _doc_type_query_adjustment(query: str, evidence: Dict[str, Any]) -> float:
    doc_type = str(evidence.get("doc_type", "")).lower()
    if not doc_type:
        return 0.0

    bonus = 0.0
    if any(sig in query for sig in ("判定标准", "测试规范", "定义与规范", "测试项")):
        if doc_type == "test_spec":
            bonus += 1.2
        elif doc_type == "test_case":
            bonus -= 0.6
    if any(sig in query for sig in ("测试用例", "前置条件", "预期输出", "回归测试")):
        if doc_type == "test_case":
            bonus += 1.2
        elif doc_type == "test_spec":
            bonus -= 0.4
    return bonus


def _exact_match_boost(evidence: Dict[str, Any], exact_terms: List[str]) -> float:
    """根据 source / metadata / content 中的精确标识符命中情况返回加权分。"""
    if not exact_terms:
        return 0.0

    source = evidence.get("source", "")
    doc_id = evidence.get("doc_id", "")
    version = evidence.get("version", "")
    model_type = evidence.get("model_type", "")
    content = evidence.get("content", "")

    boost = 0.0
    for term in exact_terms:
        is_chinese = any('\u4e00' <= c <= '\u9fff' for c in term)
        if is_chinese:
            # 中文词组：仅模块在源路径中的命中情况进行轻度加权（避免拟人化 content 匹配）
            if term in source:
                boost += 0.8
        else:
            if _contains_exact_term(source, term):
                boost += 1.2
            if _contains_exact_term(doc_id, term):
                boost += 1.0
            if _contains_exact_term(version, term):
                boost += 1.0
            if _contains_exact_term(model_type, term):
                boost += 1.0
            if _contains_exact_term(content, term):
                boost += 0.6
            # 对 doc_id 格式 PREFIX-YEAR-NUM，额外尝试匹配文件名中的 YEAR-NUM 后缀
            # 解决如 “8D报告-2025-0091.md” vs 查询中 “8D-2025-0091” 此类命名不一致的问题
            parts = term.split("-")
            if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
                year_num = f"{parts[1]}-{parts[2]}"
                if _contains_exact_term(source, year_num):
                    boost += 0.9

    return min(boost, 3.0)


def _source_overlap_boost(query: str, evidence: Dict[str, Any]) -> float:
    """根据 query 与 source 路径的词面重合度做轻量加权。"""
    source = evidence.get("source", "")
    if not query or not source:
        return 0.0
    tokens = []
    seen: set[str] = set()
    for token in _tokenize(query):
        token = token.strip()
        if len(token) < 2 or token in _WEAK_TERMS:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        tokens.append(token)
    source_lower = source.lower()
    hits = sum(1 for token in tokens if token.lower() in source_lower)
    return min(hits * 0.12, 0.84)


def _norm_query(text: str) -> str:
    return " ".join(text.strip().lower().split())


def expand_query(
    query: str,
    llm_rewrites: List[str] | None = None,
    max_variants: int = 5,
    synonyms_path: Path = _SYNONYMS_PATH,
    min_term_weight: float = 0.65,
    per_group_max_replacements: int = 2,
) -> List[str]:
    """查询扩展：词典扩展 + LLM 改写，合并去重后返回。"""
    base = query.strip()
    if not base:
        return []

    variants: List[str] = [base]
    groups = _normalize_synonym_groups(synonyms_path)

    # 词典扩展：命中词组后，优先使用高权重术语替换；弱词不参与替换
    for group in groups:
        hit = next((t for t in group if t["text"] and t["text"] in base), None)
        if not hit:
            continue
        hit_text = str(hit["text"])
        hit_weight = float(hit.get("weight", 0.0))
        if hit_weight < min_term_weight:
            continue

        replaced = 0
        for term in group:
            target_text = str(term.get("text", "")).strip()
            target_weight = float(term.get("weight", 0.0))
            if not target_text or target_text == hit_text:
                continue
            if target_weight < min_term_weight:
                continue
            if target_weight + 1e-9 < hit_weight:
                continue
            candidate = base.replace(hit_text, target_text, 1).strip()
            if candidate:
                variants.append(candidate)
                replaced += 1
            if replaced >= per_group_max_replacements:
                break

    # LLM 改写补充
    for rw in llm_rewrites or []:
        rw = str(rw).strip()
        if rw:
            variants.append(rw)

    deduped: List[str] = []
    seen: set[str] = set()
    for q in variants:
        key = _norm_query(q)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(q)
        if len(deduped) >= max_variants:
            break
    return deduped


# ── 元数据加载 ─────────────────────────────────────────────────────────────────

def load_doc_metadata(doc_file: Path) -> Dict[str, str]:
    """读取同目录下同名 .meta.json 文件，提取元数据字段。

    meta.json 示例::

        {
            "product_line": "晶圆测试设备",
            "version": "V2.1",
            "department": "软件",
            "confidentiality": "部门内",
            "doc_type": "测试方案",
            "model_type": "WPS-3000",
            "module": "视觉/传感",
            "status": "已发布",
            "owner": "张三",
            "effective_date": "2025-01-01",
            "doc_id": "SW-TEST-2025-001",
            "related_software_version": "3.5.2"
        }
    """
    meta_file = Path(str(doc_file) + ".meta.json")
    if meta_file.exists():
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            # 兼容旧元数据字段名：classification -> confidentiality
            if "confidentiality" not in data and "classification" in data:
                data["confidentiality"] = data["classification"]
            result = {field: str(data.get(field, DEFAULT_META[field])) for field in META_FIELDS}
            # 版本号归一化：确保以 V 开头（如 2.1 → V2.1）
            if result.get("version") and not result["version"].upper().startswith("V"):
                result["version"] = "V" + result["version"]
            return result
        except Exception:
            pass
    return dict(DEFAULT_META)


# ── Chunk 记录构建 ─────────────────────────────────────────────────────────────

def make_chunk_record(
    doc_file: Path,
    docs_dir: Path,
    chunk_index: int,
    chunk: str,
    extra_metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    relative_path = doc_file.relative_to(docs_dir).as_posix()
    chunk_id      = hashlib.sha256(f"{relative_path}:{chunk_index}".encode()).hexdigest()
    content_hash  = hashlib.sha256(chunk.encode()).hexdigest()
    # 自动从文件后缀推断解析轨道和格式
    auto_track  = get_parse_track(doc_file)
    auto_format = doc_file.suffix.lower().lstrip(".")
    metadata: Dict[str, Any] = {
        "source":        relative_path,
        "chunk_index":   chunk_index,
        "content_hash":  content_hash,
        **DEFAULT_META,
        "parse_track":   auto_track,
        "file_format":   auto_format,
    }
    if extra_metadata:
        allowed_keys = (*META_FIELDS, "source", "chunk_index", "content_hash")
        metadata.update({k: v for k, v in extra_metadata.items() if k in allowed_keys})
    # parse_track / file_format 始终由文件后缀自动推导，不允许被 meta.json 覆盖
    metadata["parse_track"] = auto_track
    metadata["file_format"] = auto_format
    return {"id": chunk_id, "document": chunk, "metadata": metadata}


# ── 目录批量加载 ───────────────────────────────────────────────────────────────

def load_chunks_from_directory(docs_dir: Path) -> List[Dict[str, Any]]:
    """扫描 docs_dir 中所有支持格式的文档，返回 chunk 记录列表"""
    chunks: List[Dict[str, Any]] = []
    for doc_file in sorted(docs_dir.rglob("*")):
        if doc_file.is_dir() or doc_file.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        doc_metadata = load_doc_metadata(doc_file)
        # DITA/XML 额外从文件内部抽取元数据（meta.json 优先）
        if doc_file.suffix.lower() in (".dita", ".ditamap", ".xml"):
            for k, v in extract_dita_metadata(doc_file).items():
                if not doc_metadata.get(k):
                    doc_metadata[k] = v
        for chunk_index, chunk in enumerate(split_into_chunks(doc_file)):
            chunks.append(make_chunk_record(doc_file, docs_dir, chunk_index, chunk, doc_metadata))
    return chunks


def _compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_docs_state(docs_dir: Path) -> Dict[str, Dict[str, Any]]:
    """扫描 docs_dir 并返回文档状态清单，用于增量构建判定。"""
    state: Dict[str, Dict[str, Any]] = {}
    for doc_file in sorted(docs_dir.rglob("*")):
        if doc_file.is_dir() or doc_file.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        rel = doc_file.relative_to(docs_dir).as_posix()
        meta_file = Path(str(doc_file) + ".meta.json")
        state[rel] = {
            "file_hash": _compute_sha256(doc_file),
            "meta_hash": _compute_sha256(meta_file) if meta_file.exists() else "",
            "mtime_ns": doc_file.stat().st_mtime_ns,
        }
    return state


def load_manifest(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_manifest(path: Path, manifest: Dict[str, Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def diff_docs_state(
    old_state: Dict[str, Dict[str, Any]],
    new_state: Dict[str, Dict[str, Any]],
) -> Tuple[List[str], List[str], List[str]]:
    old_keys = set(old_state)
    new_keys = set(new_state)
    added = sorted(new_keys - old_keys)
    deleted = sorted(old_keys - new_keys)
    changed = sorted(
        rel for rel in (old_keys & new_keys)
        if old_state.get(rel) != new_state.get(rel)
    )
    return added, changed, deleted


def load_chunks_for_files(docs_dir: Path, relative_paths: List[str]) -> List[Dict[str, Any]]:
    """仅为指定文件列表解析和构建 chunk 记录。"""
    chunks: List[Dict[str, Any]] = []
    for rel in sorted(relative_paths):
        doc_file = docs_dir / rel
        if not doc_file.exists() or doc_file.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        doc_metadata = load_doc_metadata(doc_file)
        if doc_file.suffix.lower() in (".dita", ".ditamap", ".xml"):
            for k, v in extract_dita_metadata(doc_file).items():
                if not doc_metadata.get(k):
                    doc_metadata[k] = v
        for chunk_index, chunk in enumerate(split_into_chunks(doc_file)):
            chunks.append(make_chunk_record(doc_file, docs_dir, chunk_index, chunk, doc_metadata))
    return chunks


# ── Embedding ─────────────────────────────────────────────────────────────────

def embed_chunks_batch(model: SentenceTransformer, texts: List[str]) -> List[List[float]]:
    embeddings = model.encode(
        texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False
    )
    return embeddings.tolist()


# ── 向量库写入 ─────────────────────────────────────────────────────────────────

def build_collection(
    chunks: List[Dict[str, Any]],
    embedding_model: SentenceTransformer,
    db_dir: Path,
    collection_name: str,
):
    """增量同步 chunks 到 ChromaDB（新增/更新/跳过）"""
    client     = chromadb.PersistentClient(path=str(db_dir))
    collection = client.get_or_create_collection(name=collection_name)

    existing = collection.get(include=["metadatas"])
    existing_hashes = {
        cid: (meta or {}).get("content_hash")
        for cid, meta in zip(existing["ids"], existing["metadatas"])
    }

    to_add, to_update, skipped = [], [], 0
    for chunk in chunks:
        cid          = chunk["id"]
        content_hash = chunk["metadata"]["content_hash"]
        if cid not in existing_hashes:
            to_add.append(chunk)
        elif existing_hashes[cid] != content_hash:
            to_update.append(chunk)
        else:
            skipped += 1

    if to_add:
        add_embeddings = embed_chunks_batch(embedding_model, [c["document"] for c in to_add])
        collection.add(
            ids       =[c["id"]       for c in to_add],
            documents =[c["document"] for c in to_add],
            embeddings=add_embeddings,
            metadatas =[c["metadata"] for c in to_add],
        )

    if to_update:
        upd_embeddings = embed_chunks_batch(embedding_model, [c["document"] for c in to_update])
        collection.update(
            ids       =[c["id"]       for c in to_update],
            documents =[c["document"] for c in to_update],
            embeddings=upd_embeddings,
            metadatas =[c["metadata"] for c in to_update],
        )

    print(
        f"Collection sync complete: total={len(chunks)}, "
        f"added={len(to_add)}, updated={len(to_update)}, skipped={skipped}"
    )
    return collection


def _collect_source_to_ids(collection) -> Dict[str, List[str]]:
    existing = collection.get(include=["metadatas"])
    source_to_ids: Dict[str, List[str]] = {}
    for cid, meta in zip(existing["ids"], existing["metadatas"]):
        source = (meta or {}).get("source", "")
        if not source:
            continue
        source_to_ids.setdefault(source, []).append(cid)
    return source_to_ids


def delete_chunks_by_sources(collection, sources: List[str]) -> int:
    if not sources:
        return 0
    source_to_ids = _collect_source_to_ids(collection)
    ids_to_delete: List[str] = []
    for source in sources:
        ids_to_delete.extend(source_to_ids.get(source, []))
    if ids_to_delete:
        collection.delete(ids=ids_to_delete)
    return len(ids_to_delete)


def sync_collection_incremental(
    docs_dir: Path,
    embedding_model: SentenceTransformer,
    db_dir: Path,
    collection_name: str,
    manifest_path: Path | None = None,
):
    """最小增量构建：

    - 新增文件：解析并新增 chunk
    - 变更文件：先删旧 chunk，再重建该文件所有 chunk
    - 删除文件：删除 collection 中对应旧 chunk
    """
    client = chromadb.PersistentClient(path=str(db_dir))
    collection = client.get_or_create_collection(name=collection_name)

    if manifest_path is None:
        manifest_path = db_dir / "build_manifest.json"

    current_state = scan_docs_state(docs_dir)
    previous_state = load_manifest(manifest_path)
    added, changed, deleted = diff_docs_state(previous_state, current_state)

    print(
        "Incremental diff: "
        f"added={len(added)}, changed={len(changed)}, deleted={len(deleted)}"
    )

    deleted_count = delete_chunks_by_sources(collection, changed + deleted)

    to_rebuild = added + changed
    chunks = load_chunks_for_files(docs_dir, to_rebuild)
    added_count = len(chunks)
    if chunks:
        embeddings = embed_chunks_batch(embedding_model, [c["document"] for c in chunks])
        collection.add(
            ids=[c["id"] for c in chunks],
            documents=[c["document"] for c in chunks],
            embeddings=embeddings,
            metadatas=[c["metadata"] for c in chunks],
        )

    save_manifest(manifest_path, current_state)

    print(
        "Incremental sync complete: "
        f"rebuilt_files={len(to_rebuild)}, deleted_files={len(deleted)}, "
        f"deleted_chunks={deleted_count}, added_chunks={added_count}, "
        f"unchanged_files={max(0, len(current_state) - len(to_rebuild))}"
    )
    return collection


# ── 检索 ───────────────────────────────────────────────────────────────────────

def retrieve(
    collection,
    embedding_model: SentenceTransformer,
    query: str,
    top_k: int,
    where: Dict[str, Any] | None = None,
) -> List[str]:
    """返回纯文本 chunk 列表（CLI / 简单调用用）"""
    query_embedding = embed_chunks_batch(embedding_model, [query])[0]
    kwargs: Dict[str, Any] = {"query_embeddings": [query_embedding], "n_results": top_k}
    if where:
        kwargs["where"] = where
    results = collection.query(**kwargs)
    return results["documents"][0]


def retrieve_with_metadata(
    collection,
    embedding_model: SentenceTransformer,
    query: str,
    top_k: int,
    where: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    """返回带元数据的证据列表，用于前端展示证据卡片。

    每条记录包含:
    ``content``, ``source``, ``version``, ``department``, ``doc_type``,
    ``owner``, ``effective_date``, ``doc_id``, ``confidentiality``,
    ``product_line``, ``model_type``, ``module``, ``score``
    """
    query_embedding = embed_chunks_batch(embedding_model, [query])[0]
    kwargs: Dict[str, Any] = {
        "query_embeddings": [query_embedding],
        "n_results":        top_k,
        "include":          ["documents", "metadatas", "distances"],
    }
    if where:
        kwargs["where"] = where
    results = collection.query(**kwargs)

    evidence = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        m = meta or {}
        evidence.append({
            "content":          doc,
            "source":           m.get("source", ""),
            "version":          m.get("version", ""),
            "department":       m.get("department", ""),
            "doc_type":         m.get("doc_type", ""),
            "owner":            m.get("owner", ""),
            "effective_date":   m.get("effective_date", ""),
            "doc_id":           m.get("doc_id", ""),
            "confidentiality":  m.get("confidentiality", ""),
            "product_line":     m.get("product_line", ""),
            "model_type":       m.get("model_type", ""),
            "module":           m.get("module", ""),
            "score":            round(1 - float(dist), 4),
        })
    return evidence


def retrieve_hybrid(
    collection,
    embedding_model: SentenceTransformer,
    query: str,
    top_k: int,
    where: Dict[str, Any] | None = None,
    rrf_k: int = 60,
) -> List[Dict[str, Any]]:
    """向量检索 + BM25 全文检索，用 RRF 融合后返回带元数据的证据列表。

    RRF score = 1/(rrf_k + rank_vector) + 1/(rrf_k + rank_bm25)
    """
    # ── 1. 向量检索，扩大候选池，避免正确文档在首轮就被截断 ───────────────────
    fetch_k = max(top_k * 15, 50)          # 拉大候选池，让 BM25 有更多文档可重排
    query_embedding = embed_chunks_batch(embedding_model, [query])[0]
    vec_kwargs: Dict[str, Any] = {
        "query_embeddings": [query_embedding],
        "n_results": fetch_k,
        "include": ["documents", "metadatas", "distances"],
    }
    if where:
        vec_kwargs["where"] = where
    vec_results = collection.query(**vec_kwargs)

    # 构建 id→evidence 映射（以 source+chunk_index 为唯一标识）
    evidence_map: Dict[str, Dict[str, Any]] = {}
    vec_order: List[str] = []
    for doc, meta, dist in zip(
        vec_results["documents"][0],
        vec_results["metadatas"][0],
        vec_results["distances"][0],
    ):
        m = meta or {}
        key = f"{m.get('source', '')}:{m.get('chunk_index', 0)}"
        if key not in evidence_map:
            evidence_map[key] = {
                "content":         doc,
                "source":          m.get("source", ""),
                "version":         m.get("version", ""),
                "department":      m.get("department", ""),
                "doc_type":        m.get("doc_type", ""),
                "owner":           m.get("owner", ""),
                "effective_date":  m.get("effective_date", ""),
                "doc_id":          m.get("doc_id", ""),
                "confidentiality": m.get("confidentiality", ""),
                "product_line":    m.get("product_line", ""),
                "model_type":      m.get("model_type", ""),
                "module":          m.get("module", ""),
                "vec_score":       round(1 - float(dist), 4),
            }
            vec_order.append(key)

    # ── 2. BM25 全文检索（在向量候选池上） ────────────────────────────────────
    if not evidence_map:
        return []

    keys = list(evidence_map.keys())
    corpus = [evidence_map[k]["content"] for k in keys]
    tokenized_corpus = [_tokenize(doc) for doc in corpus]      # jieba 词语级分词
    bm25 = BM25Okapi(tokenized_corpus)
    tokenized_query = _tokenize(query)
    bm25_scores = bm25.get_scores(tokenized_query)
    bm25_order = [keys[i] for i in sorted(range(len(keys)), key=lambda i: bm25_scores[i], reverse=True)]

    # ── 3. RRF 融合 + 精确标识符加权 ──────────────────────────────────────────
    exact_terms = _extract_exact_terms(query)
    rrf_scores: Dict[str, float] = {k: 0.0 for k in keys}
    for rank, key in enumerate(vec_order):
        rrf_scores[key] += 1.0 / (rrf_k + rank + 1)
    for rank, key in enumerate(bm25_order):
        rrf_scores[key] += 1.0 / (rrf_k + rank + 1)
    if exact_terms:
        for key in keys:
            rrf_scores[key] += _exact_match_boost(evidence_map[key], exact_terms)
    for key in keys:
        rrf_scores[key] += _model_match_adjustment(query, evidence_map[key])
        rrf_scores[key] += _doc_type_query_adjustment(query, evidence_map[key])
        rrf_scores[key] += _source_overlap_boost(query, evidence_map[key])

    ranked_keys = sorted(rrf_scores.keys(), key=lambda k: rrf_scores[k], reverse=True)

    # ── 4. 返回 chunk 级候选，按 source 去重延后到 rerank 之后 ─────────────────
    result = []
    for key in ranked_keys[:fetch_k]:
        ev = evidence_map[key].copy()
        ev["score"] = round(rrf_scores[key], 6)
        result.append(ev)

    return result


def retrieve_hybrid_multi(
    collection,
    embedding_model: SentenceTransformer,
    queries: List[str],
    top_k: int,
    where: Dict[str, Any] | None = None,
    rrf_k: int = 60,
    base_query_weight: float = 1.8,
    expanded_query_weight: float = 1.0,
    base_route_window: int = 30,
    expanded_route_window: int = 8,
) -> List[Dict[str, Any]]:
    """多查询混合检索：每个查询独立检索，再进行跨查询 RRF 融合。

    - 主查询路加权，提升主问题的稳定性
    - 扩展查询路限窗，降低噪声召回
    """
    cleaned = [q.strip() for q in queries if q and q.strip()]
    if not cleaned:
        return []

    deduped_queries: List[str] = []
    seen_q: set[str] = set()
    for q in cleaned:
        key = _norm_query(q)
        if key in seen_q:
            continue
        seen_q.add(key)
        deduped_queries.append(q)

    merged: Dict[str, Dict[str, Any]] = {}
    fused_scores: Dict[str, float] = {}

    for idx, q in enumerate(deduped_queries):
        partial = retrieve_hybrid(collection, embedding_model, q, top_k, where, rrf_k)
        route_weight = base_query_weight if idx == 0 else expanded_query_weight
        route_window = base_route_window if idx == 0 else expanded_route_window
        for rank, ev in enumerate(partial[:max(1, route_window)]):
            key = f"{ev.get('source', '')}:{ev.get('doc_id', '')}:{ev.get('content', '')[:64]}"
            if key not in merged:
                merged[key] = ev
                fused_scores[key] = 0.0
            fused_scores[key] += route_weight * (1.0 / (rrf_k + rank + 1))

    ranked_keys = sorted(fused_scores, key=lambda k: fused_scores[k], reverse=True)
    fetch_k = max(top_k * 15, 50)          # 与 retrieve_hybrid 保持一致
    result: List[Dict[str, Any]] = []
    for key in ranked_keys[:fetch_k]:
        item = merged[key].copy()
        item["score"] = round(fused_scores[key], 6)
        result.append(item)
    return result
