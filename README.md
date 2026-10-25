# WorkLoom

WorkLoom 是一个双服务 AI 工作台：把任务管理和企业知识检索放在同一个平台里。

- **TaskFlow**：任务管理 + LLM 助手（JWT、Redis 缓存、结构化输出、Tool Calling）
- **DocMind**：企业知识库 RAG（pgvector、混合检索、LangGraph ReAct Agent）
- **shared**：两个服务共用的配置、DB、LLM 客户端、鉴权与异常处理

> An AI workspace that connects task automation and enterprise knowledge retrieval.

## 快速启动

```bash
cp .env.example .env       # 填入 LLM_API_KEY / EMBED_API_KEY / JWT_SECRET_KEY
make up                    # 构建并启动 postgres / redis / 两个 api
make migrate               # 两个库分别执行 alembic upgrade head
make smoke                 # 端到端冒烟
```

启动后：

- TaskFlow Swagger: http://localhost:8000/docs
- DocMind Swagger: http://localhost:8001/docs

## 本地开发（不用 Docker 跑 API）

```bash
make install
cp taskflow/.env.example taskflow/.env
cp docmind/.env.example docmind/.env
# 填入真实 Key / 数据库密码

# 方案 A：本机已有 PostgreSQL（当前配置）→ 只起 Redis（可选）
docker compose up -d redis-taskflow redis-docmind
# 方案 B：没有本机 PostgreSQL → 起 compose 的数据库
# docker compose up -d postgres-taskflow redis-taskflow postgres-docmind redis-docmind

cd taskflow && ../.venv/bin/alembic upgrade head && ../.venv/bin/uvicorn app.main:app --reload
cd docmind  && ../.venv/bin/alembic upgrade head && ../.venv/bin/uvicorn app.main:app --reload --port 8001
```

`.env` 已被 `.gitignore` 忽略，不要提交；`.env.example` 只放占位值，可以提交。

## 鉴权

DocMind 的 `/documents`、`/rag`、`/conversations` 都要求 TaskFlow 签发的 JWT：

```bash
# 先注册（如果还没有用户），再登录拿 token
curl -X POST http://localhost:8000/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"FastAPI123"}'
```

把返回的 `access_token` 填到 DocMind Swagger 的 **Authorize** 按钮里。
两个服务必须配置同一个 `JWT_SECRET_KEY`；DocMind 只验签，不查询 TaskFlow 的数据库。

## 服务互联

除了共享 JWT 契约，两个服务还通过 HTTP 互相调用：

- **DocMind `POST /retrieval/search`**：纯检索接口，只返回片段、不生成答案，
  支持 `vector` / `hybrid` 两种模式。比 `/rag/query` 少一次 LLM 调用，
  适合调用方自己处理片段的场景。
- **TaskFlow `GET /tasks/{id}/knowledge`**：任务详情「相关知识」，
  用任务标题 + 描述去知识库检索，返回带文件名 / 页码 / 分数的片段。
- **TaskFlow AI 助手新增 `search_knowledge` 工具**：在 `POST /ai/chat/tools`
  的对话里，模型可以一边查知识库一边操作任务，回答时注明来源文件名。
- **任务 ↔ 文档关联**：`GET/POST /tasks/{id}/documents`、
  `DELETE /tasks/{id}/documents/{document_id}`。只存 DocMind 的
  `document_id` 整数，跨库不建外键；重复关联是幂等的。
  `GET /tasks/{id}/knowledge?linked_only=true` 只在这些关联文档里检索。
- **文档生成任务（带引用）**：`POST /ai/tasks/from-document`。
  流程是「校验文档归属 → 在该文档内检索行动项片段 → LLM 结构化提取 →
  一次性写入任务 + 关联」。每条任务带 `source_document_id` /
  `source_filename` / `source_chunk_index`，可追溯到原文片段；
  LLM 返回的 `source_chunk_index` 会被校验，不存在于本次片段的引用置空。

调用方向固定为 **TaskFlow → DocMind**：DocMind 的 LangGraph 是模块级单例、
工具在 import 时静态绑定；TaskFlow 的工具本来就按请求构建，扩展成本更低。

服务间鉴权直接透传用户自己的 Bearer token（`AccessTokenDep`），DocMind
验签后拿到的 `uid` 和 TaskFlow 完全一致，用户隔离不变。

本地直跑时 TaskFlow 需要 `DOCMIND_BASE_URL`（默认 `http://localhost:8001`）；
compose 已内置 `http://api-docmind:8001`。

## 测试

```bash
make test
```

## 评测

```bash
make eval
# 或者分步执行：
cd docmind
../.venv/bin/python eval/index_sample.py
../.venv/bin/python eval/evaluate.py --limit 2
```

- `eval/index_sample.py` 会把 `eval/sample_corpus.txt` 复制到 `docmind/data/` 并建立索引；
- `eval/evaluate.py` 调用真实 RAG 链路，再用 Ragas 计算 faithfulness / answer relevancy / context precision / context recall；
- 会真实调用 LLM 和 Embedding，产生少量费用；`--skip-ragas` 可只跑检索与延迟；
- 结果写入 `eval/report.json`（已加入 `.gitignore`）。

示例语料（4 题）上的结果，仅用于验证评测链路，不代表真实业务语料效果：

| Questions | Retrieval Hit | Faithfulness | Answer Relevancy | Context Precision | Context Recall | Avg Latency |
|---:|---:|---:|---:|---:|---:|---:|
| 4 | 1.0 | 0.95 | 0.9228 | 1.0 | 1.0 | 3526 ms |

换成真实文档时，修改 `eval/questions.json` 里的 question / ground_truth / expected_source 即可。

## 基准

```bash
make benchmark
# 或手动跑 Ragas 子集：
cd docmind
../.venv/bin/python eval/benchmark.py \
  --sizes 500 --modes vector,hybrid,rewrite \
  --question-indices 0,3,6,7 --with-ragas
```

`make benchmark` 只跑检索矩阵（hit@4 / hit@1 / MRR / 延迟），不生成答案；
加 `--with-ragas` 才会调用评委模型。实验使用独立评测库 `docmind_eval`，
不会碰开发库。

完整的方法、真实结果和结论见 [docs/benchmarks.md](docs/benchmarks.md)。

## 可观测性与索引

- 上传接口立即返回 `pending`，解析/切分/Embedding 在后台执行；用
  `GET /documents/{id}` 轮询状态。
- 索引替换是原子操作：删旧 chunk + 写新 chunk + 更新状态在同一个事务里；
  `document_chunks(document_id, chunk_index)` 有唯一约束，重跑索引不会重复写入。
- `document_chunks` 已建 HNSW 向量索引、GIN 全文索引、GIN trigram 索引和
  `(user_id, document_id)` 复合索引。
- `/health` 是存活检查，`/health/ready` 会检查数据库；compose healthcheck
  和 smoke 使用 readiness。
- 每个响应带 `X-Request-ID`，请求日志包含 method/path/status/duration_ms/request_id。
- DocMind 每次 LLM 调用记录 token/latency；一轮 RAG 汇总 usage。配置
  `LLM_INPUT_PRICE_PER_MILLION` / `LLM_OUTPUT_PRICE_PER_MILLION`（USD/百万 token）
  后会额外估算成本。
- 本地排查 SQL 时设置 `DB_ECHO=true`；默认关闭。

## 说明

- 根目录 `compose.yaml` 是唯一的全栈编排入口；`taskflow/`、`docmind/` 下不再保留各自的 compose 文件。
- 两个服务数据库独立，不共享表、不建跨库外键；用户身份通过 JWT 契约传递。
- `.env` 不入库；密钥只通过环境变量注入。
