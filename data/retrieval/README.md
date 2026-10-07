# 精确向量检索基线

2026-10-05 用户明确授权后，baseline-v1 已生成：真实 qwen3.7-text-embedding / 1024 维，128 个资料切片 + 374 条公开观测，共 502 条证据，51 批 / 58777 输入 token。数据库测试替身与本次真实模型结果分别记录。

准备脚本只选择 data/relaydesk/manifest.json 的 16 份 CC0 自建资料与 data/lab/manifest.json 的 24 个公共构造观测包。生成固定快照后才在评测侧读取既有 expected 锚点。检索 API 和 embedding 文本不含故障标签、根因或修复答案。

已生成 48 个初稿问题：24 个文档问题、24 个日志锚点定位问题。36 dev 包含配置 / 连接池 / 下游家族，12 holdout 为缓存家族；同家族的版本与参数变体不跨分区。日志问题使用工单已知观测包范围，不能声称全库定位或根因调查质量。所有标签 human_reviewed=false，章节规则与实验锚点初稿尚需人工语义复核，尤其终态查询与多条调查锚点的相关性粒度。

冻结文件：snapshot.json（知识身份、逐条来源与建库 HTTP 耗时）、dataset.json（问题和范围）、qrels.json（独立 graded 标签）。dataset 与 qrels 均有规范 JSON SHA；评测验证全部锚点属于固定知识快照，不覆盖旧结果。

从项目根复现，新的准备 / 评测结果必须使用未存在的目录 / 文件：

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe scripts\prepare-retrieval.py --output-dir data\retrieval\baseline-v1
.\.venv\Scripts\python.exe scripts\evaluate-retrieval.py --dataset-dir data\retrieval\baseline-v1 --split dev --output docs\verification\phase-3-round-1\evaluation-dev.json
```

需先启动本机 API、数据库与演示账号。会真实调用百炼 embedding、产生用量，费用以服务商账单为准。脚本拒绝覆盖；重建使用新目录，知识 UUID 会改变，需要重新绑定标签。不要运行 holdout 来调参；当前分区规模与模板重复性不支持泛化结论。

首轮 dev 报告在 docs/verification/phase-3-round-1/evaluation-dev.json：36 / 36 请求完成，1167 输入 token；Recall@5=0.843、MRR@5=0.825、nDCG@5=0.830，端到端 HTTP P95=2741.853 ms；该次运行与 5 个 smoke 查询有时间重叠，不能当作隔离性能基准。18 文档问题 Recall@5=0.972、18 日志问题=0.713；11 个任务未取全初稿锚点。失败分析见同目录 failure-analysis.json，未按结果改标签或向量基线。费用为 null，尚未核对服务商账单；12 holdout 未运行。
