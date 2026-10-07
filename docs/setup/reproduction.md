# 独立环境复现与 CI

本说明介绍独立零模型工程复现。独立环境位于同一 Docker 引擎的新项目 / 网络 / PostgreSQL 卷，新的 Python / Node 镜像从锁文件安装；不复制已有 .venv、node_modules、.env、账号或数据库。它是零模型工程复现，不重新跑已经暴露的 holdout，也不重现旧 UUID / 模型质量成绩。

## Windows 准备与启动

使用运行 Docker Desktop 的同一 Windows 用户打开 PowerShell。需要可执行的 Python（初始化只有标准库）、Docker Desktop Linux containers / Compose，以及本项目源码。业务 Python 3.12.12、Node 24、Playwright / Chromium 都在镜像内安装，主机不需要再次安装前端依赖。使用自己已配置的 docker.exe / Python 命令或显式路径，不自动寻找备用安装或端口。

```powershell
# 先进入自己克隆的 SupportOps 源码根目录
$dockerExe = 'docker.exe'
$pythonExe = 'python'
& $dockerExe version
& $dockerExe compose version
& $pythonExe --version

# 用新名称与明确的空闲端口；不要重复初始化同一名称。
.\scripts\reproduce.ps1 -Name my-demo -Action Initialize -ApiPort 18011 -WebPort 15174 -PythonExecutable $pythonExe -DockerExecutable $dockerExe
.\scripts\reproduce.ps1 -Name my-demo -Action Start -DockerExecutable $dockerExe
.\scripts\reproduce.ps1 -Name my-demo -Action Verify -DockerExecutable $dockerExe
.\scripts\reproduce.ps1 -Name my-demo -Action Smoke -DockerExecutable $dockerExe
.\scripts\reproduce.ps1 -Name my-demo -Action Browser -DockerExecutable $dockerExe
.\scripts\reproduce.ps1 -Name my-demo -Action Restart -DockerExecutable $dockerExe
```

首次下载 Python / Node 依赖和 Chromium 比后续构建慢；网络或锁文件安装失败会停止并保留错误，不换依赖。Dockerfile 将耗时浏览器安装放在源码复制之前，源码修改不会要求重新下载浏览器。示例私有状态在 local/reproduction/my-demo/；名称和端口以自己的显式初始化配置为准。端口被占用就停止，确认后用另一个新名称和明确端口初始化，不自动换到其他服务。

网页为 http://127.0.0.1:15174，API 就绪为 http://127.0.0.1:18011/health/ready。账号来自 local/reproduction/my-demo/demo-accounts.json，只有该目录有随机口令；不要把整个 local 目录、.env 或浏览器 trace 提交 GitHub。浏览器凭据挂载在 Vite 根目录外，文件接口应拒绝读取。这两个端口与 Windows 主机开发入口 5173 / 8010 独立。

初始化会核对业务库首次没有 public 表，执行两套 Alembic 升级，创建两个组织 / 三个随机口令账号并保存 initialization.json。再次启动沿用同一个明确数据卷，init-demo.py 核对已有凭据，不重置口令。应用数据库角色不能超级用户、绕过 RLS 或创建角色 / 数据库。默认 Compose 不暴露 PostgreSQL 端口。

## 检查与能力边界

Verify 与 GitHub Actions 使用同一个 verify-reproduction.py：pip check、Ruff、两库迁移 check、全部 pytest --integration --offline-models、UTF-8 和公开凭据检查；前端运行 Prettier、Vitest 和类型 / Vite 构建。新库的实际 PostgreSQL 用例与 Fake / Mock 模型契约分别声明，三项旧付费用例未启用。CI 未配置模型密钥并拒绝任何已配置密钥 / 付费开关；显式 offline-models 只在测试侧提供虚拟凭据，Provider 传输必须为 MockTransport，否则在网络发送前失败。常规测试和应用运行行为不变，因此不把零模型回归当模型质量通过。

Smoke 验证真实 HTTP：新工单 / blocked 输入检查、1.1 的五份 Markdown / JSON 及 2.0 的一份 PDF 资料导入，保持各自版本身份，原文字节 SHA、逐块引文 / 组织 404；写入私有 http.json 后拒绝再次执行。Restart 实际重启 API，再零模型逐条比对保存响应与摘要，写入新的 http-after-restart.json。Browser 用独立 Chromium 真实登录、输入检查、刷新持久化、跨组织隔离与 390px 视口，证据在私有 browser/，无 trace / video 或密码截图。

此 Compose 不传递主机模型密钥，主模型配置仍为 qwen3.6-plus，默认缺密钥时明确报错。已有现场工具固定读取 API 所在网络的 127.0.0.1:8101/8102；本轮不改变这些白名单，也不把未接入现场实验的容器配置称完整调查 / 动作 / 模型闭环复现。需要运行实际模型、实验工具或审批动作时，使用已有 Windows 本机路线，按 [Windows 运行说明](local-run.md) 显式准备 compose.lab.yaml、独立实验凭据和服务；该路线的历史真实质量 / 失败记录保持原身份。本轮模型质量重测、第二台机器和公网部署均未验证。

## 停止与清理

```powershell
.\scripts\reproduce.ps1 -Name my-demo -Action Stop -DockerExecutable $dockerExe
# down 不删卷；再次 Start 可核对历史状态。
```

需要彻底清理时，先停止此名称的项目，使用 docker volume inspect 核对卷名和 com.docker.compose.project 标签确实为 supportops-repro-my-demo，再显式删除这个单独的卷。禁止 docker system prune、通配卷清理或删除原 supportops / supportops-lab 卷。私有目录包含凭据和失败证据，保留到完成验收；本轮不自动递归删除。

## 实际故障定位

- .env Access is denied：初始化进程与 Docker 用户不同。私有目录应由实际运行 Docker 的用户生成；不要扩大 ACL 到所有用户。原准备失败保留，以同一用户创建新的独立名称。
- 构建失败：先看最后成功的镜像层和首次错误，核对锁文件 / 代理 / registry；保留日志，不以旧 .venv 或备用模型替代。
- API unhealthy：读取仅本项目 api 日志，依次检查新 postgres 就绪、两库迁移、账号校验和 0014；不清空旧库来修复新环境。
- 浏览器 / HTTP 失败：报告分母保持原样，先确认独立代理地址 / 端口和已保存记录；GET 就绪等待不重放 POST 或模型调用。

## CI 的实际状态

.github/workflows/ci.yaml 配置 push / pull_request / workflow_dispatch，contents: read，checkout 固定提交、persist-credentials=false；创建新配置、启动独立 Compose、执行同入口回归 / HTTP / Chromium、重启读回和仅停止该 CI 项目。没有模型密钥、部署或上传私有目录步骤。固定提交和最小权限依据 [GitHub 官方安全说明](https://docs.github.com/en/actions/reference/security/secure-use)，checkout 对应 [官方 v4.3.1 版本](https://github.com/actions/checkout/releases/tag/v4.3.1)。

本地 actionlint 与同入口实际执行验证定义及工程行为。2026-10-07 首次发布提交 `2069964` 的 [远端 GitHub Actions](https://github.com/Yang200OK/SupportOps/actions/runs/37574743684) 已全部通过：新配置与 Compose 构建、后端 / 真实 PostgreSQL、前端格式 / 测试 / 构建、HTTP、Chromium、API 重启读回及项目停止。该结果属于 GitHub Ubuntu runner 上的零模型工程验证；当前提交状态见 [Actions 页面](https://github.com/Yang200OK/SupportOps/actions)，不据此证明在线模型质量或生产部署。
