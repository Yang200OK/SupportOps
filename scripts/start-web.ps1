param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$npmPath = 'C:\Program Files\nodejs\npm.cmd'
if (-not (Test-Path -LiteralPath $npmPath)) { throw '没有找到已约定的 Node.js / npm 安装，请按 README 配置。' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'frontend\node_modules'))) {
    throw '前端依赖尚未安装，请先在 frontend 下运行 npm ci。'
}
Set-Location -LiteralPath (Join-Path $projectRoot 'frontend')
# 本地代理仅连接项目 API 的 8010 端口，不接受外部网络连接。
& $npmPath run dev
if ($LASTEXITCODE -ne 0) { throw "前端进程退出，代码：$LASTEXITCODE" }
