# DocMind 评测与基准

> 当前正式评测：**2026-11-19，118 题，新检索链路**。
> 复现命令仍保留在文末。

## 1. 正式 RAG 评测（118 题）

### 设置

| 项目 | 值 |
|---|---|
| 题集 | `docmind/eval/questions_100plus.json`（118 题） |
| 文档来源 | trace-swe题目.pdf（25）、MCP.pdf（24）、LangGraph教学计划.pdf（24）、实战学习路线.pdf（23）、学习LangChain.pdf（22） |
| 题目生成 | 每份文档均匀抽取 chunk，由 LLM 生成 question + ground_truth |
| 答案模型 | `deepseek-v4-pro` |
| Ragas 评委 | `deepseek-flash` |
| Embedding | DashScope `text-embedding-v4` |
| Rerank | DashScope `gte-rerank-v2`，候选 30 条 |
| chunk | size=800，overlap=80 |
| 请求 top_k | 5 |
| Agent 每轮 tool top_k | 3 |
| 最终上下文 | 全局最多 8 条，每文档最多 3 条，3-gram 去重阈值 0.85 |
| 日期 | 2026-11-19 |
| 正式报告 | `docmind/eval/report.json` |

### 结果

| Questions | Errors | Retrieval Hit | Faithfulness | Answer Relevancy | Context Precision | Context Recall | Avg Latency |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 118 | 4 | 0.9211 | 0.9182 | 0.8943 | 0.8892 | 0.9587 | 10761 ms |

有效样本：

| 指标 | ok | skipped | 实际分母 |
|---|---:|---:|---:|
| faithfulness | 106 | 12 | 106 |
| answer_relevancy | 114 | 4 | 114 |
| context_precision | 106 | 12 | 106 |
| context_recall | 106 | 12 | 106 |

最终上下文：

- 平均 2.97 条，最大 7 条；
- 12 条最终上下文为空；
- 全量并集平均 3.19 条（Agent 多轮检索后未裁剪的总量）。

> Ragas 平均值会忽略 skipped。如果把 12 条 skipped 按 0 计入，四项指标大约为
> faithfulness 0.825、answer_relevancy 0.864、context_precision 0.799、
> context_recall 0.861。正式报告采用“只对有分数的样本求平均”的口径。

### 结论

1. **Context precision 0.8892**：相比旧链路的 30 题样本（0.6531）明显提升，
   主要来自 rerank、每轮 top_k=3 和最终上下文裁剪。
2. **Context recall 0.9587**：仍然较高，但比旧链路略降。
3. **Faithfulness 0.9182 / Answer Relevancy 0.8943**：整体可用，但低于旧链路
   的小样本结果，说明上下文变少会带来少量回答质量波动。
4. **4 条 GraphRecursionError + 12 条零上下文**：这是当前结果最大的质量缺口。
   上下文类指标实际只覆盖 106/118 条，不能把 0.8892 理解为 118 条全覆盖。

### 已知问题

4 条 `GraphRecursionError`：

```text
row 26   在片段3中，Agent 除了本地 Python Tool 外，还可以连接到哪些外部系统？
row 32   关于 MCP Tool 的设计，片段中强调的原则是什么？
row 71   根据文档，第 2 个学习板块“State 状态管理”的实战内容是什么？
row 101  在 trace-swe 中，沿着代码检索、修改、测试，哪些决策由模型做，哪些由程序控制？
```

12 条最终上下文为 0，可分为三类：

- 4 条是上面的 GraphRecursionError；
- 3 条是旧链路有上下文、新链路丢失（row 30、50、106）；
- 5 条在旧链路里也没有检索到（row 9、12、40、75、90），属于基线检索 miss。

低 context precision 题：

```text
row 20   0.000  片段中建议学习 Redis 时应该按照什么顺序进行？
row 25   0.000  片段 2 结尾提出的问题是什么？
row 99   0.000  trace-swe 文档片段提到的哪两个常见踩坑？
row 22   0.250  片段中列出了哪些用户和任务相关的 API 端点？
row 35   0.333  MCP Server 的能力包括哪些？
row 102  0.333  trace-swe 命令、退出状态、标准输出分别在哪里处理？
row 54   0.500  结构化输出部分提到的几种实现思路
row 57   0.500  LangChain Tool 的正确调用链路
```

这些题主要暴露两类问题：

- 上下文从 5 条裁到 3 条后丢掉关键证据；
- rerank 在“片段 2 / 片段 5”这类指代题上选错片段。

## 2. 新旧链路 30 题对比

同一批前 30 题，旧 `rag_rows.json` 与新 `rag_rows_v2.json` 各跑一次 Ragas，
评委均为 `deepseek-flash`。

| 指标 | 旧链路 | 新链路 | 变化 |
|---|---:|---:|---:|
| Errors | 0 | 1 | 变差 |
| Retrieval Hit Rate | 0.9000 | 0.8621 | -0.0379 |
| Avg Latency | 11788 ms | 11377 ms | -411 ms |
| Faithfulness | 0.9636 | 0.9466 | -0.0170 |
| Answer Relevancy | 0.9342 | 0.9195 | -0.0147 |
| Context Precision | 0.6531 | **0.8782** | **+0.2251** |
| Context Recall | 0.9643 | 0.9279 | -0.0364 |
| 平均上下文数 | 6.90 | 3.13 | -3.77 |

配对样本（两边都有分数的题）：

```text
faithfulness       -0.0141
answer_relevancy   -0.0158
context_precision  +0.2224
context_recall     -0.0337
```

需要注意：新链路在这 30 条里有 1 条 GraphRecursionError，上下文类指标有效样本
从 28 条降到 26 条，所以 precision 的提升有一部分来自分母变小。

结论：

- rerank + 上下文裁剪确实提升了 precision，并降低了上下文数量；
- recall、faithfulness、answer relevancy 有小幅下降；
- 1 条递归失败和 1 条额外零上下文抵消了部分收益；
- 30 条样本不足以做统计结论，只能作为方向性信号。

## 3. 复现

```bash
cd docmind

# 1) 从已索引文档生成题集（仓库已提交 questions_100plus.json，可跳过）
../.venv/bin/python eval/generate_questions.py --user-id <uid>

# 2) 跑 RAG，先落盘 answer / contexts；不调用 Ragas
../.venv/bin/python eval/evaluate.py \
  --user-id <uid> \
  --questions eval/questions_100plus.json \
  --top-k 5 \
  --rows-out eval/rag_rows_v2.json \
  --report /tmp/report_rag_only.json \
  --skip-ragas

# 3) 复用 RAG rows，只跑 Ragas
../.venv/bin/python eval/evaluate.py \
  --rows eval/rag_rows_v2.json \
  --report eval/report.json \
  --judge-model deepseek-flash \
  --batch-size 5
```

说明：

- 评分按指标分批，每批写入 `ragas_checkpoint_*.jsonl`；
- 中断后重新执行同一条命令会从 checkpoint 续跑；
- `eval/rag_rows*.json` 和 checkpoint 是本地产物，已加入 `.gitignore`；
- 正式报告 `eval/report.json` 提交在仓库中。

历史检索矩阵（hit@4 / hit@1 / MRR / chunk size）来自旧链路，未在本次新链路
复测，因此不再把旧数字作为当前结论。需要复测时仍可运行：

```bash
../.venv/bin/python eval/benchmark.py
../.venv/bin/python eval/benchmark.py \
  --sizes 500 --modes vector,hybrid,rewrite \
  --question-indices 0,3,6,7 --with-ragas
```

## 4. 局限

- 题目由已索引 chunk 反向生成，通常比真实用户问题更容易；适合工程回归，
  不能直接代表业务语料效果。
- Ragas 是 LLM judge，不是人工标注；`deepseek-flash` 的判断与
  `deepseek-v4-pro` 也可能不同。
- 4 条错误 + 12 条跳过，上下文指标只覆盖 106/118 条。
- 30 题新旧对比不是统计显著结论；precision 提升的有效样本还比旧链路少 2 条。
- 5 条基线零上下文需要单独判断是“文档没有”还是“检索没召回”，不能靠
  强行补上下文掩盖。
- 正式报告里的 `details` 包含逐题 answer / contexts / metrics；
  `rag_rows_v2.json` 与 checkpoint 不提交，避免仓库体积膨胀。
