"""config.py — 全局常量与元数据默认值"""
from __future__ import annotations

from typing import Dict

# ── CLI / 默认路径 ─────────────────────────────────────────────────────────────
DEFAULT_QUERY           = "常见故障如何排查和处理"
DEFAULT_DOCS_DIR        = "docs"
DEFAULT_DB_DIR          = "chroma_db"
DEFAULT_COLLECTION_NAME = "default"

# ── 模型名称 ───────────────────────────────────────────────────────────────────
EMBEDDING_MODEL_NAME = "BAAI/bge-m3"
RERANK_MODEL_NAME    = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
GENERATION_MODEL_NAME = "glm-4-flash"

# ── 外部服务 ───────────────────────────────────────────────────────────────────
ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"

# ── 5 个解析轨道（对应需求文档 2.2 节） ────────────────────────────────────────
PARSE_TRACK_STRUCTURED_DOC = "structured_doc"   # Track 1: .dita/.xml/.md/.txt
PARSE_TRACK_RICH_TEXT      = "rich_text"        # Track 2: .pdf/.docx/.pptx
PARSE_TRACK_TABLE          = "table"            # Track 3: .xlsx/.xls/.csv
PARSE_TRACK_LOG            = "log"              # Track 4: .log/.json
PARSE_TRACK_RESTRICTED     = "restricted"       # Track 5: 邮件/聊天记录，需人工审核

# ── 文档元数据字段定义 ──────────────────────────────────────────────────────────
META_FIELDS = (
    "product_line",
    "version",
    "department",
    "confidentiality",
    "doc_type",
    "model_type",
    "module",
    "status",
    "owner",
    "effective_date",
    "doc_id",
    "related_software_version",
    "parse_track",   # 解析轨道，用于元数据过滤
    "file_format",   # 文件后缀，用于元数据过滤
)

DEFAULT_META: Dict[str, str] = {
    "product_line":             "",
    "version":                  "",
    "department":               "",
    "confidentiality":          "公开",
    "doc_type":                 "",        # 规格书/测试方案/故障案例/FAQ/作业指导书 等
    "model_type":               "",        # 机型
    "module":                   "",        # 子系统/模块
    "status":                   "已发布",  # 草稿/评审中/已发布/已冻结/已废止
    "owner":                    "",        # 责任人
    "effective_date":           "",        # 生效日期 YYYY-MM-DD
    "doc_id":                   "",        # 文档编号
    "related_software_version": "",        # 关联软件版本
    "parse_track":              "",        # structured_doc/rich_text/table/log
    "file_format":              "",        # 文件后缀（不含点）
}
