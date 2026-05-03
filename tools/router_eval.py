"""router_eval.py — 检索路由与执行计划验收脚本

运行：
    python tools/router_eval.py

验收目标：
  - 路由准确率 >= 85%
  - kb 路由不应误伤率 = 0
  - doc_id 查询自动追加 doc_check 工具
  - 含编号查询 retry_strategy = exact_match_boost
  - 含版本+明确意图查询 retry_strategy = tighten_filter
  - web/sql/manual 路由使用正确工具名
  - 信号优先级: manual > web > sql > kb
  - suggested_filters 正确透传到 ExecutionPlan.filters
  - 四种重试策略全覆盖（exact_match_boost/tighten_filter/expand_query/relax_filter）
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import dataclass, field
from core.query_understanding import QueryEntities, QueryPlan
from core.router import RouteType, build_execution_plan

@dataclass
class RouterCase:
    question:           str
    expected_route:     RouteType
    expected_tools:     list[str] | None = None
    expected_strategy:  str | None = None
    expected_filters:   dict | None = None   # 验证 filters 透传
    entities:           QueryEntities = field(default_factory=QueryEntities)
    intent:             str = "general"
    is_complex:         bool = False
    suggested_filters:  dict = field(default_factory=dict)  # 模拟 QueryPlan.suggested_filters
    note:               str = ""

CASES: list[RouterCase] = [
    # ── A. web 路由：工具名 + 实时信号 ───────────────────────────────────────
    RouterCase("今天上海的天气怎么样",
               "web",
               expected_tools=["web_search"],
               note="实时天气，工具名验证"),
    RouterCase("现在几点了",
               "web",
               expected_tools=["web_search"],
               note="实时时间"),
    RouterCase("最新行情怎么样",
               "web",
               expected_tools=["web_search"],
               note="行情信息"),
    RouterCase("今日有什么新闻",
               "web",
               expected_tools=["web_search"],
               note="实时新闻"),

    # ── B. sql 路由：工具名 + 统计信号 ───────────────────────────────────────
    RouterCase("统计所有 V2.1 版本文档数量",
               "sql",
               expected_tools=["sql_query"],
               note="统计查询，工具名验证"),
    RouterCase("列出所有未归档的 8D 记录",
               "sql",
               expected_tools=["sql_query"],
               note="列表查询"),
    RouterCase("各部门文档总数汇总",
               "sql",
               expected_tools=["sql_query"],
               note="汇总统计"),

    # ── C. manual 路由：工具名 + 转接信号 ─────────────────────────────────────
    RouterCase("帮我转接人工客服",
               "manual",
               expected_tools=["manual_handoff"],
               note="人工转接，工具名验证"),
    RouterCase("我要投诉工单",
               "manual",
               expected_tools=["manual_handoff"],
               note="投诉工单"),

    # ── D. kb 路由：基础知识查询 ──────────────────────────────────────────────
    RouterCase("WPS-3000 V2.1 版本说明",
               "kb",
               expected_tools=["kb_search"],
               expected_strategy="tighten_filter",
               entities=QueryEntities(model_types=["WPS-3000"], versions=["V2.1"]),
               intent="release_note",
               note="版本文档，收紧过滤"),
    RouterCase("VIS-1002 故障码原因是什么",
               "kb",
               expected_tools=["kb_search"],
               expected_strategy="exact_match_boost",
               entities=QueryEntities(fault_codes=["VIS-1002"]),
               intent="fault_code",
               note="故障码精确匹配"),
    RouterCase("比亚迪客户有哪些特殊要求",
               "kb",
               expected_tools=["kb_search"],
               entities=QueryEntities(customers=["比亚迪"]),
               intent="customer",
               note="客户档案"),
    RouterCase("FMEA-2024-0012 的风险评估结果",
               "kb",
               expected_tools=["kb_search", "doc_check"],
               expected_strategy="exact_match_boost",
               entities=QueryEntities(doc_ids=["FMEA-2024-0012"]),
               intent="fmea",
               note="FMEA文档+doc_check"),
    RouterCase("WPS-3000 和 WPS-3200 的主要差异",
               "kb",
               expected_tools=["kb_search"],
               expected_strategy="expand_query",
               entities=QueryEntities(model_types=["WPS-3000", "WPS-3200"]),
               is_complex=True,
               note="机型对比，复杂分解"),
    RouterCase("8D-2024-0088 根因分析结论",
               "kb",
               expected_tools=["kb_search", "doc_check"],
               expected_strategy="exact_match_boost",
               entities=QueryEntities(doc_ids=["8D-2024-0088"]),
               intent="8d",
               note="8D报告+doc_check"),
    RouterCase("接口测试流程是怎样的",
               "kb",
               expected_tools=["kb_search"],
               expected_strategy="relax_filter",
               note="流程文档，默认放宽"),

    # ── E. 信号优先级：manual > web > sql > kb ─────────────────────────────
    RouterCase("今天统计所有文档数量",
               "web",
               expected_tools=["web_search"],
               note="优先级: web > sql（今天+统计）"),
    RouterCase("帮我联系人工客服，现在有什么最新消息",
               "manual",
               expected_tools=["manual_handoff"],
               note="优先级: manual > web"),
    RouterCase("今天帮我统计文档并转接人工",
               "manual",
               expected_tools=["manual_handoff"],
               note="优先级: manual > web > sql"),

    # ── F. 四种重试策略全覆盖 ────────────────────────────────────────────────
    # exact_match_boost：fault_code（无 doc_id）
    RouterCase("ALM-0099 报警触发条件",
               "kb",
               expected_tools=["kb_search"],
               expected_strategy="exact_match_boost",
               entities=QueryEntities(fault_codes=["ALM-0099"]),
               intent="fault_code",
               note="策略: exact_match_boost (fault_code)"),

    # tighten_filter：spec + model_type（无 version）
    RouterCase("WPS-3000 规格参数查询",
               "kb",
               expected_tools=["kb_search"],
               expected_strategy="tighten_filter",
               entities=QueryEntities(model_types=["WPS-3000"]),
               intent="spec",
               note="策略: tighten_filter (spec+model)"),

    # tighten_filter：8d + model + version
    RouterCase("WPS-3000 V2.0 的 8D 质量分析",
               "kb",
               expected_tools=["kb_search"],
               expected_strategy="tighten_filter",
               entities=QueryEntities(model_types=["WPS-3000"], versions=["V2.0"]),
               intent="8d",
               note="策略: tighten_filter (8d+version)"),

    # expand_query：customer + is_complex
    RouterCase("比亚迪和长城客户需求对比",
               "kb",
               expected_tools=["kb_search"],
               expected_strategy="expand_query",
               entities=QueryEntities(customers=["比亚迪", "长城"]),
               intent="customer",
               is_complex=True,
               note="策略: expand_query (customer+complex)"),

    # relax_filter：project intent，无实体
    RouterCase("项目交付里程碑计划",
               "kb",
               expected_tools=["kb_search"],
               expected_strategy="relax_filter",
               intent="project",
               note="策略: relax_filter (project, 无实体)"),

    # relax_filter：fault_case intent，无实体
    RouterCase("历史故障维修案例汇总",
               "kb",
               expected_tools=["kb_search"],
               expected_strategy="relax_filter",
               intent="fault_case",
               note="策略: relax_filter (fault_case, 无实体)"),

    # ── G. 多 doc_id + doc_check ────────────────────────────────────────────
    RouterCase("8D-2024-0088 和 FMEA-2024-0012 关联分析",
               "kb",
               expected_tools=["kb_search", "doc_check"],
               expected_strategy="exact_match_boost",
               entities=QueryEntities(doc_ids=["8D-2024-0088", "FMEA-2024-0012"]),
               intent="8d",
               note="多 doc_id → doc_check"),

    # ── H. suggested_filters 透传验证 ────────────────────────────────────────
    RouterCase("WPS-3000 规格书",
               "kb",
               expected_tools=["kb_search"],
               expected_filters={"doc_type": "spec", "model_type": "WPS-3000"},
               entities=QueryEntities(model_types=["WPS-3000"]),
               intent="spec",
               suggested_filters={"doc_type": "spec", "model_type": "WPS-3000"},
               note="filters 透传: doc_type+model_type"),
    RouterCase("VIS-1002 故障码文档",
               "kb",
               expected_tools=["kb_search"],
               expected_strategy="exact_match_boost",
               expected_filters={"doc_type": "fault_code"},
               entities=QueryEntities(fault_codes=["VIS-1002"]),
               intent="fault_code",
               suggested_filters={"doc_type": "fault_code"},
               note="filters 透传: doc_type=fault_code"),
]

# ── 统计期望 kb 案例总数（动态计算）──────────────────────────────────────────
_KB_TOTAL = sum(1 for c in CASES if c.expected_route == "kb")


def _make_plan(case: RouterCase) -> QueryPlan:
    return QueryPlan(
        raw_question=case.question,
        rewritten_query=case.question,
        rewrite_variants=[case.question],
        sub_queries=[],
        intent=case.intent,
        entities=case.entities,
        suggested_filters=case.suggested_filters,   # 透传 suggested_filters
        is_complex=case.is_complex,
    )


def run_eval() -> None:
    total = len(CASES)
    route_pass = 0
    tool_fail: list[str] = []
    strategy_fail: list[str] = []
    filter_fail: list[str] = []
    kb_misrouted = 0

    col_w = 100
    print(f"\n{'='*col_w}")
    print(f"{'ID':<4} {'问题':<34} {'路由':<8} {'工具':<28} {'策略':<22} {'结果':<6} 备注")
    print(f"{'-'*col_w}")

    for i, case in enumerate(CASES, 1):
        plan = _make_plan(case)
        ep = build_execution_plan(plan)

        route_ok = ep.route == case.expected_route
        if route_ok:
            route_pass += 1
        if case.expected_route == "kb" and not route_ok:
            kb_misrouted += 1

        tool_ok = True
        if case.expected_tools is not None:
            tool_ok = ep.tools == case.expected_tools
            if not tool_ok:
                tool_fail.append(f"[{i}] {case.question!r}  期望 {case.expected_tools}  实际 {ep.tools}")

        strategy_ok = True
        if case.expected_strategy is not None:
            strategy_ok = ep.retry_policy.strategy == case.expected_strategy
            if not strategy_ok:
                strategy_fail.append(
                    f"[{i}] {case.question!r}  期望 {case.expected_strategy}  实际 {ep.retry_policy.strategy}"
                )

        filter_ok = True
        if case.expected_filters is not None:
            filter_ok = ep.filters == case.expected_filters
            if not filter_ok:
                filter_fail.append(
                    f"[{i}] {case.question!r}  期望 {case.expected_filters}  实际 {ep.filters}"
                )

        all_ok = route_ok and tool_ok and strategy_ok and filter_ok
        status = "✓" if all_ok else "✗"
        print(
            f"{i:<4} {case.question:<34} {ep.route:<8} "
            f"{str(ep.tools):<28} {ep.retry_policy.strategy:<22} {status:<6} {case.note}"
        )

    print(f"{'='*col_w}")
    accuracy = route_pass / total * 100
    print(f"\n总计: {total} 条  (kb={_KB_TOTAL} / web={sum(1 for c in CASES if c.expected_route=='web')} / sql={sum(1 for c in CASES if c.expected_route=='sql')} / manual={sum(1 for c in CASES if c.expected_route=='manual')})")
    print(f"路由准确率:  {route_pass}/{total} ({accuracy:.0f}%)  {'✓ 达标' if accuracy >= 85 else '✗ 未达标'}")
    print(f"kb 误判数:   {kb_misrouted}/{_KB_TOTAL}  {'✓ 无误伤' if kb_misrouted == 0 else '✗ 存在误伤'}")
    if tool_fail:
        print(f"工具列表错误: {len(tool_fail)} 条")
        for msg in tool_fail:
            print(f"  {msg}")
    else:
        print("工具列表:    ✓ 全部符合预期")
    if strategy_fail:
        print(f"重试策略错误: {len(strategy_fail)} 条")
        for msg in strategy_fail:
            print(f"  {msg}")
    else:
        print("重试策略:    ✓ 全部符合预期")
    if filter_fail:
        print(f"filters 透传错误: {len(filter_fail)} 条")
        for msg in filter_fail:
            print(f"  {msg}")
    else:
        print("filters 透传: ✓ 全部符合预期")

    all_pass = accuracy >= 85 and kb_misrouted == 0 and not tool_fail and not strategy_fail and not filter_fail
    print(f"\n{'✓ 验收通过' if all_pass else '✗ 验收未通过'}")
    sys.exit(0 if all_pass else 1)

if __name__ == "__main__":
    run_eval()
