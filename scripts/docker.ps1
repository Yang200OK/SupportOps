$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
# 使用显式配置的 Docker 命令，或当前 PATH 中的标准 docker；找不到就停止。
$dockerCommand = if ($env:SUPPORTOPS_DOCKER_EXECUTABLE) { $env:SUPPORTOPS_DOCKER_EXECUTABLE } else { 'docker.exe' }
$dockerPath = (Get-Command $dockerCommand -CommandType Application -ErrorAction Stop).Source
$dockerDirectory = Split-Path -Parent $dockerPath

# 新安装后当前终端尚未更新 PATH；凭据助手也需要同目录可发现。
$originalPath = $env:PATH
try {
    Set-Location -LiteralPath $projectRoot
    $env:PATH = $dockerDirectory + ';' + $originalPath
    & $dockerPath @args
    if ($LASTEXITCODE -ne 0) { throw "Docker 命令失败，退出码：$LASTEXITCODE" }
} finally {
    $env:PATH = $originalPath
}
