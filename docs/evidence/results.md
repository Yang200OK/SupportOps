# 实测结果与失败解释

## 来源与分母

冻结清单：[manifest.json](../../data/evaluations/final-v1/manifest.json)；协议修订：[harness-amendment.json](../../data/evaluations/final-v1/harness-amendment.json)；原始报告与 SHA-256：[source-manifest.json](source-manifest.json)；汇总：[summary.json](../verification/phase-8-round-1/summary.json)；用量：[model-usage.json](../verification/phase-8-round-1/model-usage.json)。

12 检索题 × 6 策略 × 2 重复 = 144；4 回答题 × 2 入口 × 2 重复 = 16；4 调查题 × 3 方案 × 2 重复 = 24，共 184 槽位。公开构造资料与初稿标签不是人工金标准；检索偏向少量熟悉家族 / 重复版本，初稿 Recall@5 理想上限约 0.944，重复不等于独立样本。

原回答与调查使用 qwen-plus-2025-07-28；当前主模型 qwen3.6-plus 仅有独立配置补充。embedding / rerank 分别调用，BM25 零模型。

## 完整结果

检索 143/144 完成；RRF + rerank 的 MODEL_TIMEOUT 留在分母。Vector / RRF / RRF-parent Recall@5 约 0.8681，rerank 两组约 0.6042 / 0.6250，本批没有覆盖收益。

回答直接 / 分步各 8/8，规则事实各 20/20，返回引用 35 / 23。依据不足声明 18/35 / 3/23 属于不同输出集合，不能合成统一准确率。结构完成、引用合法与语义正确分开报告。

调查单 3/8、经验 1/8、多 Agent 2/8，合计 6 完成 / 16 failed / 2 stopped。六次准备失败确定零模型；未移除它们或用补充验证覆盖原成绩。

| 失败边界 | 原样保留 | 影响 |
| --- | --- | --- |
| 实验准备 | 首次错误、零模型 | 流程未完成，不等于推理失败 |
| 无效结构 | MODEL_RESPONSE_INVALID | 有输出不代表契约完成 |
| 缺观测 | HYPOTHESIS_OBSERVATIONS_REQUIRED | 文档不能替代实例取证 |
| 预算耗尽 | stopped 与原因 | 多角色开销可能增加 |
| 依赖失败 | DEPENDENCY_FAILED | 归并不能虚构结果 |
| 模型超时 | 失败和未知用量 | 超时不证明未计费 |
| 支持不足 | 声明与评审记录 | 结构通过不证明语义正确 |

同预算开发实验单 3/4、多 Agent 2/4；另一个经验开发对照无记忆 3/4、有记忆 2/4。数据与协议不同，不能拼成统一成绩，未证明总体收益。

## 当前模型补充

[configuration-repair/agents.json](../verification/phase-8-round-1/configuration-repair/agents.json) 保存 qwen3.6-plus 六次配置验证：3 完成、3 失败，28 已知 / 2 未知调用，已知输入 100637 / 输出 15032 token。范围不同，不宣称改善原 holdout。用量账本分组记录原实验、探测和补充；费用 null，尚未对账。

## 复现层级

1. Fake / Mock：验证契约、权限、引用与恢复。
2. 独立工程复现：真实 PostgreSQL、HTTP、Chromium、重启，零模型。
3. 历史在线验证：真实模型 / MCP / 隔离实验，保留当时参数与失败。
4. 未验证：远端 Actions、第二台机器、生产并发 / SLA、人工语义金标准、公网部署。

历史报告绑定旧 UUID，仅供审阅，全新初始化不导入旧数据库身份。
