# DocMind 检索 / 切块基准

本文记录 P1-06 的实验设置、真实结果和结论。数字来自本机 PostgreSQL 18.4 +
pgvector + DashScope `text-embedding-v4` + DeepSeek。

## 1. 实验设置

| 项目 | 值 |
|---|---|
| 语料 | `docmind/eval/benchmark_corpus.txt`（由 22 页课程 PDF 提取，25,033 字符） |
| 问题数 | 8（6 条单轮 + 2 条带对话历史的指代问题） |
| chunk size | 300 / 500 / 800 |
| chunk overlap | size / 5（60 / 100 / 160） |
| top_k | 4 |
| 检索模式 | `vector` / `hybrid` / `rewrite` |
| 向量库 | 独立评测库 `docmind_eval`，不碰开发库 `docmind` |
| 检索指标 | hit@4、hit@1、MRR、平均检索延迟 |
| 答案质量 | Ragas 四项指标，在 4 题子集上运行（见第 3 节） |

补充说明：

- 检索矩阵的答案生成使用**固定 Prompt**，不经过 LangGraph Agent 的工具选择，
  这样不同检索模式之间的差异只来自检索本身；`eval/evaluate.py` 才走完整 Agent。
- hit@4 / hit@1 / MRR 用 `expected_key` 子串匹配计算，是自动代理指标，不等同于
  人工相关性标注。

复现命令：

```bash
cd docmind

# 检索矩阵：3 种 chunk size × 3 种模式 × 8 题
../.venv/bin/python eval/benchmark.py

# Ragas 子集：chunk=500，三种模式，4 题（含 2 条多轮）
../.venv/bin/python eval/benchmark.py \
  --sizes 500 --modes vector,hybrid,rewrite \
  --question-indices 0,3,6,7 --with-ragas \
  --report eval/benchmark_report_modes.json

# Ragas 子集：chunk=300/800，vector，4 题
../.venv/bin/python eval/benchmark.py \
  --sizes 300,800 --modes vector \
  --question-indices 0,3,6,7 --with-ragas \
  --report eval/benchmark_report_sizes.json
```

## 2. 检索矩阵结果

hit@4 在所有 cell 上都是 1.0，区分度不够，因此同时记录 hit@1 和 MRR：

| chunk | mode | chunks | hit@4 | hit@1 | MRR | 平均检索延迟 |
|---:|---|---:|---:|---:|---:|---:|
| 300 | vector | 110 | 1.00 | 0.50 | 0.73 | 157 ms |
| 300 | hybrid | 110 | 1.00 | 0.38 | 0.62 | 199 ms |
| 300 | rewrite | 110 | 1.00 | 0.50 | 0.73 | 1617 ms |
| 500 | vector | 68 | 1.00 | **0.88** | **0.94** | 162 ms |
| 500 | hybrid | 68 | 1.00 | 0.88 | 0.91 | 202 ms |
| 500 | rewrite | 68 | 1.00 | 0.88 | 0.94 | 1241 ms |
| 800 | vector | 46 | 1.00 | 0.62 | 0.79 | 141 ms |
| 800 | hybrid | 46 | 1.00 | 0.75 | 0.88 | 201 ms |
| 800 | rewrite | 46 | 1.00 | 0.62 | 0.79 | 1186 ms |

### 结论

1. **chunk_size=500 在这份语料上排序质量最好**：hit@1 0.88、MRR 0.94。300 过度切分
   （110 个 chunk），hit@1 降到 0.50；800 欠切分（46 个 chunk），hit@1 0.62。
   这说明 chunk size 不是越小越好，也不是越大越好，需要按语料和问题类型调。
2. **加入中文子串兜底后，hybrid 开始部分生效，但仍不是稳定赢家**。
   pg_trgm 对中文不生成 trigram（`show_trgm('板块二')` 返回空数组），
   `tsvector('simple', ...)` 也不做中文分词。因此 `hybrid_search` 的关键词一路
   现在优先用 `position(lower(q) in lower(content))` 做中文/精确子串匹配，再用
   `word_similarity` 兜底英文。效果：
   - chunk=800：hybrid hit@1 0.75 > vector 0.62；
   - chunk=500：两者持平（0.88）；
   - chunk=300：hybrid 仍低于 vector（0.38 vs 0.50），过细切分 + RRF 融合会打乱排序。
   结论：中文关键词兜底是必要的，但 RRF 不保证每个 cell 都提升；300 chunk 需要
   单独调参，或者引入中文分词（`zhparser` / `pg_jieba`）。
3. **rewrite 不再是排序损失，但仍有明显延迟成本**。只有 2/8 条问题带历史，其余
   6 条 rewrite 是 no-op；平均检索延迟从 ~150–200 ms 升到 ~1.2–1.6 s，因为多轮
   问题会调用一次 LLM 做改写。在这 2 条指代问题上：
   - chunk=500：两条都排到 rank 0；
   - chunk=300：一条 rank 1、一条 rank 0；
   - chunk=800：一条 rank 0、一条 rank 1。
   聚合 hit@1 与 vector 持平。说明改写没有明显提升，但也不像上一版那样伤害排序；
   要判断它的价值，需要更大的多轮问题集。

## 3. Ragas 答案质量子集

为了控制评委调用量，Ragas 只在 4 条问题上运行：题目下标 `0,3,6,7`
（2 条单轮 + 2 条带历史的指代问题），chunk=500，top_k=4。

| mode | hit@4 | hit@1 | MRR | 检索延迟 | 答案生成延迟 | Faithfulness | Answer Relevancy | Context Precision | Context Recall |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| vector | 1.00 | 1.00 | 1.00 | 172 ms | 2326 ms | 1.000 | 0.838 | 0.917 | 1.00 |
| hybrid | 1.00 | 1.00 | 1.00 | 207 ms | 2574 ms | 1.000 | **0.887** | 0.806 | 1.00 |
| rewrite | 1.00 | 1.00 | 1.00 | 1881 ms | 2526 ms | 1.000 | 0.696 | **0.938** | 1.00 |

> 这是 P1-08 中文关键词兜底之后的重跑结果。chunk-size 子集是另一次运行；
> Answer Relevancy 在不同运行间有波动（约 0.70–0.95），4 题 + LLM judge 的
> 噪声很明显，不能逐位比较。

### 结论

1. 在这 4 条问题上三种模式的 hit@1 都是 1.0，检索层没有拉开差距；差异主要来自
   答案质量和延迟。
2. **这一轮 hybrid 的 Answer Relevancy 最高**（0.887），vector 0.838，rewrite
   0.696；但上一轮 vector 更高（0.918）。两轮排序不一致，说明 4 题子集的
   LLM judge 噪声很大，不能据此断言哪种模式更好。
3. rewrite 的 Context Precision 最高（0.938），vector 0.917，hybrid 0.806；
   hybrid 偏低与关键词/RRF 扰动排序的观察一致。
4. rewrite 的检索延迟约 1.9 s，是 vector 的 10 倍以上；在这个子集上，
   它没有稳定带来答案质量收益，主要成本是多一次 LLM 改写调用。
5. 结论：**当前证据不足以证明 hybrid/rewrite 优于 vector**；vector 仍是最便宜、
   最稳的默认选择。要做统计上可信的结论，需要更大的问题集和多轮样本。

### chunk size 的答案质量（同样 4 题，vector）

| chunk | chunks | hit@1 | MRR | 检索延迟 | 答案生成延迟 | Faithfulness | Answer Relevancy | Context Precision | Context Recall |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 300 | 110 | 0.50 | 0.71 | 183 ms | 2855 ms | 1.000 | 0.916 | 0.792 | 1.00 |
| 500 | 68 | 1.00 | 1.00 | 151 ms | 2211 ms | 1.000 | 0.918 | 0.917 | 1.00 |
| 800 | 46 | 0.75 | 0.88 | 318 ms | 2737 ms | 1.000 | **0.950** | **0.958** | 1.00 |

> 注意：Ragas 子集只有 4 题，hit@1 / MRR 的分母和第 2 节的 8 题矩阵不同，
> 不要直接跨表比较绝对数值。

补充结论：

- **300 明显过度切分**：110 个 chunk，hit@1 只有 0.50，Context Precision 降到
  0.792；碎片化让检索排序和上下文质量都变差。
- **500 的检索排序最好**（hit@1 1.0、MRR 1.0），答案生成也最快（2211 ms），
  是这组实验里综合最稳的配置。
- **800 的 Answer Relevancy / Context Precision 略高**（0.950 / 0.958），可能因为
  更大的 chunk 给生成器提供了更完整的上下文；但 hit@1 只有 0.75，检索排序不如
  500。4 题的差异在噪声范围内，不能据此断言 800 更好。
- 项目当前 `.env` 是 `CHUNK_SIZE=800`。这组实验提示 **500 可能更适合“检索优先”的
  场景**；建议用真实业务语料和更大的问题集复测后再改默认值，不要只凭 4 题改配置。

## 4. 局限

- 语料是从课程 PDF 提取的对话式文本，不是精修业务文档；问题数只有 8 条，
  结论应视为方向性参考，不是统计显著结论。
- 没有统计 token 成本：DocMind 还没有 LLM usage 记账（P1-09），当前用延迟作为
  成本代理。
- rewrite 的多轮样本只有 2 条，样本量不足以判断改写策略的普遍效果。
- 评测库和开发库分开，但每次实验都会重建索引；不同 chunk size 的索引不能同时
  存在。
- Ragas 子集不是全部 9 个 cell，避免评委调用量过大；检索指标覆盖全部 cell。
- hit@k / MRR 的 `expected_key` 子串匹配只是代理指标；它可能在同一 chunk 中出现
  多次，也无法判断片段是否真正支持答案。Ragas 的 context 指标由 LLM 判断，
  但只有 4 题子集。
