# 信息使能AI平台需求

## 目标拆分

### 目标1：低级问题（文本描述类问题）自动检查率达到 80%

**目标定义**

*   规则类问题自动发现率：90%+
    

*   语义类低级问题自动发现率：60%~75%
    

*   综合后达到 80% 
    

**覆盖项**

*   错别字、语病、术语不统一、格式不一致、模板缺项、编号/单位不规范。
    
*   表述重复、歧义、语气不规范
    
*   note/warning/caution 使用不规范
    
*   链接、xref、conref异常
    

### 目标2：技术类准确性自动化检查率达到 50%

不定义为AI 自动判断所有技术内容是否正确。定义成：技术参数/命令/版本/术语/配置项的自动比对覆盖率达到 50%。

**覆盖项**

*   参数名称是否正确
    

*   默认值/取值范围是否一致
    

*   命令格式是否一致
    

*   产品名、模块名、接口名是否正确
    

核心实现路径

*   以知识库/RAG + 结构化比对 + 规则校验为主
    
*   以大模型做差异识别、风险提示和依据输出为辅
    
*   采用“自动识别 + 人工确认”模式
    

参数抽取 DITA 中的参数/命令/版本/默认值 → 去权威源比对 → 再让模型生成解释和风险说明。

技术检查应该以：参数规格、API 定义、术语表/命令表，作为依据，AI 只负责解释和补充，不负责单独裁决。

### 目标3：面向客户的产品文档自动化生成率达到 30%

**目标定义**  
AI 生成可供writer直接修改的 DITA 草稿占新增文档的 30%

**覆盖项：**

*   topic 草稿生成
    
*   基于现有文档，使用AI助手改写
    
*   使用AI助手可直接翻译英文手册为中文初稿
    
*   从已知模板生成 topic 框架
    

核心实现路径

*   先做草稿生成、改写、简单翻译
    
*   再根据需求拆解、输入的概要设计、详细设计做产品介绍、功能说明等描述性介绍内容
    
*   可根据外部输入的安装、操作SOP直接生成安装手册、操作维护手册中对应的章节内容
    

| 内容接入层 | 负责读取和整理源文件 | 输入：<br>*   `.dita`<br>    <br>*   `.ditamap`<br>    <br>*   术语表 | 输出：<br>*   结构化内容对象<br>    <br>*   topic 索引<br>    <br>*   map 依赖关系<br>    <br>*   引用关系图 | 建议能力：<br>*   XML 解析<br>    <br>*   DITA topic 类型识别<br>    <br>*   ditamap 解析<br>    <br>*   conref / keyref / xref 解析<br>    <br>*   metadata 抽取<br>    <br>*   版本属性抽取 |
| --- | --- | --- | --- | --- |
| 规则校验层 | 负责“确定性问题”检查。<br>*   结构完整性检查<br>    <br>*   引用完整性检查<br>    <br>*   规范性检查<br>    <br>*   可发布性检查 | *   XPath<br>    <br>*   XSLT<br>    <br>*   Schematron<br>    <br>*   自定义 Python/Java 校验器 | *   问题列表<br>    <br>*   问题等级<br>    <br>*   问题定位 |  |
| 技术真值比对层 | 负责“技术类准确性”检查<br>*   字段映射<br>    <br>*   差异比对<br>    <br>*   缺失项检查<br>    <br>*   冲突项标记 | *   DITA 内容中抽取的参数、命令、表格、版本、名称<br>    <br>*   权威源数据 | *   一致项<br>    <br>*   不一致项<br>    <br>*   疑似过期项<br>    <br>*   缺证据项 |  |
| AI 语义检查层 | 负责“低级语义问题”和“受控生成”<br>*   文风检查<br>    <br>*   歧义检查<br>    <br>*   术语一致性建议<br>    <br>*   shortdesc 生成<br>    <br>*   标题优化<br>    <br>*   topic 草稿生成<br>    <br>*   修订建议生成 |  |  |  |
| 执行与集成层 | 负责把能力接入实际流程 |  | *   CLI 命令行<br>    <br>*   CI 检查<br>    <br>*   批量扫描<br>    <br>*   HTML 报告<br>    <br>*   JSON API<br>    <br>*   Web 审阅页面 |  |

## 业务场景验证

**落地方案（一）**

已有个人开发者开发出较好的AI辅助工具Text-Well：![image.png](https://alidocs.oss-cn-zhangjiakou.aliyuncs.com/res/eYVOL5jNAN0eLlpz/img/ba9a62ab-edef-47e0-a33e-612cbbb5d1bf.png)

[https://www.text-well.com/zh/app: https://www.text-well.com/zh/app](https://www.text-well.com/zh/app)

可实现word文稿的写作、检查、评审、翻译。

**存在问题：**

无法与现有资料开发软件oxygen集成、不支持dita格式文件的检查、内容生成。需要单独开发web前端+后端的应用。

**可实现路径：**

前期可参考text-well先开发可进行基于XML/DITA规则检查和低级错别字问题检查的应用，预留AI服务接口。

后期接入内网部署的AI服务器，利用AI模型能力实现。

**落地方案（二）**

利用资料开发软件oxygen官方提供的AI插件AI Positron Assistant（官方提供的AI服务，调用的是gpt）。

![image.png](https://alidocs.oss-cn-zhangjiakou.aliyuncs.com/res/eYVOL5jNAN0eLlpz/img/3051c3fb-1185-4c62-95d2-33e014a8cf6c.png)

可实现基于dita文件的草稿生成、术语生成、语法检查、可读性改写、英文翻译等。

**存在问题：**

现阶段只是业务场景验证，使用该AI插件需购买官方正版oxygen软件，订阅官方的AI插件服务，价格昂贵。调用外部大模型存在信息安全问题。

**验证情况：**

已安装非官方的开源破解插件，接入收费/免费的外部大模型，已打通业务场景，目标1，2，3均可覆盖。

下一步需结合资料开发全流程，验证AI辅助能力，评估对模型要求。

**可实现路径：**

安装非官方的开源破解插件， 配置AI Positron助手连接至本地部署的AI服务。

---

## 知识工程+知识库问答与大模型交互系统

# 1.完整建设方案

## （1） 总体建设思路与建设原则

这套方案不把“AI知识库”定义成一个聊天机器人项目，而是定义成“半导体芯片检测设备企业内部知识底座 + 权限继承检索体系 + 可运营的知识治理机制 + 面向业务场景的AI助手群”。

**总体建设思路**

*   统一知识底座：把共享盘、钉钉/企微、本地文档、PLM、代码仓、MES、项目资料、服务工单、测试日志等先接进同一知识底座，再做AI应用。
    
*   分层治理：原始文件、解析内容、知识对象、标签体系、检索索引、AI应用分层建设，避免后期重构。
    
*   权限隔离：权限不在AI层“另做一套”，而是继承 LDAP/SSO + 项目/客户/部门权限，并下沉到检索结果级别。
    
*   AI增强应用：先做“查得到、答得出、能引用”，再做“会分析、会推荐、会生成”。
    
*   先试点后扩展：先选研发文档检索、故障案例问答、测试用例辅助生成三个高价值场景打样。
    
*   业务驱动：优先解决新员工培养慢、跨部门找资料难、故障经验不复用、项目重复踩坑四类业务痛点。
    
*   内容可信：所有回答必须附来源、版本、时间、责任部门，冲突内容要提示版本差异。
    
*   可持续运营：把知识入库、审核、复核、下线、责任人机制固化到流程，不做“一次性展示项目”。
    

## （2）企业级总体架构设计

### 2.1 总体架构

| 层级 | 主要职责 | 关键模块 | 输入 | 输出 |
| --- | --- | --- | --- | --- |
| 知识来源层 | 汇聚企业原始知识 | 共享盘、钉钉/企微文件、PLM、代码仓、MES、CRM/项目系统、服务工单、邮件、日志库 | 文件、表单、记录、日志、图片 | 原始知识源 |
| 数据接入层 | 连接、同步、增量抓取 | API连接器、文件监听、定时任务、事件订阅、CDC、手工导入 | 原始知识源 | 原始数据副本、接入日志 |
| 文档解析与清洗层 | 抽取文本、表格、版面、元数据 | OCR、版面分析、表格抽取、附件拆解、去噪、去重、分块 | PDF/Word/Excel/PPT/图片/日志/工单 | 标准化内容块、文档元数据 |
| 知识建模与标签层 | 形成业务可理解知识对象 | 文档对象、项目对象、模块对象、故障案例对象、测试用例对象、BOM对象、版本对象、客户对象 | 标准化内容块、结构化记录 | 知识对象、标签、关系 |
| 检索层 | 可控检索与召回 | 全文检索、向量检索、混合检索、重排序、权限过滤 | 查询、用户身份、知识对象 | 候选证据集 |
| 大模型与RAG层 | 生成可引用答案 | 查询改写、检索增强、答案合成、引用拼装、冲突检测、置信提示 | 候选证据集、提示模板 | 带引用答案、摘要、对比、推荐 |
| 应用层 | 面向角色交付价值 | 智能搜索、问答助手、项目助手、售后助手、培训助手、测试助手、变更影响助手 | 答案与知识对象 | 页面、对话、分析结果、报告 |
| 权限安全与审计层 | 权限继承、脱敏、留痕 | SSO/LDAP、ABAC/RBAC、字段级权限、敏感识别、审计日志 | 用户、权限、访问行为 | 授权结果、审计记录 |
| 运营评估层 | 持续优化 | 命中率分析、问答评测、知识缺口发现、知识老化监控、使用分析 | 使用日志、人工反馈 | KPI、优化清单 |

### 2.2 不同内容类型处理策略

| 内容类型 | 处理方式 | 解析重点 | 入库形态 | 注意事项 |
| --- | --- | --- | --- | --- |
| 图纸/CAD导出PDF/原理图截图 | OCR + 图框抽取 + 标题栏抽取 + 关联PLM元数据 | 图号、版本、模块、料号、适用机型 | 文档对象 + 图纸对象 | 原始CAD仍以PLM为准，不建议仅靠OCR作为事实源 |
| Word/PDF规格书/设计文档 | 版面解析 + 章节切分 + 表格抽取 | 章节、术语、接口约束、指标 | 文档块 + 章节对象 | 章节级切块优于固定长度切块 |
| Excel/BOM/配置表 | 表格结构化抽取 | 料号、数量、替代关系、版本 | BOM对象、配置对象 | 与PLM编码强绑定 |
| PPT培训/汇报材料 | 页面解析 + 标题摘要 | 方案摘要、客户案例、培训重点 | 页面块 + 摘要对象 | 仅作辅助知识，不作强权威源 |
| 测试报告/测试方案/测试用例 | 模板化解析 | 用例名称、前置条件、步骤、预期结果、适用版本 | 测试对象 | 支持后续AI生成与复用 |
| 测试日志/设备日志/报警记录 | 日志规整 + 字段抽取 + 事件聚合 | 报警码、时间、模块、版本、工位 | 事件对象、故障对象 | 更适合结构化检索与时序分析 |
| 工单/售后记录/远程支持记录 | 文本摘要 + 案例结构化 | 现象、环境、根因、处理动作、备件 | 故障案例对象 | 需脱敏客户名、产线、机台编号 |
| 项目文档/验收文档 | 项目映射 + 配置差异解析 | 客户、项目阶段、定制项、验收问题 | 项目对象 | 权限按客户/项目隔离 |
| 邮件/聊天记录 | 只抽取指定范围、只保留业务相关片段 | 结论、决策、问题闭环 | 讨论证据对象 | 不建议全文开放问答，需审批纳入 |

### 2.3 结构化与非结构化知识融合

建议建立“知识对象模型”，核心对象至少包括：

*   产品线、机型、模块/子系统、文档、版本、料号/BOM、测试项、测试用例、故障码、故障案例、项目、客户、工单、质量事件、8D、FMEA、培训主题、岗位。
    

关键关系至少包括：

*   文档属于哪个产品线/模块/版本。
    
*   测试用例验证哪个需求、哪个功能点、哪个软件版本。
    
*   故障案例关联哪个机型、哪个模块、哪个报警码、哪个客户现场。
    
*   BOM/器件变更影响哪些图纸、哪些测试项、哪些质量风险。
    
*   客户项目定制项与标准产品差异映射到哪些配置。
    

### 2.4 回答如何附带引用来源和版本信息

每次AI回答至少返回：

*   来源文档标题
    
*   文档编号/图号/工单号/案例号
    
*   版本号或修订号
    
*   生效日期/更新时间
    
*   所属部门/责任人
    
*   引用片段
    
*   权限说明
    
*   若多版本冲突，显示“最新受控版”与“历史版”差异提示
    

建议前端展示“证据卡片”，而不是只给一段自然语言答案。

## （3）知识分类体系与元数据标准

### 3.1 分类体系

| 维度 | 建议分类 |
| --- | --- |
| 产品线 | 晶圆测试设备、SoC测试设备、ATE配套设备、治具/接口板/上位机配套 |
| 模块/子系统 | 机械平台、电气控制、运动控制、视觉/传感、治具接口、测试头、温控、软件平台、算法、PLC/HMI、上位机、数据采集、校准模块 |
| 项目 | 内部研发项目、客户交付项目、质量改进项目、工艺验证项目 |
| 客户 | 行业、客户名、客户等级、区域、保密级别 |
| 阶段 | 预研、立项、研发、样机、试产、小批量、量产、交付、售后、退市 |
| 文档类型 | 规格书、需求、概要设计、详细设计、图纸、BOM、评审记录、测试方案、测试用例、调试记录、作业指导书、8D、FMEA、FAQ、培训资料 |
| 所属部门 | 软件、硬件、逻辑、测试、整机、质量、供应链、项目、AE、市场、资料开发、信息使能 |
| 保密等级 | 公开内部、部门内、项目级、客户级、核心机密 |
| 版本状态 | 草稿、评审中、已发布、已冻结、已废止 |
| 生命周期状态 | 新建、审核中、生效、待复核、归档、下线 |

### 3.2 目录树设计

目录树建议采用“业务主线 + 统一标签”模式，不建议只靠文件夹层层嵌套。

推荐一级目录：

*   产品研发
    
*   测试与验证
    
*   制造与质量
    
*   项目交付与客户服务
    
*   销售与方案支持
    
*   组织制度与培训
    

每份知识同时打标签，不依赖单一路径存放。原因是同一份文档往往同时属于“某产品线 + 某模块 + 某项目 + 某客户 + 某版本”。

### 3.3 元数据字段标准

建议最少元数据字段：

*   doc\_id
    
*   title
    
*   product\_line
    
*   model\_type
    
*   module
    
*   project\_id
    
*   customer\_id
    
*   department
    
*   doc\_type
    
*   version
    
*   status
    
*   lifecycle\_state
    
*   confidential\_level
    
*   owner
    
*   reviewer
    
*   effective\_date
    
*   expire\_date
    
*   source\_system
    
*   source\_path
    
*   related\_part\_no
    
*   related\_alarm\_code
    
*   related\_test\_item
    
*   related\_software\_version
    
*   related\_hardware\_version
    

### 3.4 为什么这样设计

*   支持智能检索：可按“机型+模块+版本+问题现象”组合检索。
    
*   支持权限管理：可按客户、项目、部门、保密级别做过滤。
    
*   支持影响分析：变更时可快速定位受影响图纸、BOM、测试项、质量文件。
    
*   支持知识运营：可识别缺少责任人、长期未复核、版本过期的知识。
    

## （4）知识采集与治理机制

### 4.1 历史知识导入

分三类导入：

*   高价值受控文档优先：规格书、设计文档、图纸目录、BOM、测试方案、作业指导书、8D、FMEA、售后案例。
    
*   高频使用资料其次：FAQ、培训资料、项目复盘、AE现场经验。
    
*   弱治理资料最后：邮件、聊天、零散笔记，仅提炼结论性内容入库。
    

### 4.2 增量知识持续进入

建议建立四条增量通道：

*   系统自动入库：PLM发布、MES异常单、工单关闭、项目里程碑文档自动同步。
    
*   流程触发入库：评审通过、版本发布、8D关闭、FAT/SAT完成后自动触发。
    
*   模板化人工沉淀：故障案例卡、项目经验卡、测试策略卡。
    
*   AI辅助沉淀：从工单、会议纪要、聊天记录中提取候选经验，由责任人审核入库。
    

### 4.3 必须入库的文档范围

必须入库：

*   产品定义/规格书/技术协议
    
*   受控设计文档与评审记录
    
*   图纸目录与BOM版本
    
*   测试方案、测试用例、测试报告
    
*   装配/调试/检验指导书
    
*   8D、FMEA、可靠性验证记录
    
*   项目验收文档、客户定制配置清单
    
*   售后故障闭环案例
    
*   流程制度、培训资料、岗位知识地图
    

不建议直接原样入库而应先处理：

*   聊天记录、邮件串、临时草稿、重复版本、未经确认的现场口头经验。
    

### 4.4 经验类知识的结构化模板

对故障/项目/测试经验统一采用卡片模板：

*   适用产品/机型
    
*   适用版本
    
*   问题现象
    
*   触发条件
    
*   排查步骤
    
*   根因
    
*   临时措施
    
*   永久措施
    
*   验证结果
    
*   关联工单/客户/项目
    
*   是否可复用
    
*   责任人
    
*   复核时间
    

### 4.5 清洗、拆分、去重、归档

*   清洗：去空白页、去扫描噪声、统一编码、统一时间格式。
    
*   拆分：按章节、功能点、表格、步骤块拆分，不按固定字符硬切。
    
*   去重：基于文件哈希 + 标题 + 版本 + 文本近似度联合判重。
    
*   合并：同一主题多来源资料汇聚成知识专题页。
    
*   归档：废止文档保留但默认不参与普通问答，只作追溯。
    
*   版本控制：同文档仅一个“当前有效版”，历史版保留索引但低优先级。
    
*   失效管理：超过复核期自动提醒责任人，未复核则降权或下线。
    

### 4.6 审核与责任机制

| 角色 | 核心职责 |
| --- | --- |
| 知识平台主管 | 制定分类、标准、KPI，推进跨部门协同 |
| 部门知识官 | 负责本部门知识盘点、审核组织、质量推进 |
| 文档责任人 | 对文档准确性、版本有效性负责 |
| 业务专家 | 对经验类知识复核，校正AI回答 |
| IT/数据平台团队 | 建连接器、索引、权限、模型、应用 |
| 安全与审计负责人 | 做分级分类、脱敏策略、审计检查 |
| 信息使能部 | 统筹平台、账号、权限、培训、运营报表 |

建议建立“文档有主、专题有官、答案可追责”的机制。

## （5）AI应用场景设计

### 5.1 重点场景

| 场景 | 适用对象 | 所需知识来源 | 业务价值 | 技术难点 | 优先级 |
| --- | --- | --- | --- | --- | --- |
| 智能搜索 | 全员 | 全部受控文档、共享盘、PLM、FAQ | 替代找文件、问同事 | 权限过滤、同义词 | P0 |
| RAG问答 | 研发、测试、AE、质量 | 设计文档、工艺文档、案例库 | 快速答技术问题并给出处 | 多版本冲突、术语歧义 | P0 |
| 项目经验复用 | 项目经理、AE、研发 | 项目总结、验收、客户配置差异 | 减少重复踩坑 | 相似项目判定 | P1 |
| 故障诊断助手 | AE、售后、测试 | 工单、日志、故障码、维修案例 | 提升排障效率 | 症状到根因链条复杂 | P0 |
| 装调与维修助手 | 制造、现场服务 | 作业指导书、调试记录、备件记录 | 缩短装调与维修时间 | 图文步骤对齐 | P1 |
| 新员工培训助手 | 新员工、部门主管 | 培训资料、制度、岗位地图 | 缩短培养周期 | 路径个性化 | P1 |
| 变更影响分析助手 | 研发、质量、供应链 | PLM、BOM、图纸、测试、FMEA | 降低变更漏评估风险 | 关系图谱建设 | P1 |
| 客户FAQ助手 | AE、售后、市场 | FAQ、标准答法、工单闭环 | 提升答复一致性 | 客户隔离 | P1 |
| 文档摘要与对比 | 研发、项目、市场 | 规格书、版本记录、投标资料 | 快速识别差异 | 表格/条款比对 | P0 |
| 知识缺口识别 | 知识平台主管、部门知识官 | 问答日志、零命中查询、工单热点 | 驱动补知识 | 缺口归因 | P1 |

### 5.2 测试部专项场景

结合你提供的图片，测试知识域建议单独建设“需求-策略-用例-自动化-执行结果”链路：

*   需求文档理解：从需求说明、规格约束、UI要求中抽取测试点。
    
*   测试策略确定：自动建议功能测试、边界测试、异常测试、UI测试、回归测试范围。
    
*   测试用例生成：按“用例名称、前置条件、测试步骤、预期结果”标准模板生成。
    
*   自动化可行性判断：识别哪些测试步骤可脚本化，哪些必须人工确认。
    
*   自动化脚本辅助：结合现有 robotframework、自定义函数库、上位机命令接口生成脚本草案。
    
*   执行结果回写：将脚本执行结果、日志、截图、断言失败回写知识库，形成“可复用测试资产”。
    

这部分是半导体检测设备公司很有价值的差异化能力，因为它把“测试知识库”直接变成“测试生产力工具”。

## （6）技术选型建议

### 6.1 推荐路线

对这类公司，我更建议“混合部署方案”为主：

*   核心检索、安全、索引、知识库在内网私有化。
    
*   文档解析优先本地化。
    
*   大模型优先私有部署或专有云隔离部署。
    
*   对外部商业服务，只在脱敏、非核心、非涉密场景使用。
    

### 6.2 选型建议表

| 技术域 | 开源优先方案 | 商业产品优先方案 | 混合部署建议 |
| --- | --- | --- | --- |
| 文档解析 | Unstructured + Apache Tika | Azure AI Document Intelligence / ABBYY | Office/PDF用 Unstructured，本地复杂扫描件按需引入商业OCR |
| OCR | PaddleOCR / Tesseract | Azure Document Intelligence | 中文表格、截图优先 PaddleOCR，复杂表单补商业OCR |
| 表格抽取 | PaddleOCR PP-Structure + Unstructured | Azure Document Intelligence | Excel走原生解析，扫描表格走PP-Structure |
| 图纸/图片文本 | OCR + 图框规则抽取 | 商业IDP | 图纸事实元数据以PLM为主，OCR只做辅助检索 |
| Embedding | BGE-M3 / GTE-multilingual-base | 商业Embedding服务 | 中文+英文资料混合场景优先多语模型 |
| 重排序 | BGE-reranker-v2-m3 / GTE-multilingual-reranker-base | Cohere Rerank类服务 | 私有化优先本地reranker |
| 向量数据库 | Milvus / pgvector / Qdrant | Elastic向量能力 | 若权限复杂，优先搜索引擎主控；若规模大，Milvus做向量层 |
| 全文检索 | OpenSearch / Elasticsearch | Elastic | 权限复杂场景优先 Elastic/OpenSearch |
| 大语言模型 | Qwen/DeepSeek类私有部署 + vLLM | 企业级商用模型平台 | 涉密回答优先本地推理 |
| Agent/编排 | LangGraph / Haystack | 商业AI中台 | 固定流程场景用workflow，少用全自主agent |
| 权限控制 | Keycloak + LDAP/SSO + OpenSearch/Elastic DLS/FLS | Microsoft Purview + Elastic/企业IAM | 文档权限由检索层强控，模型层不单独放权 |
| 监控评估 | Ragas + Prometheus + 审计日志 | 商业观测平台 | RAG评测与安全审计双轨运行 |

### 6.3 三种路线比较

| 维度 | 开源优先 | 商业优先 | 混合部署 |
| --- | --- | --- | --- |
| 安全性 | 高，可完全内网化 | 取决于厂商与部署模式 | 高，且可按场景分级 |
| 实施难度 | 较高 | 中 | 中偏高 |
| 成本 | 软件授权低，人力成本高 | 授权成本高 | 总体最平衡 |
| 性能 | 可调优，依赖团队能力 | 通常较稳定 | 平衡 |
| 可维护性 | 依赖内部平台团队 | 依赖厂商 | 较优 |
| 私有化适配 | 最好 | 部分产品受限 | 较好 |

### 6.4 我建议的主栈

*   检索主控：OpenSearch 或 Elasticsearch
    
*   向量层：中小规模直接用搜索引擎内置向量；规模增大后补 Milvus
    
*   文档解析：Unstructured + PaddleOCR
    
*   元数据主库：PostgreSQL
    
*   模型服务：vLLM 托管私有模型
    
*   工作流：LangGraph
    
*   统一认证：LDAP/SSO + Keycloak
    
*   评估：Ragas + 人工标注集
    
*   对象存储：MinIO 或企业现有NAS/对象存储
    

基于官方能力，我的判断是：这家公司权限复杂、文档异构、客户隔离要求高，所以“纯向量库路线”不适合作为主架构，应该由具备 DLS/FLS 的检索引擎做权限主控，向量检索只是其中一个召回能力。

## （7）安全、权限与合规设计

### 7.1 权限隔离原则

*   按部门隔离：研发、质量、市场、AE等默认最小权限。
    
*   按岗位隔离：普通员工、项目经理、部门负责人、专家、审计员不同视图。
    
*   按项目隔离：项目资料默认项目成员可见。
    
*   按客户隔离：客户A资料不能被客户B项目成员检索到。
    
*   按保密等级隔离：核心研发、报价、工艺窗口参数、算法细节单独控制。
    

### 7.2 如何实现“回答继承原文档权限”

*   每个知识对象写入权限标签，如部门、项目、客户、保密级别。
    
*   检索前先依据用户身份生成权限过滤条件。
    
*   检索结果先过 DLS/FLS，再进入RAG。
    
*   模型只能看见已授权证据，不允许先检索后再做前端隐藏。
    
*   没权限时返回“存在相关资料但您无权访问”或完全不提示，按公司策略设定。
    

### 7.3 敏感信息识别与脱敏

敏感信息规则建议覆盖：

*   客户名称、客户设备配置、产线信息
    
*   报价、成本、折扣
    
*   核心算法参数、工艺窗口、校准因子
    
*   料号映射、关键器件替代策略
    
*   个人信息、手机号、邮箱
    

处理方式：

*   入库前分级分类
    
*   问答前敏感检测
    
*   输出前脱敏重写
    
*   高风险问题转人工审批
    

### 7.4 审计与追溯

必须记录：

*   谁在什么时间问了什么问题
    
*   调用了哪些知识源
    
*   返回了哪些证据
    
*   是否命中敏感内容策略
    
*   是否发生拒答、脱敏、权限拦截
    
*   用户是否点开原文
    

### 7.5 外部模型风险控制

*   核心资料默认不出内网。
    
*   如需外部模型，必须先脱敏、抽象化、删标识。
    
*   设置模型调用白名单、网关、审计。
    
*   合同与法务层面明确数据不留存、不训练。
    
*   对涉密知识域优先使用本地模型。
    

## （8）分阶段实施路线图

| 阶段 | 建设目标 | 覆盖范围 | 数据范围 | 核心能力 | 交付成果 | 周期 | 风险点 | 组织投入建议 | 预算关注点 | 成功标准 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 阶段1 试点期 | 跑通可用闭环 | 软件/测试/AE/质量四部门 | 研发文档、测试方案、故障案例、FAQ、部分PLM元数据 | 智能搜索、RAG问答、引用、权限控制、测试用例辅助生成 | 试点知识库、问答助手、案例库、测试助手原型 | 10-12周 | 历史文档脏乱、权限边界不清 | 业务负责人1、知识平台主管1、部门知识官4、平台开发3-5、算法2、安全1 | OCR算力、检索集群、模型推理、连接器开发 | 试点用户周活>60%，问答引用率>95%，高频问题命中率>70% |
| 阶段2 推广期 | 扩到项目/制造/质量主链路 | 增加整机、制造、供应链、项目组 | MES异常、作业指导书、8D、FMEA、项目文档、售后工单 | 项目复用、故障诊断、装调助手、变更影响分析初版 | 多部门助手、项目知识专题、质量知识专题 | 4-6个月 | 系统接口复杂、专家参与度不足 | 再增加部门知识官3-5、数据集成2、运营1-2 | 接口建设、治理运营、知识审核人力 | 项目复用率提升、售后闭环时长下降、知识覆盖率>75% |
| 阶段3 平台期 | 建统一知识底座与多助手协同 | 全公司 | 全量受控知识域 | 企业搜索、专题助手、多角色工作台、知识运营、评估体系 | 企业统一知识平台、助手矩阵、治理制度、评测体系 | 6-12个月 | 变成“平台空转”、业务使用不足 | 设常设知识运营团队，信息使能部牵头 | 平台高可用、审计、GPU与存储扩容 | 全员检索入口统一、关键部门活跃稳定、年度知识更新机制成型 |

# 2. 分阶段实施

**阶段1：先做**

*   盘点四类高价值知识：规格/设计、测试方案与用例、故障案例、制度FAQ。
    
*   明确试点部门和权限边界。
    
*   建元数据标准、知识模板、案例模板。
    
*   打通共享盘、PLM部分元数据、工单/FAQ。
    
*   上线智能搜索、RAG问答、故障案例检索、测试用例辅助生成。
    
*   建人工评测集，按周优化。
    

**阶段2：再扩**

*   接MES异常、8D、FMEA、装调指导书、项目验收文档。
    
*   上线项目助手、装调助手、变更影响分析初版。
    
*   引入知识缺口分析与知识老化治理。
    
*   建客户/项目/版本专题知识页。
    

**阶段3：平台化**

*   建企业统一知识门户。
    
*   建多助手协同工作台。
    
*   建统一审计、运营报表、模型网关。
    
*   把知识更新纳入业务流程节点。
    
*   形成年度知识治理机制。
    

**一句管理建议**  
先把“找资料、问问题、复用经验”三件事做好，再逐步把AI从知识查询工具，升级为研发、测试、制造、AE协同的生产力平台。

**技术选型参考来源**

*   [Unstructured 文档解析](https://docs.unstructured.io/open-source/core-functionality/partitioning)
    
*   [PaddleOCR PP-Structure](https://www.paddleocr.ai/v3.1.0/en/version2.x/ppstructure/overview.html)
    
*   [PaddleOCR 表格识别](https://www.paddleocr.ai/latest/en/version2.x/ppstructure/model_train/train_table.html)
    
*   [OpenSearch 混合检索](https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/index/)
    
*   [OpenSearch 文档级权限 DLS](https://docs.opensearch.org/docs/security/access-control/document-level-security/)
    
*   [OpenSearch 字段级权限 FLS](https://docs.opensearch.org/latest/security/access-control/field-level-security/)
    
*   [OpenSearch 审计日志](https://docs.opensearch.org/docs/3.1/security/audit-logs/index/)
    
*   [Elastic Hybrid Search](https://www.elastic.co/docs/solutions/search/hybrid-search)
    
*   [Elastic RRF](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion/)
    
*   [Elastic DLS/FLS](https://www.elastic.co/docs/deploy-manage/users-roles/cluster-or-deployment-auth/controlling-access-at-document-field-level)
    
*   [Milvus Hybrid Search](https://milvus.io/docs/multi-vector-search.md)
    
*   [LangGraph 工作流与Agent](https://docs.langchain.com/oss/python/langgraph/workflows-agents)
    
*   [Keycloak LDAP Federation](https://www.keycloak.org/docs/latest/server_admin/)
    
*   [Ragas 评测](https://docs.ragas.io/en/latest/references/evaluate/)
    
*   [Azure AI Document Intelligence](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/overview?view=doc-intel-3.0.0&viewFallbackFrom=doc-intel-3.1.0)
    
*   [BGE-M3 模型卡](https://huggingface.co/BAAI/bge-m3)
    
*   [GTE Multilingual Base 模型卡](https://huggingface.co/Alibaba-NLP/gte-multilingual-base)
    
*   [GTE Multilingual Reranker 模型卡](https://huggingface.co/Alibaba-NLP/gte-multilingual-reranker-base)
    
*   [vLLM OpenAI Compatible Server](https://docs.vllm.ai/en/v0.14.0/serving/openai_compatible_server/)
    

  

---

## 参考阅读

[《AI领域知识》](https://alidocs.dingtalk.com/i/nodes/YMyQA2dXW792ZO2ZSM67a6QYJzlwrZgb?corpId=dinga5823b9868fcb659&utm_medium=im_card&cid=74801532165&iframeQuery=utm_medium%3Dim_card%26utm_source%3Dim&utm_scene=person_space&utm_source=im)

[《AI基础设施部署方案》](https://alidocs.dingtalk.com/i/nodes/P7QG4Yx2Jp7enzenCB4AXy1XV9dEq3XD?corpId=dinga5823b9868fcb659&utm_medium=im_card&cid=74801532165&iframeQuery=utm_medium%3Dim_card%26utm_source%3Dim&utm_scene=person_space&utm_source=im)

[《AE部门关于AI平台需求》](https://alidocs.dingtalk.com/i/nodes/P7QG4Yx2Jp7enzenCBkYpyNLV9dEq3XD?corpId=dinga5823b9868fcb659&utm_medium=im_card&cid=74801532165&iframeQuery=utm_medium%3Dim_card%26utm_source%3Dim&utm_scene=person_space&utm_source=im)

[DITA-OT documentation](https://www.dita-ot.org/dev/?utm_source=chatgpt.com)

[dita command选项](https://www.dita-ot.org/dev/parameters/dita-command-arguments?utm_source=chatgpt.com)

[使用dita command发布](https://www.dita-ot.org/dev/topics/build-using-dita-command.html?utm_source=chatgpt.com)

[schematron.](https://schematron.com/document/2755.html?utm_source=chatgpt.com)

[Implementation](https://schematron.com/document/390.html?utm_source=chatgpt.com)

## 外部资源

AI辅助工具Text-Well：

[https://www.text-well.com/zh/app: https://www.text-well.com/zh/app](https://www.text-well.com/zh/app)

# AI文档校对工具 (AI-DocProof 1.0)—基于word

[https://github.com/chenningling/AI-DocProof: https://github.com/chenningling/AI-DocProof](https://github.com/chenningling/AI-DocProof)

中**文** markdown 编写格式规范的命令行工具

[https://github.com/lint-md/lint-md: https://github.com/lint-md/lint-md](https://github.com/lint-md/lint-md)

中**文文档**语言规范**检查**工具

[https://github.com/wsdjeg/ChineseLinter.vim: https://github.com/wsdjeg/ChineseLinter.vim](https://github.com/wsdjeg/ChineseLinter.vim)

智能知识管理的**检**索增强生成（RAG）系统

[https://github.com/Zhongye1/KnowledgeRAG-GZHU: https://github.com/Zhongye1/KnowledgeRAG-GZHU](https://github.com/Zhongye1/KnowledgeRAG-GZHU)

Oxygen AI助手自定义插件

[https://github.com/oxygenxml/oxygen-ai-positron-custom-connector-addon: https://github.com/oxygenxml/oxygen-ai-positron-custom-connector-addon](https://github.com/oxygenxml/oxygen-ai-positron-custom-connector-addon)

[https://deepseek.csdn.net/683e56df7e10b149bf1e451a.html: https://deepseek.csdn.net/683e56df7e10b149bf1e451a.html](https://deepseek.csdn.net/683e56df7e10b149bf1e451a.html)

[https://blog.csdn.net/long\_jj/article/details/155103080: https://blog.csdn.net/long\_jj/article/details/155103080](https://blog.csdn.net/long_jj/article/details/155103080)

dify搭建AI问答助手：

[不写代码，用Lighthouse轻松搭建知识库AI问答助手-腾讯云开发者社区-腾讯云](https://cloud.tencent.com/developer/article/2563567)