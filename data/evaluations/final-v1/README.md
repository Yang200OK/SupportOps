# 最终冻结评测

当前运行主模型已按用户要求更换为 qwen3.6-plus；本目录原冻结与 184 个槽位是在 qwen-plus-2025-07-28 下完成，仍保持原身份和摘要。后续六次配置调查在独立新模型冻结中记录，见 docs/verification/phase-8-round-1/configuration-repair/；不并入原成功率，也不构成新模型整体效果比较。原运行器会拒绝当前模型配置的漂移，这是保留历史边界的预期行为。

本轮开始时原检索 12 个 holdout、回答 v2 的 4 个 holdout 均未执行。manifest.json 保存原任务 / 标签和旧报告逐任务状态审计，不替换先前分区或初稿标签。检索与回答共用 502 条既有来源、原 UUID 和模型配置；知识语料一直可见，因此这不是未见知识测试。

调查四题在本轮首次模型调用前创建并冻结，同一工单用于两次重复和三个流程，每次重新准备实际故障 / 登记范围。configuration、pool、cache、downstream 全部是已开发的家族；这是预先登记的新措辞任务，非未见故障家族。记忆快照为冻结时已有合格候选，不在评测后新建 / 改写候选。

固定两次重复，共 184 槽位：检索 12 × 6 × 2 = 144，回答 4 × 2 × 2 = 16，调查 4 × 3 × 2 = 24。第一轮按题轮转组顺序，第二轮反向；所有失败 / 停止 / 传输未知 / 未执行保留。独立重复不代替足够的独立家族样本，不计算显著性或人工准确率。

运行（项目根目录，使用本项目 .venv）：

```powershell
$env:PYTHONUTF8='1'
.venv/Scripts/python.exe scripts/freeze-final-evaluation.py
.venv/Scripts/python.exe scripts/run-final-evaluation.py --suite retrieval
.venv/Scripts/python.exe scripts/run-final-evaluation.py --suite answers
.venv/Scripts/python.exe scripts/run-final-evaluation.py --suite agents
.venv/Scripts/python.exe scripts/report-final-evaluation.py
.venv/Scripts/python.exe scripts/report-final-evaluation.py --readback before-restart
# 实际重启项目 API 后执行；不重新派发模型或故障。
.venv/Scripts/python.exe scripts/report-final-evaluation.py --readback after-restart
```

既有文件会拒绝再次执行或覆盖。这些命令说明原运行协议；重复复现与新环境初始化属于下一轮，需要新的明确批次 / 数据身份，不能清空原报告来重试。每个报告保存全部预登记槽位，写入 request_started 后才派发，再保存原 API 响应和付费计量；临时文件同目录原子替换。断线没有服务端用量时单列未知管道，不猜测内部调用次数。

仅检索 rerank / parent 四组是受控组件消融；vector / bm25 / rrf 是检索模式对照。direct / guided 的规划、检索次数与输出协议不同，属于完整流程对照。single / memory 共享原上限，记忆占用其中的上下文；single / multi 还存在检索窗口、输出目标、公共信封与静态私有配额差异。整体较少时延 / token 可能来自失败早停，不直接称收益。

检索 144 槽位结束后、回答与调查开始前，按既有 API 协议修正运行脚本对多 Agent 字符串错误的读取。manifest.json 未重写，runner-initial.py.txt 保留首次冻结源码，harness-amendment.json 记录原 / 新文件字节 SHA、修订时间、完整检索报告 SHA 与修订原因。后两套报告显式绑定该修订，逐文件检查只接受这一个已记录的评测器差异；所有应用源码、模型配置、任务 / 标签和请求预算继续按原冻结验证。这是离线计量契约修正，不根据效果改进推理或重跑失败。

标签只在响应结束后评分，不进入任何请求。原文核对与模型语义支持分开，规则事实覆盖只在同条结论中匹配，模型评审不是人工金标准。费用和人工根因准确率保持 null。当前轮结果为本地合成实验，不能证明生产 SLA、线上泛化或实际实例的唯一根因。
