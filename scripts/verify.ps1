param([switch]$Integration)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
Set-Location -LiteralPath $projectRoot
$env:PYTHONUTF8 = '1'

# 每个检查都显式判断原生进程退出码，失败就停止。
& $pythonPath -m pip check
if ($LASTEXITCODE -ne 0) { throw '依赖兼容性检查失败。' }
& $pythonPath -m ruff check src tests scripts migrations
if ($LASTEXITCODE -ne 0) { throw '静态检查失败。' }
& $pythonPath -m ruff format --check src tests scripts migrations
if ($LASTEXITCODE -ne 0) { throw '格式检查失败。' }
$pytestArguments = @('-q', '--tb=short')
if ($Integration) { $pytestArguments += '--integration' }
& $pythonPath -m pytest @pytestArguments
if ($LASTEXITCODE -ne 0) { throw '行为测试失败。' }
& $pythonPath scripts/check-text.py
if ($LASTEXITCODE -ne 0) { throw '文本编码检查失败。' }
if (-not $Integration) { Write-Output '本次未运行数据库集成测试；完整本轮检查使用 -Integration。' }
