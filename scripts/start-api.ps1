param([ValidateRange(1024, 65535)][int]$Port = 8010)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw '项目独立环境不存在，请先按照 README 安装。'
}

# 显式使用项目解释器，避免启动到课程或 MachineGuard 环境。
Set-Location -LiteralPath $projectRoot
$env:PYTHONUTF8 = '1'
& $pythonPath -m uvicorn supportops.api.app:app --app-dir src --host 127.0.0.1 --port $Port
if ($LASTEXITCODE -ne 0) { throw "API 进程退出，代码：$LASTEXITCODE" }
