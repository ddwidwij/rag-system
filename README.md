# RAG 知识库问答系统

基于 ChromaDB + BGE-M3 + BM25（jieba 中文分词）+ GLM-4-flash 构建的混合检索增强生成系统，提供 FastAPI 服务接口与静态前端页面。

## 技术栈

| 层次 | 组件 |
|------|------|
| 向量数据库 | ChromaDB |
| Embedding 模型 | BAAI/bge-m3（支持离线模式） |
| 稀疏检索 | BM25Okapi + jieba 中文分词 |
| 重排序模型 | cross-encoder/mmarco-mMiniLMv2-L12-H384-v1 |
| 生成模型 | ZhipuAI GLM-4-flash |
| 服务框架 | FastAPI + uvicorn |
| 包管理 | uv（Python 3.12+） |

## 目录结构

```
rag/
├── server.py          # FastAPI 主服务入口
├── main.py            # CLI 入口
├── core/
│   ├── config.py      # 全局常量与模型配置
│   ├── parsers.py     # 多格式文档解析与 chunk 分割
│   ├── store.py       # 混合检索核心（向量 + BM25 + 查询扩展）
│   ├── rag_chain.py   # Rerank + 去重 + 生成链
│   └── synonyms.json  # 同义词 / 权重词典
├── tools/
│   ├── synonym_eval.py   # A/B 检索命中率评测脚本
│   └── checker.py        # 文档规范检查工具
├── docs/              # 知识库文档目录
├── chroma_db/         # ChromaDB 持久化数据
├── static/            # 前端静态资源
└── reports/           # 评测报告输出
```

## 快速开始

### 1. 安装依赖

确保已安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)，然后在项目根目录执行：

```bash
uv sync
```

### 2. 配置环境变量

在项目根目录创建 `.env` 文件：

```env
# 智谱AI GLM-4-flash（免费），前往 https://open.bigmodel.cn 获取
ZHIPU_API_KEY=your_zhipu_api_key

# HuggingFace 国内镜像（首次下载模型时使用）
HF_ENDPOINT=https://hf-mirror.com

# 可选：访问 GitHub/HuggingFace 需要代理时配置
HTTPS_PROXY=
```

### 3. 启动服务

**首次运行**（需联网下载模型）：

```bash
source .venv/bin/activate
uvicorn server:app --host 0.0.0.0 --port 8000
```

**离线模式**（模型已缓存，推荐）：

```bash
TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 source .venv/bin/activate && \
uvicorn server:app --host 0.0.0.0 --port 8000
```

服务启动后访问 http://localhost:8000 即可使用前端界面。

### 4. 导入文档

将文档（支持 `.md`、`.txt`、`.pdf`、`.docx`、`.pptx`、`.xlsx`）放入 `docs/` 目录，然后调用导入接口：

```bash
curl -X POST http://localhost:8000/ingest
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/ingest` | 扫描 `docs/` 并导入所有文档 |
| POST | `/query` | 混合检索问答（流式返回） |
| POST | `/upload` | 上传单个文档并导入 |
| GET  | `/health` | 健康检查 |

### 问答请求示例

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query": "常见故障如何排查？", "top_k": 5}'
```

## 检索质量评测

使用内置 A/B 评测脚本对检索命中率进行评估：

```bash
TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 \
python tools/synonym_eval.py --force-regenerate --size 100 --top-k 5
```

评测报告将输出到 `reports/` 目录。

## 同义词配置

编辑 `core/synonyms.json` 可自定义同义词组与词权重，格式示例：

```json
{
  "故障|排障|异常": 1.0,
  "接口|API|端口": 1.0,
  "配置|设置|参数": 0.9
}
```

权重越高的词在混合检索中得分加成越大。