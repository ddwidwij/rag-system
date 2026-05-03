# RAG 知识库问答系统 — 功能现状、优化计划与测试需求

> 文档类型：项目工程评审  
> 更新日期：2026-04-29  
> 当前测试基线：**15 / 45 通过（33.33%）**

---

## 一、已实现功能清单

### 1.1 基础检索层

| 功能 | 实现位置 | 状态 |
|------|---------|------|
| 向量检索（BGE-M3 Embedding） | `core/store.py` | ✅ 已上线 |
| BM25 稀疏检索（jieba 中文分词） | `core/store.py` | ✅ 已上线 |
| 混合检索融合（Hybrid Multi-query） | `core/store.py → retrieve_hybrid_multi` | ✅ 已上线 |
| 同义词查询扩展 | `core/store.py → expand_query`，`core/synonyms.json` | ✅ 已上线 |
| 精确标识符加权（机型/版本/故障码） | `core/store.py` | ✅ 已上线 |
| Cross-Encoder Rerank 精排 | `core/rag_chain.py → rerank_with_scores` | ✅ 已上线 |
| 结果去重（多 chunk 来源控制） | `core/rag_chain.py → dedupe_sources`，max_per_source=5 | ✅ 已上线 |
| 元数据显式过滤（用户手选） | `server.py → _build_where` | ✅ 已上线 |
| 元数据自动推断过滤（实体映射） | `server.py → _merge_where` + `query_understanding` | ✅ 已上线 |
| 多格式文档解析入库 | `core/parsers.py`（md/txt/pdf/docx/pptx/xlsx） | ✅ 已上线 |

### 1.2 查询理解层（`core/query_understanding.py`）

| 功能 | 实现状态 |
|------|---------|
| 实体抽取（机型/版本/故障码/文档编号/工单号/部门/客户） | ✅ 规则实现 |
| 意图识别（spec/release_note/fault_code/8d/fmea 等 10 类） | ✅ 规则实现 |
| 自动 `suggested_filters` 生成 | ✅ 已集成到主流程 |
| 查询改写（rewrite_query） | ⚠️ 接口已预留，当前无实质改写逻辑 |
| 复杂问题分解（decompose_query） | ⚠️ 信号词检测逻辑存在，但未接入多子查询分别检索 |

### 1.3 路由与决策层（`core/router.py`）

| 功能 | 实现状态 |
|------|---------|
| 路由规则（kb / web / sql / manual） | ✅ 规则实现 |
| ExecutionPlan 生成（工具列表 + 重试策略） | ✅ 已实现 |
| 工具注册表（TOOL_REGISTRY） | ✅ 接口定义完成 |
| web_search / sql_query 实际调用 | ❌ Stub 占位，未接真实工具 |

### 1.4 自检与重试层（`core/self_reflection.py`）

| 功能 | 实现状态 |
|------|---------|
| 检索结果自检 `critique_evidence` | ✅ 已实现（低置信/类型不符/实体未命中三项检查） |
| 二次检索触发判断 `should_retry` | ✅ 已实现 |
| 二次检索查询构建 `build_retry_query` | ✅ 已实现 |
| 答案质量自检 `critique_answer`（关键词覆盖率） | ✅ 已实现（覆盖率 ≥ 0.5 通过） |
| LLM 语义相关性判断 `llm_judge_relevance` | ✅ 刚完成（本轮新增） |
| Step 3.4：关键词缺失 → 补充检索 + 重新生成 | ✅ 已集成 server.py |
| Step 3.5：LLM 判定无关 → 宽松检索 + 重新生成 | ✅ 刚完成（本轮新增） |

### 1.5 服务与可观测性层

| 功能 | 实现状态 |
|------|---------|
| FastAPI SSE 流式输出 | ✅ 已上线 |
| SSE 事件类型：chunk / evidence / plan / critique / retry / llm_relevance / done | ✅ 已上线 |
| 审计日志（NDJSON 格式） | ✅ 已上线 |
| 文档上传接口 `/upload` | ✅ 已上线 |
| 文档批量导入接口 `/ingest` | ✅ 已上线 |
| 前端静态页面（`static/index.html`） | ✅ 已上线 |
| 健康检查 `/health` | ✅ 已上线 |
| 元数据选项接口 `/api/meta-options` | ✅ 已上线 |

### 1.6 评测与工具层

| 功能 | 实现状态 |
|------|---------|
| 45 用例自动化评测（检索准确性 + 元数据筛选） | ✅ `scripts/run_metadata_tests.py` |
| 同义词扩展效果 A/B 评测 | ✅ `tools/synonym_eval.py` |
| 文档规范检查工具 | ✅ `tools/checker.py` |
| 查询理解/路由/自检单项评测脚本 | ✅ `tools/query_understanding_eval.py` 等 |

---

## 二、需要优化的功能

### 2.1 【P1-高优先级】查询改写（Query Rewriting）

**当前问题：** `build_query_plan` 已抽取实体，但未对用户原句做语义改写，口语化/模糊问法直接进入检索，命中率低。

**优化方案：**
- 在 `core/query_understanding.py` 的 `build_query_plan` 中增加真实改写逻辑
- 改写目标：口语化 → 结构化（"WPS-3000 怎么了" → "WPS-3000 故障码定义与处理建议"）
- 首版用规则 + 少量模板；后续可接 LLM 改写
- 输出追加到 `QueryPlan.rewritten_query`，检索时优先使用改写后问题

**验收指标：** R 类测试中含模糊问法的用例通过率提升 ≥ 10%

---

### 2.2 【P1-高优先级】答案质量自检对通用问题的适用性

**当前问题：** `critique_answer` 仅检查实体关键词覆盖率；当问题不含机型/版本/故障码等实体时（通用类问题），覆盖率逻辑返回 `passed=True`，等于没有检查。

**优化方案：**
- 已通过本轮新增 `llm_judge_relevance` 做兜底语义判断
- 进一步优化：对 LLM 判定 `relevant=False` 且 `confidence=high` 的情况，增加 `reason` 字段写入 SSE 事件，便于前端提示用户
- 考虑将 LLM 相关性判断结果反馈到测试评测脚本，作为新的评测维度

**验收指标：** 通用问题类（无实体）的答案无关率降低至 < 10%

---

### 2.3 【P1-高优先级】复杂问题分解（Query Decomposition）

**当前问题：** `_DECOMPOSE_SIGNALS` 信号词检测逻辑存在于 `query_understanding.py`，但 `build_query_plan` 返回的 `sub_queries` 字段始终为空；`server.py` 未使用 `sub_queries` 做多路检索。

**优化方案：**
1. 完善 `decompose_query()` 函数：根据信号词将问题拆成 2~3 个子问题
2. 在 `server.py` Step 2 中，当 `plan.sub_queries` 非空时，对每个子问题独立调用 `retrieve_hybrid_multi`，再统一 rerank 和去重
3. 最大子问题数建议限制为 3 个（避免开销过大）

**验收指标：** 含"和/对比/分别"等信号词的复杂问题 Top3 命中率提升 ≥ 15%

---

### 2.4 【P2-中优先级】多工具编排（Web Search / SQL）

**当前问题：** `TOOL_REGISTRY` 中 `web_search`、`sql_query` 均为 Stub，路由到 `web`/`sql` 时系统实际回退到 kb 检索。

**优化方案：**
- `web_search`：接入 Bing/Tavily Search API，处理"实时/联网"类问题
- `sql_query`：接入内部业务数据库，处理"统计/列表/数量"类结构化查询
- 首版实现 web_search，sql_query 可继续保留 Stub

**验收指标：** 含实时性问题路由到 web 后可返回有效结果

---

### 2.5 【P2-中优先级】前端 Agent 决策可观测性

**当前问题：** SSE 已输出 `plan`、`retry`、`critique`、`llm_relevance` 等事件，但前端 `static/index.html` 未渲染这些事件，用户看不到系统做了哪些决策。

**优化方案：**
- 前端增加"检索日志面板"（可折叠），展示：
  - 识别出的意图与实体
  - 路由结果（kb/web/sql/manual）
  - 是否触发重试及重试原因
  - LLM 相关性判断结果与理由
  - 证据来源卡片（已部分实现）

**验收指标：** 每次查询结束后面板完整显示 plan → critique → retry → llm_relevance 四类事件

---

### 2.6 【P2-中优先级】检索参数自适应调整

**当前问题：** `retrieve_top_k=15`、`rerank_top_k=7` 为硬编码默认值，在不同意图下效果差异大：
- 精确查询（含编号）：top_k 过大引入噪声
- 通用问题：top_k 偏小导致漏召回

**优化方案：**
- 根据意图类型动态调整：`fault_code` 类 top_k 可降至 8；`spec` 类可升至 20
- 在 `ExecutionPlan` 中增加 `retrieve_top_k` / `rerank_top_k` 字段，由路由层推荐

---

### 2.7 【P3-低优先级】答案引用规范化

**当前问题：** LLM 生成答案中来源引用格式不统一（有时引用完整路径，有时只引用文件名，有时不引用）。

**优化方案：**
- 在 Prompt 模板中统一要求格式：`[来源: 文件名.md]`
- 生成后通过正则后处理补全缺失引用

---

## 三、需要测试的功能

### 3.1 回归测试（已有测试用例）

| 测试组 | 用例数 | 当前通过 | 目标通过 | 执行命令 |
|--------|--------|---------|---------|---------|
| R 类：检索准确性（无筛选） | 25 | ~ 10 | 18+ | `python scripts/run_metadata_tests.py` |
| M 类：元数据筛选准确性 | 20 | ~ 5 | 14+ | 同上 |
| 总计 | 45 | 15（33%） | 32+（70%） | — |

---

### 3.2 新功能专项测试（本轮新增）

#### T-01：LLM 语义相关性判断（`llm_judge_relevance`）

| 测试项 | 预期结果 |
|--------|---------|
| 问题与答案高度相关 → 应返回 `relevant=True` | PASS |
| 问题与答案完全不相关 → 应返回 `relevant=False` | PASS |
| LLM 网络超时或响应格式错误 → 保守返回 `relevant=True`，不触发重试 | PASS |
| `confidence` 字段取值只能为 high/medium/low | PASS |
| SSE 流中能收到 `llm_relevance` 事件 | PASS |

**测试方法：** 启动 server，发送构造好的问答对，检查 SSE 事件序列

---

#### T-02：Step 3.4 关键词缺失补充检索

| 测试项 | 预期结果 |
|--------|---------|
| 初答缺少关键实体词 → 触发 `retry` 事件，`strategy=supplement_retrieval` | PASS |
| 补充检索后重新生成的答案含缺失关键词 | PASS |
| `already_retried=True` 后不再触发 Step 3.5 LLM 补充检索 | PASS |

---

#### T-03：Step 3.5 LLM 驱动补充检索

| 测试项 | 预期结果 |
|--------|---------|
| LLM 判定不相关且 Step 3.4 未触发 → 发出 `retry` 事件，`strategy=llm_driven_supplement` | PASS |
| 补充检索后重新生成答案流式正常输出 | PASS |
| Step 3.4 已触发时，Step 3.5 不再触发（`already_retried` 保护） | PASS |

---

#### T-04：已有自检模块回归

| 测试项 | 预期结果 |
|--------|---------|
| `critique_evidence`：低 rerank 分数场景触发 `low_confidence` 标签 | PASS |
| `critique_evidence`：intent=fault_code 但证据全为 spec 类型 → `type_mismatch` | PASS |
| `critique_answer`：答案覆盖率 < 0.5 时 `passed=False` | PASS |
| `critique_answer`：无实体通用问题 → 直接 `passed=True`，不阻断 | PASS |

---

### 3.3 端到端场景测试

| 场景 | 问题示例 | 验收标准 |
|------|---------|---------|
| 精确机型+版本查询 | "WPS-3000 V2.3 新增了哪些功能？" | Top1 命中对应版本文档，答案含新增功能列表 |
| 故障码查询 | "VIS-1002 是什么意思，如何处理？" | Top1 命中故障码文档，答案含处理步骤 |
| 8D 报告查询 | "8D-2025-0087 的根因是什么？" | Top1 命中对应 8D 文档，答案含根因分析 |
| 跨文档通用问题 | "探针卡通常多久需要更换一次？" | 答案相关（LLM relevance=true），无明显无关内容 |
| 元数据筛选+检索 | 筛选 doc_type=fault_code，问"有哪些高压相关报警" | 结果全部属于 fault_code 类文档 |
| 无关问题（应拒答或说明） | "今天北京天气怎么样？" | 路由到 web（或友好提示无法回答），不伪造业务答案 |
| 含分解信号的复杂问题 | "WPS-3000 和 SCT-2100 的温控范围分别是多少？" | 两个机型均有覆盖（待问题分解上线后验收） |

---

### 3.4 性能与稳定性测试

| 测试项 | 指标要求 |
|--------|---------|
| 单次查询端到端响应时间（首 token） | ≤ 3 秒（含 rerank） |
| LLM 相关性判断新增延时 | ≤ 1.5 秒（非流式单次调用） |
| 连续 45 个测试用例全量跑通无异常退出 | 0 crash，0 unhandled exception |
| 并发 3 个查询同时发起 | 全部正常返回，无数据串流 |

---

## 四、风险与注意事项

| 风险 | 影响 | 应对措施 |
|------|------|---------|
| LLM 相关性判断结果不稳定（GLM 输出非 JSON） | Step 3.5 误触发或不触发 | 已做 `try/except` 保守 fallback，需监控 `raw_response` 日志 |
| 补充检索两次叠加导致延时过长 | 用户体验差 | `already_retried` 标志保证两路互斥；超时时直接返回已生成内容 |
| 复杂问题分解生成子问题语义漂移 | 检索结果发散 | 子问题数量上限 3 个；每路 top_k 独立限制 |
| 测试评测命令需离线模式运行 | 下载模型失败 | 始终使用 `TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1` |

---

## 五、当前推荐执行顺序

```
[本轮已完成]
  ✅ LLM 语义相关性判断 (llm_judge_relevance)
  ✅ Step 3.5 LLM 驱动补充检索

[下一步 P1]
  1. 完善查询改写逻辑（对通用/模糊问题）
  2. 激活复杂问题分解路径（sub_queries 多路检索）
  3. 运行第 4 轮测试，目标 > 25/45

[后续 P2]
  4. 前端 Agent 决策面板可视化
  5. 检索参数自适应调整
  6. web_search 工具接入
```

---

*文档生成自代码审查，如有功能变更请同步更新本文档。*
