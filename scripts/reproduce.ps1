param(
    [Parameter(Mandatory)][string]$Name,
    [ValidateSet('Initialize','Start','Verify','Smoke','Browser','Restart','Stop')]
    [string]$Action = 'Initialize',
    [int]$ApiPort = 18011,
    [int]$WebPort = 15174,
    [string]$PythonExecutable = 'python',
    [string]$DockerExecutable = 'docker'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:PYTHONUTF8 = '1'
if ($Name -notmatch '^[a-z][a-z0-9-]{0,31}$') { throw '复现名称格式不正确。' }
$privateRoot = Join-Path $projectRoot ('local\reproduction\' + $Name)
if ($Action -eq 'Initialize') {
    if (-not $PSBoundParameters.ContainsKey('ApiPort') -or -not $PSBoundParameters.ContainsKey('WebPort')) {
        throw '初始化必须显式提供 ApiPort 和 WebPort。'
    }
    # 使用实际运行 Docker 的 Windows 用户创建私有目录；不自动修改 ACL。
    & $PythonExecutable scripts/init-reproduction.py --name $Name --api-port $ApiPort --web-port $WebPort
    if ($LASTEXITCODE -ne 0) { throw '独立配置初始化失败，原目录不覆盖。' }
    return
}
$manifest = Get-Content -LiteralPath (Join-Path $privateRoot 'manifest.json') -Encoding UTF8 -Raw | ConvertFrom-Json
if ($manifest.project -ne ('supportops-repro-' + $Name)) { throw '独立项目身份不匹配。' }
$envFile = Join-Path $privateRoot '.env'
$composeArgs = @('compose','-p',$manifest.project,'--env-file',$envFile,'-f','compose.reproduction.yaml')
function Invoke-ReproductionDocker {
    param([string[]]$CommandArguments)
    & $DockerExecutable @composeArgs @CommandArguments
    if ($LASTEXITCODE -ne 0) { throw ('独立 Docker 检查失败，代码：' + $LASTEXITCODE) }
}
switch ($Action) {
    'Start' { Invoke-ReproductionDocker -CommandArguments @('up','--build','-d','--wait','--wait-timeout','300') }
    'Verify' {
        Invoke-ReproductionDocker -CommandArguments @('exec','-T','api','python','scripts/verify-reproduction.py')
        Invoke-ReproductionDocker -CommandArguments @('exec','-T','web','sh','-c','npm run format:check && npm test && npm run build')
    }
    'Smoke' { Invoke-ReproductionDocker -CommandArguments @('exec','-T','api','python','scripts/smoke-reproduction.py','--output','local/http.json') }
    'Browser' { Invoke-ReproductionDocker -CommandArguments @('exec','-T','web','npm','run','test:reproduction') }
    'Restart' {
        Invoke-ReproductionDocker -CommandArguments @('restart','api')
        Invoke-ReproductionDocker -CommandArguments @('exec','-T','api','python','scripts/smoke-reproduction.py','--output','local/http.json','--readback')
    }
    'Stop' { Invoke-ReproductionDocker -CommandArguments @('down') }
}
