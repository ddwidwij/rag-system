"""router.py — 检索路由、执行计划与工具注册表

路由优先级（从高到低）：
  manual  → 含人工转接信号
  web     → 含实时/联网信号
  sql     → 含结构化统计信号
  kb      → 默认，走本地知识库 RAG

ExecutionPlan：路由后产出的完整执行计划，包含工具列表和重试策略。
工具编排：首版为 stub，保留接口供后续接真实工具。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, AsyncIterator, Literal

from core.query_understanding import QueryPlan

# ── 类型定义 ──────────────────────────────────────────────────────────────────
RouteType = Literal["kb", "web", "sql", "manual"]

# ── 路由信号词 ─────────────────────────────────────────────────────────────────
_MANUAL_SIGNALS = [
    "人工", "转接", "投诉工单", "人工客服", "联系客服", "客服电话",
]
_WEB_SIGNALS = [
    "今天", "现在", "实时", "最新行情", "股价", "天气", "新闻",
    "最新消息", "当前时间", "现在几点", "今日",
]
_SQL_SIGNALS = [
    "统计", "查表", "列出所有", "多少条", "数量", "总数",
    "求和", "计数", "均值", "最大值", "最小值",
]

# ── 工具注册表 ────────────────────────────────────────────────────────────────
TOOL_REGISTRY: dict[str, str] = {
    "kb_search":      "kb_search",      # 本地 RAG 检索（server.py 主流程）
    "doc_check":      "doc_check",      # 文档精确核验（doc_id 命中场景）
    "web_search":     "web_search",     # 占位：联网搜索工具
    "sql_query":      "sql_query",      # 占位：结构化查询工具
    "manual_handoff": "manual_handoff", # 占位：人工升级工具
}


# ── 数据结构 ──────────────────────────────────────────────────────────────────
@dataclass
class RetryPolicy:
    """重试策略：定义何时重试以及如何调整检索策略。

    trigger_conditions 可选值：
      low_confidence    — rerank 分数低于阈值
      entity_not_found  — 关键实体（编号/版本）未出现在 Top-N 证据中
      type_mismatch     — 证据 doc_type 与 intent 不匹配

    strategy 可选值：
      exact_match_boost — 强化精确匹配权重（适用于含编号的查询）
      tighten_filter    — 收紧 doc_type/version 过滤（意图明确时）
      expand_query      — 展开子查询重试（复杂问题）
      relax_filter      — 放宽 doc_type 约束重试（通用兜底）
    """
    max_retries:         int       = 1
    trigger_conditions:  list[str] = field(default_factory=lambda: ["low_confidence"])
    strategy:            str       = "relax_filter"


@dataclass
class ExecutionPlan:
    """路由决策产出的完整执行计划。

    route         — 路由目标（kb / web / sql / manual）
    tools         — 按顺序调用的工具列表
    filters       — 检索过滤条件（来自 QueryPlan.suggested_filters）
    retry_policy  — 首次检索失败时的重试策略
    """
    route:        RouteType
    tools:        list[str]
    filters:      dict[str, Any]
    retry_policy: RetryPolicy

    def to_dict(self) -> dict:
        return asdict(self)


# ── 路由决策 ──────────────────────────────────────────────────────────────────
def route_query(plan: QueryPlan) -> RouteType:
    """根据 QueryPlan 决定走哪条工具链。

    规则优先级：manual > web > sql > kb（默认）
    仅使用 raw_question 做规则匹配，保证无 LLM 延迟。
    """
    q = plan.raw_question
    if any(kw in q for kw in _MANUAL_SIGNALS):
        return "manual"
    if any(kw in q for kw in _WEB_SIGNALS):
        return "web"
    if any(kw in q for kw in _SQL_SIGNALS):
        return "sql"
    return "kb"


# ── 重试策略推断 ──────────────────────────────────────────────────────────────
def _build_retry_policy(plan: QueryPlan) -> RetryPolicy:
    """按意图和实体类型推断最优重试策略。"""
    intent = plan.intent

    # 含精确编号（doc_id / fault_code）→ 精确匹配增强
    if plan.entities.doc_ids or plan.entities.fault_codes:
        return RetryPolicy(
            max_retries=1,
            trigger_conditions=["entity_not_found", "low_confidence"],
            strategy="exact_match_boost",
        )

    # 含版本/机型 + 意图明确 → 收紧 doc_type 过滤
    if (plan.entities.versions or plan.entities.model_types) and intent in (
        "spec", "release_note", "fmea", "8d"
    ):
        return RetryPolicy(
            max_retries=1,
            trigger_conditions=["low_confidence", "type_mismatch"],
            strategy="tighten_filter",
        )

    # 复杂多维问题 → 子查询展开重试
    if plan.is_complex:
        return RetryPolicy(
            max_retries=1,
            trigger_conditions=["low_confidence"],
            strategy="expand_query",
        )

    # 默认 → 放宽 doc_type 约束重试
    return RetryPolicy(
        max_retries=1,
        trigger_conditions=["low_confidence"],
        strategy="relax_filter",
    )


# ── 执行计划构建（主入口）────────────────────────────────────────────────────
def build_execution_plan(plan: QueryPlan) -> ExecutionPlan:
    """根据 QueryPlan 构建完整的 ExecutionPlan。

    - route：由 route_query() 决定
    - tools：kb 路由时，含 doc_id 的查询追加 doc_check 工具
    - filters：直接复用 plan.suggested_filters
    - retry_policy：由 _build_retry_policy() 按意图/实体推断
    """
    route = route_query(plan)

    # 工具列表
    # 各路由默认工具列表（使用 TOOL_REGISTRY 中注册的正式工具名）
    _ROUTE_DEFAULT_TOOLS: dict[str, list[str]] = {
        "web":    ["web_search"],
        "sql":    ["sql_query"],
        "manual": ["manual_handoff"],
    }

    if route == "kb":
        tools: list[str] = ["kb_search"]
        if plan.entities.doc_ids:          # 有精确文档编号时附加核验工具
            tools.append("doc_check")
    else:
        tools = list(_ROUTE_DEFAULT_TOOLS.get(route, [route]))

    return ExecutionPlan(
        route=route,
        tools=tools,
        filters=plan.suggested_filters or {},
        retry_policy=_build_retry_policy(plan),
    )


# ── Stub 响应文本 ─────────────────────────────────────────────────────────────
_ROUTE_STUBS: dict[str, str] = {
    "web": (
        "该问题需要实时联网数据，当前系统暂不支持联网检索。"
        "请直接通过搜索引擎获取最新信息，或联系管理员开启联网工具。"
    ),
    "sql": (
        "该问题需要查询结构化数据库，当前系统暂不支持 SQL 统计查询。"
        "请联系数据管理员获取相关统计报表。"
    ),
    "manual": (
        "您的问题已被识别为需要人工处理。"
        "请联系相关部门或拨打客服热线获取进一步帮助。"
    ),
}


# ── 非 kb 路由的 SSE stub 响应 ─────────────────────────────────────────────────
async def execute_route_stub(route: RouteType) -> AsyncIterator[str]:
    """为非 kb 路由生成 SSE 格式的 stub 响应。"""
    msg = _ROUTE_STUBS.get(route, "当前路由暂不支持，请换一种方式提问。")
    yield f"data: {json.dumps({'type': 'sources', 'sources': []}, ensure_ascii=False)}\n\n"
    yield f"data: {json.dumps({'type': 'chunk', 'content': msg}, ensure_ascii=False)}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"
