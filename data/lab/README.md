# RelayDesk 首批实验与标注任务

2026-10-04，独立 Docker 项目 supportops-lab；三版本、四故障家族、两参数组，共 24 个实验，每个包含正常 / 故障 / 恢复复测。使用真实 PostgreSQL、Redis 与 HTTP 接收器，无模型调用。

| 文件 | 内容与边界 |
|---|---|
| manifest.json | 24 包身份 / 版本 / 三阶段状态、规范包摘要、实际 JSONL 字节摘要、默认值、数据集摘要 |
| observations/*.json | 严格公共观测，UTC 排序，无标签 / 控制方式，可上传 SupportOps |
| observations/*.jsonl | 同一批规范化事件加 lab:{run_uuid}:{index}，不是 stdout 字节副本 |
| evaluation-dataset.v1.json | 24 个构造工单、来源分组 / split / expected；模型只读 model_input() 的 task_id / input |
| evaluation-labels.v1.json | 受控条件得到的根因 / 修复 / 证据，human_reviewed=false，仅用于人工复核 / 评测 |
| runtime-contract-checks.json | 三版本默认值、常规更新 / 手工清理实测，明确未测生产性能 / SLA / 分布式一致性 |

374 条观测包括 252 产品事件、116 接收器事件、6 启动校验。每套矩阵为 66 次 HTTP 投递 + 6 次失败启动；配置阶段 422 是启动观测状态，真实退出码 2。工单由实际实验整理，source_type=synthetic_case，不是真实用户上报。

原始容器 JSON 行字节 / SHA / 镜像 ID 在 ../../docs/verification/phase-2-round-3。首批六次启动失败只保留规范化观测；runtime-regression 另存最终容器复跑全部 24 实验的日志与六份原始 boot stdout。两套 UUID 不同，复跑未替换冻结包或标签。scripts/verify-lab-evidence.py 核对首批服务事件一致，并从公开最终日志重建 24 包、核对摘要。

18 dev 为配置 / 连接池 / 下游完整家族，6 holdout 为缓存完整家族，三版本和参数变体不跨分区。第一批集小且家族分布不均衡，不代表最终评测或泛化质量；标签尚未人工语义复核，不能称为 24 个高质量人工金标准。

未来调查 / 评测采用 EvaluationTask.model_input()，公共观测按 run UUID 关联。普通 API 拒绝 expected / split / fault_family / 控制参数。文件分开不自动等于权限隔离，不能把整个仓库纳入检索。上传摘要一致只证明字面完整，execution_verified_by_api=false。

先按根 README 启动实验，再从项目根执行：

```powershell
.\.venv\Scripts\python.exe scripts\run-lab.py --output outputs\lab-new-run
.\.venv\Scripts\python.exe scripts\verify-lab-evidence.py
```

run-lab 拒绝覆盖已有 manifest，异常保存 status=failed 并停止，恢复失败不能标记通过；结束恢复默认 1.1 / current / 零延迟并释放占用。仅一个控制器顺序运行，避免全局实验条件竞争。新输出不覆盖冻结包，verify-lab-evidence 核对已归档公开证据。凭据在 ignored .env.lab；新实验输出在项目内 ignored outputs，归档前检查内容。

来源为自建演示产品，无外部客户日志或凭据。详见 ../../docs/specs/phase-2-round-3/acceptance.md。
