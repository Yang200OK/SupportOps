# Windows 本地运行

## 独立零模型体验

在源码根目录，用运行 Docker Desktop 的同一 Windows 用户打开 PowerShell。准备 Python（初始化仅标准库）、Git 和 Docker Desktop Linux containers。`python` 与 `docker.exe` 必须是明确可用的命令；也可用脚本的 PythonExecutable / DockerExecutable 参数传自己的实际路径。缺依赖或端口冲突会停止，不自动换配置。

```powershell
python --version
docker.exe version
docker.exe compose version
.\scripts\reproduce.ps1 -Name my-demo -Action Initialize -ApiPort 18011 -WebPort 15174
.\scripts\reproduce.ps1 -Name my-demo -Action Start
```

浏览器打开 `http://127.0.0.1:15174`，就绪接口为 `http://127.0.0.1:18011/health/ready`。账号从本机 `local/reproduction/my-demo/demo-accounts.json` 读取，不提交口令 / 私有配置。新环境没有 README 截图中的旧历史记录，也没有模型密钥。

```powershell
.\scripts\reproduce.ps1 -Name my-demo -Action Verify
.\scripts\reproduce.ps1 -Name my-demo -Action Smoke
.\scripts\reproduce.ps1 -Name my-demo -Action Browser
.\scripts\reproduce.ps1 -Name my-demo -Action Restart
.\scripts\reproduce.ps1 -Name my-demo -Action Stop
```

Verify 包含真实 PostgreSQL 集成与测试侧 Fake / Mock。Smoke 导入三格式 / 两版本的六份来源并核对 HTTP，重复执行拒绝覆盖；Restart 核对持久化。Stop 保留数据卷。详见[复现与 CI](reproduction.md)。

## Windows 主机完整 RAG / 调查路线

完整路线与零模型 Compose 是两个明确入口。需要 Python 3.12、Node 24、Docker，以及自己有权限使用的模型。以下命令均在源码根目录执行。

1. `python -m venv .venv`；`.venv\Scripts\python.exe -m pip install -r requirements-dev.lock`；`npm --prefix frontend ci`。在当前 PowerShell 显式设置 `$env:PYTHONPATH = Join-Path (Get-Location) 'src'`，让脚本导入当前源码；不要复用其他项目的已安装包。
2. 将 `.env.example` 复制为本机 `.env`，使用自己的随机数据库口令，四个数据库 URL 与显式 PostgreSQL 端口保持一致。配置自己的模型 key、区域对应接口、模型名和 embedding 维度；不使用仓库内其他项目的环境。主模型默认 qwen3.6-plus，模型可用性以自己账号和真实最小验证为准。
3. `.\scripts\docker.ps1 compose --env-file .env -f compose.yaml up -d --wait postgres`；执行 `.venv\Scripts\python.exe scripts/migrate.py`、`scripts/migrate.py --test` 与 `scripts/init-demo.py`（后两项仍以同一 Python 调用）。随机账号只保存在本机 local。
4. 后端：`.venv\Scripts\python.exe -m uvicorn supportops.api.app:app --app-dir src --host 127.0.0.1 --port 8010`；另一个 PowerShell：`npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173 --strictPort`。
5. 登录后导入自己的版本资料、生成结构切片并显式创建索引。不要更改模型或维度后继续使用旧索引。上传当前实例观测与工单，设计资料不能替代实例日志。

### 隔离实验准备

现场 MCP 工具固定使用本机 8101 / 8102；不要将它们指向生产服务。初始化独立实验配置：`.venv\Scripts\python.exe scripts/init-lab.py`，私有实验口令在 `.env.lab`。通过 `scripts/prepare-investigation-lab.py --help` 查看故障与输出参数；脚本固定准备 1.1 实例，先准备每次独立实例，再发起对应调查。`scripts/run-lab.py` 是 24 次三阶段实验批次入口，阅读脚本参数后再执行，不能把它当单实例准备。旧实验过期不能靠重新打开历史工单恢复现场。

实际动作必须使用页面的人工批准入口，核对建议、证据及实例前状态后执行。发现 started 未完成回执时按 uncertain 处理，不重复动作。必要模型和 Docker 请求会使用自己账号资源；没有 key 或准备条件时明确失败，无运行时 fallback。

公开历史报告用于阅读结果，不能自动重建旧数据库 UUID 或代替现场准备。原模型质量与当前新账号结果分开记录。
