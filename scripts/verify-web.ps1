param([switch]$Browser)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$npmPath = 'C:\Program Files\nodejs\npm.cmd'
Set-Location -LiteralPath (Join-Path $projectRoot 'frontend')
& $npmPath run format:check
if ($LASTEXITCODE -ne 0) { throw '前端格式检查失败。' }
& $npmPath test
if ($LASTEXITCODE -ne 0) { throw '前端行为测试失败。' }
& $npmPath run build
if ($LASTEXITCODE -ne 0) { throw '前端类型检查或构建失败。' }
if ($Browser) {
    # 真实浏览器会创建少量明确的构造工单；API 和网页必须已启动。
    & $npmPath run test:e2e
    if ($LASTEXITCODE -ne 0) { throw '真实浏览器验证失败。' }
}
else { Write-Output '本次未运行真实浏览器；本轮完整验证使用 -Browser。' }
