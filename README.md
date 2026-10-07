# SupportOps

**企业技术支持与故障排查：文档、工单、日志联合分析。**

SupportOps 将版本知识库、现场证据、RAG 回答、受控调查 Agent、人工批准动作和经验治理放在同一支持工作台。面对“请求超时”，先核对产品版本和原文，再读取本次实例状态，保留支持 / 反驳证据并输出可复核结论；条件不足时保留未决与失败。

实验对象是自建产品 **RelayDesk**，包含 1.0 / 1.1 / 2.0 三个版本和配置、连接池、缓存、下游四类故障。资料与工单为公开构造材料，现场观测来自本地隔离实验。当前支持本地运行，没有公网应用地址或生产 SLA。

![支持工作台](docs/images/workbench.png)

[功能与页面](#功能与页面) · [系统架构](#系统架构) · [实测结果](#实测结果) · [本地运行](#本地运行) · [技术栈与源码](#技术栈与源码)

## 核心能力

| 能力 | 实际行为 |
| --- | --- |
| 版本化知识库 | Markdown / JSON / PDF，原文字节、SHA-256、修订、章节父块与引用位置 |
| 混合检索 | 精确向量、BM25、RRF、rerank、有界父块扩展，固定组织 / 产品 / 版本范围 |
| 可信回答 | 结构化结论与逐条引用，区分引用存在、原句一致与语义支持，直接 / 分步独立入口 |
| 单 Agent | LangGraph 有界循环、真实 MCP、白名单观察工具、假设与检查计划、证据回查 |
| 人工批准动作 | 绑定实例 / 证据 / 前状态，审批、执行、回执、取消与显式恢复，未知状态停止 |
| Skill 与经验 | 目录 / 正文渐进加载，事件复盘、冲突 / 失效治理、回归后审批与发布 |
| 多 Agent | 任务 DAG、两调查角色、私有 MCP / 上下文、共享预算、归并与未决冲突 |
| 评测与审计 | 完整分母、真实失败、未知用量、原文摘要、独立数据库与浏览器验证 |

## 功能与页面

以下是实际本地页面。“历史实测”图片保留当时模型和结果，没有修改画面或用当前模型重跑。新截图读取原演示库，其中浏览器验收资料与失败记录也如实展示。[图片来源清单](docs/images/manifest.json)保存文件身份与摘要。

### 1. 登录与工单

登录后工单、资料、运行和经验均在当前组织范围内访问。初始化随机生成口令，公开仓库不提供固定口令。工单保留版本、现象与缺失信息；输入不足生成持久化 blocked 记录，不直接确认根因。

<details>
<summary>登录页面</summary>

![登录](docs/images/login.png)

</details>

![工单详情](docs/images/ticket-details.png)

### 2. 知识与资料

导入后核对来源 / 许可、格式、解析状态和修订。解析失败与旧修订继续可见；更新资料不会替换历史引用绑定的原文。

![知识资料列表](docs/images/documents.png)

<details>
<summary>来源身份、结构切片与原文引用</summary>

![来源与修订](docs/images/document-revisions.png)

结构切片保留章节父块、配置表与代码。下面展示结构解析验收资料的旧修订及历史原文引用页面。

![结构切片历史页面](docs/images/chunks-history.png)

![逐块原文引用历史页面](docs/images/citation-history.png)

</details>

### 3. RAG 回答与分步追问

在固定组织和版本中检索，输出结构化事实、引用与缺失条件。分步入口支持改写、澄清和追加检索，保持检索次数、调用与时间上限。截图只提取字段，由用户确认后再进入后续流程。

以下为历史真实模型验证，模型身份以画面内原记录为准，不代表 qwen3.6-plus 的质量成绩。

![直接回答历史实测](docs/images/answer-history.png)

<details>
<summary>分步回答与截图字段确认</summary>

![分步回答历史实测](docs/images/guided-history.png)

![截图字段确认历史实测](docs/images/screenshot-history.png)

</details>

### 4. 现场调查与动态假设

调查固定任务与证据范围，再选择观察工具。文档说明设计行为，当前日志说明实例实际行为；症状词与经验不能代替本次取证。动态假设保留支持 / 反驳 / 未决状态及后续检查。

![假设调查入口](docs/images/hypothesis-setup.png)

<details>
<summary>假设、检查步骤与历史只读调查</summary>

![假设与检查步骤](docs/images/hypothesis-history.png)

下面是历史真实模型 / MCP 只读调查。过期实验身份不会自动复用成新现场。

![只读调查历史实测](docs/images/investigation-history.png)

</details>

### 5. 人工批准与动作回执

用户审批前查看建议、证据、参数和前状态；执行前再核对身份与状态。已 completed 的回执恢复时直接读回；started 后缺少完成回执标记 uncertain 并停止，避免重复副作用。

下图为历史真实实验动作的批准与回执，仅控制隔离 RelayDesk 实验环境。

![批准动作历史实测](docs/images/actions-history.png)

### 6. Skill、复盘与组织方法

Skill 从目录 / 适用条件到正文渐进加载，核对版本、哈希和原始来源。方法辅助规划，不授予工具权限。经验保留候选、冲突、失效与撤销；组织方法经过固定回归、人工审批和显式发布。

![项目 Skill 目录](docs/images/skills.png)

<details>
<summary>方法正文、经验治理与发布详情</summary>

![方法正文与来源](docs/images/skill-details.png)

![事件复盘历史页面](docs/images/memory-history.png)

![组织方法与发布状态](docs/images/skill-publication.png)

发布状态不证明方法语义正确或记忆效果。下面为历史实际审批详情。

![发布详情历史页面](docs/images/publication-history.png)

</details>

### 7. 多 Agent 协作

任务板固定 DAG、角色、依赖、证据与预算。两个调查角色具有私有工具入口、输入和上下文；归并区分一致结论、未决冲突和失败依赖。取消和恢复保留持久化追加回执。

![协作任务板](docs/images/coordination-board.png)

<details>
<summary>角色私有包、执行停止、归并与证据</summary>

![角色私有任务包](docs/images/coordination-package.png)

这里展示真实预算停止和依赖失败。

![协作执行](docs/images/coordination-execution.png)

![归并报告](docs/images/coordination-report.png)

![协作证据回查](docs/images/coordination-evidence.png)

</details>

同预算开发实验单 Agent 完成 3/4、多 Agent 完成 2/4。全部分母、失败、成本与协议差异保持可见，尚未证明多 Agent 总体收益。

![单与多 Agent 比较](docs/images/coordination-comparison.png)

### 8. 运行记录与三阶段故障实验

运行详情保存状态、事件顺序与可回查结果。观察包分别展示正常 / 异常 / 恢复阶段的 HTTP、错误码、时间及原始记录；SHA-256 核对包身份。

![运行记录](docs/images/runs.png)

<details>
<summary>运行详情、实验列表与异常观测</summary>

![运行详情](docs/images/run-details.png)

![三阶段实验列表](docs/images/experiments.png)

下面的超时请求有正常与恢复对照。错误码用于定位调查方向，不能独立证明根因。

![异常观测详情](docs/images/experiment-details.png)

</details>

### 9. 评测与移动端

数据集校验固定任务、标签与 dev / holdout 分区。离线报告页面校验契约与分母，不执行批量模型，也不认证任意上传 JSON 的执行来源。

![评测入口](docs/images/evaluation.png)

<details>
<summary>历史评测报告与 390px 实际浏览器</summary>

下面显示原开发报告：qwen3.8-max-0902，9 完成 / 3 失败 / 4 当时未执行，和后文最终实验是不同数据组。

![离线报告历史页面](docs/images/evaluation-history.png)

下图来自独立 Docker 环境的真实 Chromium 390px 验证，属于零模型工程检查。

![移动端](docs/images/mobile-history.png)

</details>

## 系统架构

```mermaid
flowchart LR
  UI[Vue 支持工作台] --> API[FastAPI 组织与任务校验]
  API --> PG[(PostgreSQL / pgvector)]
  DOC[版本文档 / 工单 / 冻结观察包] --> INGEST[解析 / 修订 / 结构切片]
  INGEST --> PG
  API --> RET[范围过滤 / Vector / BM25 / RRF / Rerank]
  RET --> PG
  RET --> RAG[结构化回答 / 引用 / 支持检查]
  API --> GRAPH[LangGraph 有界调查]
  GRAPH --> MCP[受控 MCP / 观察工具]
  MCP --> LAB[隔离 RelayDesk 现场]
  GRAPH --> APPROVAL[人工批准 / 前状态核对]
  APPROVAL --> RECEIPT[动作回执 / 幂等 / 恢复]
  API --> DAG[角色私有任务 / 并行 / 归并]
  DAG --> MCP
  PG --> MEMORY[事件复盘 / 经验 / Skill 治理]
  MEMORY --> GRAPH
  RAG --> LLM[百炼模型接口]
  GRAPH --> LLM
  DAG --> LLM
```

身份、证据范围、工具白名单、预算与副作用由应用控制。模型输出、资料、Skill 和 MCP 文本不能扩大权限。引用身份、版本匹配、原句一致和语义支持分别检查。详细流程与恢复契约见[架构说明](docs/architecture.md)。

## 实测结果

最终冻结实验包含 12 个检索题、4 个回答题、四类熟悉故障家族的新调查任务，各组重复两次，共 **184 个计划槽位**。标签为初稿，尚未人工语义复核；小样本和同家族重复不能当作生产泛化能力。见[结果与失败解释](docs/evidence/results.md)及[原始报告清单](docs/evidence/source-manifest.json)。

### 检索：完整分母 144 次

| 策略 | 完成 / 全部 | Recall@5 | MRR@5 | 上下文覆盖 |
| --- | --- | --- | --- | --- |
| Vector | 24/24 | 0.8681 | 1.0000 | 0.8681 |
| BM25 | 24/24 | 0.2500 | 0.5000 | 0.2500 |
| RRF | 24/24 | 0.8681 | 1.0000 | 0.8681 |
| RRF + parent | 24/24 | 0.8681 | 1.0000 | 0.8681 |
| RRF + rerank | 23/24 | 0.6042 | 0.9583 | 0.6042 |
| RRF + rerank + parent | 24/24 | 0.6250 | 1.0000 | 0.7500 |

指标使用全部槽位，失败计入分母。一次 rerank 模型超时保留；本批 rerank 未提高覆盖。BM25 零模型，其他策略存在 embedding / rerank 调用。

### 回答：完成 16/16，不等于语义准确率 100%

| 入口 | 完成 / 全部 | 初稿规则事实命中 | 返回引用 | 模型判断依据不足声明 |
| --- | --- | --- | --- | --- |
| 直接 | 8/8 | 20/20 | 35 | 18/35 |
| 分步 | 8/8 | 20/20 | 23 | 3/23 |

最后一列是各自生成的不同声明集合，不能计算统一准确率或宣布分步更优。模型支持判断未人工复核；引用存在和原句一致不表示结论获得足够支持。

### 调查：保留全部失败与停止

| 方案 | 完成 / 全部 |
| --- | --- |
| 单 Agent | 3/8 |
| 单 Agent + 经验 | 1/8 |
| 多 Agent | 2/8 |
| 合计 | 6/24 |

其余 **16 失败、2 停止**，包含 6 次实验配置准备失败（确定零模型）。其他失败包括模型结构无效、缺少观测、上下文预算耗尽和依赖失败。检索窗口、输出目标与子配额不同，尚未证明经验或多 Agent 总体优越。

### 模型与证据边界

- 当前主模型 **qwen3.6-plus**。上面原始回答 / 调查实验使用 **qwen-plus-2025-07-28**，历史成绩未改标。
- qwen3.6-plus 独立配置补充 **3/6 完成、3/6 失败，28 已知 / 2 未知调用**，不合并原成功率。
- 原始用量和未知调用分别留档；费用为 `null`，未与账单对账。
- Fake / Mock 验证契约；Docker、HTTP、浏览器验证工程行为。它们不证明在线模型质量、生产并发、SLA 或真实企业根因准确率。

## 本地运行

先用独立 Docker 环境体验工单、资料、真实 PostgreSQL、HTTP、浏览器和重启读回。

```powershell
# 源码根目录；使用运行 Docker Desktop 的同一 Windows 用户。
# Python 与 docker.exe 必须可执行，两个端口明确空闲。
.\scripts\reproduce.ps1 -Name my-demo -Action Initialize -ApiPort 18011 -WebPort 15174
.\scripts\reproduce.ps1 -Name my-demo -Action Start
```

网页 `http://127.0.0.1:15174`；随机账号在本机 `local/reproduction/my-demo/demo-accounts.json`。详见[Windows 运行说明](docs/setup/local-run.md)与[独立复现 / CI](docs/setup/reproduction.md)。

独立复现默认零模型、无密钥，不导入旧演示库 UUID、现场实验或评测成绩。完整 RAG / 调查需显式准备自己的模型、资料索引和隔离实验，缺条件会明确失败。

## 技术栈与源码

公开暂存源码在独立 Docker 项目中实测：**662 后端通过 / 3 付费跳过、12 前端、1 条真实 Chromium**，6 来源 / 43 切片 / 51 条 HTTP 与 API 重启读回通过，零模型。首次前端导出的换行问题与修复保留，见[工程验证与边界](docs/evidence/engineering.md)。

| 层 | 实现 |
| --- | --- |
| 前端 | Vue 3、TypeScript、Element Plus、Vite |
| 数据与 API | Python 3.12、FastAPI、Pydantic、SQLAlchemy、Alembic、PostgreSQL 17、pgvector |
| Agent 与模型 | LangGraph、MCP SDK、百炼 OpenAI-compatible / embedding / rerank |
| 验证 | pytest、真实 PostgreSQL integration、Vitest、Playwright / Chromium、Ruff、Prettier |
| 复现 | Docker Compose、Python / Node 锁文件、固定镜像、零模型 GitHub Actions 定义 |

```text
src/supportops/    API、检索、回答、调查、动作、经验、协作和模型契约
frontend/         支持工作台与浏览器用例
migrations/       数据库与组织隔离迁移
skills/           四类有来源排查方法
data/             版本资料、构造任务与冻结清单
scripts/          初始化、实验、评测、复现与发布审计
tests/            契约、数据库和恢复验证
docker/           业务 / 实验 / 独立复现镜像
docs/images/      页面与图片身份
docs/evidence/    结果索引与解释
```

公开范围排除本机密钥、账号、数据库、依赖目录、开发计划和私人验收记录；必要公开历史报告保持原路径及字节，失败不被成功重跑覆盖。

GitHub Actions 已定义零模型独立复现，当前远端尚未运行。源码发布与本地运行不能当成公网应用已部署。
