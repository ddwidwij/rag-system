"""query_understanding.py — 智能查询处理：实体抽取、意图识别、查询改写、问题分解"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field, asdict
from typing import Any

# ── 实体抽取规则 ──────────────────────────────────────────────────────────────
# 质量系统文档编号前缀（不应被识别为机型），须在 _RE_MODEL_TYPE 之前处理
_DOC_ID_PREFIXES = frozenset(["QE", "8D", "FMEA", "NCR", "CAR", "WO"])
_MODEL_PREFIXES = frozenset(["WPS", "SCT", "ATE"])
_FAULT_CODE_PREFIXES = frozenset(["VIS", "ERR", "ALM", "FLT", "WARN", "PCK", "MOT", "TMP", "VAC", "PRB", "CAM", "IO", "HV"])
_ALARM_SIGNALS = ("告警", "报警", "报错", "故障", "异常")

_RE_MODEL_TYPE = re.compile(r"\b[A-Z]{2,6}-\d{3,5}\b")
_RE_VERSION    = re.compile(r"\bV\d+(?:\.\d+){1,2}\b", re.IGNORECASE)
_RE_FAULT_CODE = re.compile(r"\b(?:VIS|ERR|ALM|FLT|WARN|PCK|MOT|TMP|VAC|PRB|CAM|IO|HV)-\d{3,5}\b", re.IGNORECASE)
_RE_DOC_ID     = re.compile(r"\b(?:QE|8D|FMEA|NCR|CAR)-\d{4}-\d{4}\b", re.IGNORECASE)
_RE_WORK_ORDER = re.compile(r"\bWO-\d{4}-\d{4}\b", re.IGNORECASE)

_DEPT_KEYWORDS    = ["质量部", "研发部", "工程部", "生产部", "售后部", "项目部", "测试部", "软件部", "硬件部"]
_CUSTOMER_KEYWORDS = [
    "长城", "比亚迪", "宁德", "博世", "大陆", "华为", "联想", "小米",
    "深圳车规芯片", "华东先进封装", "XX半导体", "海外代工厂",
]


def _find_keywords(text: str, keywords: list[str]) -> list[str]:
    return [kw for kw in keywords if kw in text]


# ── 意图识别规则 ──────────────────────────────────────────────────────────────
_INTENT_RULES: dict[str, set[str]] = {
    "fault_code":     {"故障码", "报警", "错误码", "报错", "VIS", "ERR", "ALM", "FLT", "WARN"},
    "release_note":   {"版本", "更新", "发布", "改动", "新增", "修复", "changelog", "变更", "迭代", "版本说明"},
    "spec":           {"规格", "参数", "尺寸", "精度", "量程", "接口", "技术指标", "规格书"},
    "test_spec":      {"测试规范", "测试项", "判定标准", "定义与规范", "执行时机", "覆盖率要求"},
    "test_case":      {"测试用例", "前置条件", "预期输出", "回归测试", "测试步骤"},
    "fault_case":     {"案例", "失效", "维修", "维保", "处理过", "曾经", "历史", "故障案例",
                       "怎么处理", "如何处理", "处理方法", "解决方法", "如何解决", "怎么解决"},
    "work_order":     {"工单", "服务工单", "现场服务", "上门维修", "服务记录"},
    "quality_event":  {"质量事件", "QE-", "纠正措施", "处置措施", "不合格品", "质量报告"},
    "project":        {"项目", "交付", "里程碑", "进度", "排期", "计划"},
    "8d":             {"8D", "8d", "质量问题", "不合格", "整改", "D1", "D2", "D3"},
    "fmea":           {"FMEA", "fmea", "风险", "失效模式", "预防"},
    "customer":       {"客户", "甲方", "用户反馈", "投诉", "售后",
                       "深圳车规芯片", "华东先进封装", "XX半导体", "海外代工厂",
                       "特殊需求", "客户档案", "客户服务"},
}

# 意图 → 知识库 doc_type 映射（使用数据库中实际存储的英文枚举值）
_INTENT_TO_DOC_TYPE: dict[str, str] = {
    "release_note":  "release_note",
    "fault_code":    "fault_code",
    "spec":          "spec",
    "test_spec":     "test_spec",
    "test_case":     "test_case",
    "8d":            "8d",
    "fmea":          "fmea",
    "fault_case":    "fault_case",
    "work_order":    "work_order",
    "quality_event": "quality_event",
    "project":       "project",
    "customer":      "customer",
}

# 复杂问题信号词
_DECOMPOSE_SIGNALS = ["和", "与", "对比", "区别", "分别", "并且", "另外", "同时", "以及", "还有"]


# ── 数据结构 ──────────────────────────────────────────────────────────────────
@dataclass
class QueryEntities:
    model_types:    list[str] = field(default_factory=list)
    versions:       list[str] = field(default_factory=list)
    fault_codes:    list[str] = field(default_factory=list)
    doc_ids:        list[str] = field(default_factory=list)
    work_order_ids: list[str] = field(default_factory=list)
    departments:    list[str] = field(default_factory=list)
    customers:      list[str] = field(default_factory=list)


@dataclass
class QueryPlan:
    raw_question:      str
    rewritten_query:   str           # 主检索串（rewrite_variants[0]）
    rewrite_variants:  list[str]     # P2：主改写 + 备选表述，全部进入 expand_query
    sub_queries:       list[str]     # P3：分解后的子问题
    intent:            str
    entities:          QueryEntities
    suggested_filters: dict[str, Any]
    is_complex:        bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ── 实体抽取 ──────────────────────────────────────────────────────────────────
def extract_entities(question: str) -> QueryEntities:
    fault_codes = list(dict.fromkeys(_RE_FAULT_CODE.findall(question)))
    fault_code_set = {fc.upper() for fc in fault_codes}

    doc_ids = list(dict.fromkeys(_RE_DOC_ID.findall(question)))
    # 质量系统文档编号前缀（QE-YYYY 等）也需排除，避免被误识别为机型
    doc_id_prefixes = {m.split("-")[0].upper() for m in doc_ids}

    # model_type 正则与 fault_code / doc_id 有重叠，全部排除
    raw_model_types = _RE_MODEL_TYPE.findall(question)
    has_alarm_signal = any(signal in question for signal in _ALARM_SIGNALS)
    for code in raw_model_types:
        prefix = code.split("-")[0].upper()
        if prefix in _FAULT_CODE_PREFIXES or (has_alarm_signal and prefix not in _MODEL_PREFIXES):
            fault_code_set.add(code.upper())
            if code not in fault_codes:
                fault_codes.append(code)
    model_types = list(dict.fromkeys(
        m for m in raw_model_types
        if m.upper() not in fault_code_set
        and m.split("-")[0].upper() not in _DOC_ID_PREFIXES
        and m.split("-")[0].upper() not in doc_id_prefixes
    ))

    raw_versions = _RE_VERSION.findall(question)
    versions = list(dict.fromkeys(
        v.upper() if v.upper().startswith("V") else "V" + v for v in raw_versions
    ))
    doc_ids        = list(dict.fromkeys(_RE_DOC_ID.findall(question)))
    work_order_ids = list(dict.fromkeys(_RE_WORK_ORDER.findall(question)))
    departments    = _find_keywords(question, _DEPT_KEYWORDS)
    customers      = _find_keywords(question, _CUSTOMER_KEYWORDS)

    return QueryEntities(
        model_types=model_types,
        versions=versions,
        fault_codes=fault_codes,
        doc_ids=doc_ids,
        work_order_ids=work_order_ids,
        departments=departments,
        customers=customers,
    )


# ── 意图识别 ──────────────────────────────────────────────────────────────────
def classify_intent(question: str) -> str:
    if any(sig in question for sig in ("判定标准", "测试规范", "定义与规范")):
        return "test_spec"
    if "测试项" in question and any(sig in question for sig in ("标准", "规范", "定义", "要求")):
        return "test_spec"
    if "测试用例" in question or "前置条件" in question or "预期输出" in question:
        return "test_case"
    # 工单编号优先：只要匹配到 WO-YYYY-NNNN，直接识别为 work_order
    if _RE_WORK_ORDER.search(question):
        return "work_order"
    # 质量事件编号优先：匹配到 QE-YYYY-NNNN，识别为 quality_event
    if re.search(r"\bQE-\d{4}-\d{4}\b", question, re.IGNORECASE):
        return "quality_event"
    # 8D 报告编号优先：匹配到 8D-YYYY-NNNN，识别为 8d（防止被"纠正措施"等词拉到 quality_event）
    if re.search(r"\b8D-\d{4}-\d{4}\b", question, re.IGNORECASE):
        return "8d"
    # 含告警码 + 参数调整/处理步骤，优先视为工单/现场处置问题
    if _RE_FAULT_CODE.search(question) and any(sig in question for sig in ("怎么调整", "如何调整", "参数怎么", "参数如何", "Over-Drive", "清针")):
        return "work_order"
    # 含告警码且带处理动作，优先视为案例排障
    if _RE_FAULT_CODE.search(question) and any(sig in question for sig in ("怎么处理", "如何处理", "如何解决", "怎么解决", "处理方法", "解决方法")):
        return "fault_case"
    scores: dict[str, int] = {}
    lq = question.lower()
    for intent, keywords in _INTENT_RULES.items():
        # 小写去重后再计分，避免 "8D"/"8d" 等大小写变体对同一字符串重复计分
        deduped_kws = {kw.lower() for kw in keywords}
        score = sum(1 for kw in deduped_kws if kw in lq)
        if score > 0:
            scores[intent] = score
    if not scores:
        return "general"
    return max(scores, key=lambda k: scores[k])


# ── 自动推断元数据过滤条件 ─────────────────────────────────────────────────────
def build_suggested_filters(
    entities: QueryEntities, intent: str, existing_fields: set[str]
) -> dict[str, Any]:
    """根据实体和意图推断 ChromaDB where 过滤条件，跳过 existing_fields 中已有的字段。

    注意：model_type 字段在当前数据库中为空，不自动加入过滤以避免返回空结果。
    version / doc_type / department 有实际数据，可以安全过滤。
    """
    conditions: list[dict] = []

    if "version" not in existing_fields and len(entities.versions) == 1:
        conditions.append({"version": {"$eq": entities.versions[0]}})

    if "doc_type" not in existing_fields and intent in _INTENT_TO_DOC_TYPE:
        conditions.append({"doc_type": {"$eq": _INTENT_TO_DOC_TYPE[intent]}})

    if "department" not in existing_fields and len(entities.departments) == 1:
        conditions.append({"department": {"$eq": entities.departments[0]}})

    if not conditions:
        return {}
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


# ── 复杂问题判断 ──────────────────────────────────────────────────────────────
def should_decompose(question: str, entities: QueryEntities) -> bool:
    has_multiple = len(entities.model_types) > 1 or len(entities.versions) > 1
    has_signal   = any(s in question for s in _DECOMPOSE_SIGNALS)
    return has_multiple or (has_signal and len(question) > 25)


# ── 口语化/模糊问法信号词 ─────────────────────────────────────────────────────
_VAGUE_PATTERNS = frozenset([
    "怎么了", "什么情况", "咋回事", "有没有", "怎么办", "怎么弄",
    "怎么搞", "啥意思", "啥", "咋", "为啥", "干嘛", "干什么",
    "有什么用", "能干啥", "可以做什么", "是什么", "是啥",
])


# ── LLM 调用：查询改写（P2）────────────────────────────────────────────────────
async def _llm_rewrite(llm_client: Any, question: str, entities: QueryEntities, intent: str) -> list[str]:
    """
    P2 结构化查询改写：输出 2 条检索语句。
    - variants[0]：精确检索式（保留编号+版本+文档类型关键词，面向 BM25）
    - variants[1]：语义扩展式（描述问题现象/目标，面向向量检索）
    对口语化/模糊问题额外注入规范化指令，通用意图补充领域聚焦词。
    失败时返回 [原问题]。
    """
    entity_hint_parts: list[str] = []
    if entities.model_types:
        entity_hint_parts.append(f"机型: {', '.join(entities.model_types)}")
    if entities.versions:
        entity_hint_parts.append(f"版本: {', '.join(entities.versions)}")
    if entities.fault_codes:
        entity_hint_parts.append(f"故障码: {', '.join(entities.fault_codes)}")
    if entities.doc_ids:
        entity_hint_parts.append(f"文档编号: {', '.join(entities.doc_ids)}")
    entity_hint = "；".join(entity_hint_parts) if entity_hint_parts else "无"

    intent_focus = {
        "fault_code":   "故障码定义、现象、原因、处理措施",
        "release_note": "版本号、新增功能列表、修复项、变更说明",
        "spec":         "技术规格参数、量程、精度、接口定义",
        "test_spec":    "测试项名称、判定标准、执行规范",
        "test_case":    "测试用例名称、前置条件、预期输出",
        "8d":           "问题描述、根因分析、纠正措施、预防措施",
        "fmea":         "失效模式、风险等级 RPN、预防与探测措施",
        "fault_case":   "失效现象、处理经过、结论与建议",
        "work_order":   "工单编号、故障描述、处理结果、服务记录",
        "project":      "项目里程碑、交付物、进度状态",
        "customer":     "客户特殊需求、认证要求、合同条款",
        "general":      "设备操作规范、维护保养要求、工艺参数、常见问题处理步骤",
    }.get(intent, "关键概念、定义、操作步骤")

    # 口语化/模糊问题检测
    has_no_entities = not any([
        entities.model_types, entities.fault_codes,
        entities.doc_ids, entities.versions,
    ])
    is_vague = (
        any(p in question for p in _VAGUE_PATTERNS)
        or (intent == "general" and has_no_entities and len(question) < 30)
    )

    # 按意图生成 few-shot 示例，避免模型把指令词汇直接输出到检索式
    _EXAMPLES: dict[str, tuple[str, str, str]] = {
        "fault_code":   ("VIS-1002 是什么",
                         "VIS-1002 故障码定义 触发原因 处理措施",
                         "视觉定位报警 VIS-1002 现象说明与解决方法"),
        "release_note": ("WPS-3000 V2.3 有什么新功能",
                         "WPS-3000 V2.3 版本发布说明 新增功能 修复项",
                         "WPS-3000 最新版本变更内容与升级说明"),
        "spec":         ("WPS-3000 温控范围多少",
                         "WPS-3000 温控模块规格参数 温控范围 精度",
                         "WPS-3000 温度控制技术指标"),
        "fault_case":   ("WPS-3000 真空报警怎么处理",
                         "WPS-3000 真空报警 故障案例 处理经过 解决方法",
                         "WPS-3000 真空系统报警排查与维修记录"),
        "general":      ("WPS-3000 怎么了",
                         "WPS-3000 常见故障现象 故障诊断 处理步骤",
                         "WPS-3000 设备异常原因分析与维护保养规范"),
    }
    ex = _EXAMPLES.get(intent, _EXAMPLES["general"])
    example_block = (
        f"示例（原始问题: \"{ex[0]}\"）：\n"
        f"[\"精确改写\", \"语义改写\"] → [\"{ex[1]}\", \"{ex[2]}\"]\n"
    )

    # 口语化问题追加提示
    vague_note = (
        "注意：原始问题较口语化，请将其规范化为专业文档检索语句。\n"
        if is_vague else ""
    )

    prompt = (
        "你是半导体检测设备知识库检索助手。\n"
        "任务：将原始问题改写为 2 条检索语句，直接返回 JSON 数组，不要解释。\n\n"
        f"{example_block}"
        f"{vague_note}"
        f"改写要求：\n"
        f"- 第1条（精确检索式）：保留原始实体，补充与「{intent_focus}」相关的领域术语\n"
        f"- 第2条（语义扩展式）：用描述性语言表达同一查询意图，不要与第1条重复\n"
        f"- 两条均需是完整自然语句，不能只罗列词汇\n\n"
        f"原始问题: {question}\n"
        f"已识别实体: {entity_hint}\n"
        f"输出格式: [\"精确检索式\", \"语义扩展式\"]"
    )
    try:
        resp = await asyncio.wait_for(
            llm_client.chat.completions.create(
                model="glm-4-flash",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            ),
            timeout=4.0,
        )
        text = (resp.choices[0].message.content or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        parsed = json.loads(text)
        if isinstance(parsed, list):
            variants = [str(v).strip() for v in parsed if str(v).strip()][:2]
            if variants:
                return variants
    except Exception:
        pass
    return [question]


# ── LLM 调用：问题分解（P3）────────────────────────────────────────────────────
async def _llm_decompose(llm_client: Any, question: str, intent: str) -> list[str]:
    """
    P3 复杂问题分解：根据意图约束拆分子问题。
    - 每个子问题保留原始实体（编号、版本）
    - 子问题之间相互独立可单独检索
    - 失败时返回空列表
    """
    intent_instruction = {
        "spec":         "可按机型维度、参数维度分别拆分",
        "release_note": "可按版本号分别拆分，每条问一个版本",
        "fault_code":   "可按故障码 + 处理措施分别拆分",
        "8d":           "可拆分为：问题描述、根因分析、纠正措施三个子问题",
        "fmea":         "可按风险项（失效模式）分别拆分",
    }.get(intent, "按不同查询维度或不同实体分别拆分")

    prompt = (
        f"将以下复杂问题拆分为 2-4 个可独立检索的子问题。\n"
        f"拆分原则：{intent_instruction}\n"
        "每个子问题需保留原始问题中的产品编号、版本号等关键实体。\n"
        "仅返回 JSON 数组，格式: [\"子问题1\", \"子问题2\"]，不要解释。\n\n"
        f"问题: {question}"
    )
    try:
        resp = await asyncio.wait_for(
            llm_client.chat.completions.create(
                model="glm-4-flash",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            ),
            timeout=5.0,
        )
        text = (resp.choices[0].message.content or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            return []
        return [str(q).strip() for q in parsed if str(q).strip()][:4]
    except Exception:
        return []


# ── 主入口 ─────────────────────────────────────────────────────────────────────
async def build_query_plan(
    llm_client: Any,
    question: str,
    existing_filter_fields: set[str] | None = None,
) -> QueryPlan:
    """
    给定用户原始问题，返回完整的 QueryPlan：
    - 实体抽取和意图识别（同步规则，无延迟）
    - 查询改写（LLM，超时 3s，失败回退原问题）
    - 复杂问题分解（LLM，仅在 should_decompose 时触发，超时 4s）
    - 自动推断元数据过滤（规则，跳过 existing_filter_fields 中已有字段）
    """
    if existing_filter_fields is None:
        existing_filter_fields = set()

    entities = extract_entities(question)
    intent   = classify_intent(question)

    is_complex = should_decompose(question, entities)

    # doc_id 精确命中时跳过 LLM 改写，直接用原始问题（避免改写丢失精确编号）
    if entities.doc_ids:
        rewrite_variants = [question]
        sub_queries: list[str] = []
    else:
        # 并发执行：改写(P2) + 分解(P3，仅在需要时)
        rewrite_task   = asyncio.create_task(_llm_rewrite(llm_client, question, entities, intent))
        decompose_task = asyncio.create_task(_llm_decompose(llm_client, question, intent)) if is_complex else None

        rewrite_variants = await rewrite_task
        sub_queries = []
        if decompose_task is not None:
            sub_queries = await decompose_task

    # 主改写串 = variants[0]
    rewritten_query = rewrite_variants[0] if rewrite_variants else question

    suggested_filters = build_suggested_filters(entities, intent, existing_filter_fields)

    return QueryPlan(
        raw_question=question,
        rewritten_query=rewritten_query,
        rewrite_variants=rewrite_variants,
        sub_queries=sub_queries,
        intent=intent,
        entities=entities,
        suggested_filters=suggested_filters,
        is_complex=is_complex,
    )
