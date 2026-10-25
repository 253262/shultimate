$ErrorActionPreference = 'Continue'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location -LiteralPath $PSScriptRoot

if (Get-Command python -ErrorAction SilentlyContinue) {
    if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
        & python -m venv .venv
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
        & py -3 -m venv .venv
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
} else {
    Write-Error '请先安装 Python 3.10 或更高版本，然后重新运行本脚本。'
    exit 1
}
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    Write-Error 'Python 虚拟环境不完整，请删除 .venv 后重新运行本脚本。'
    exit 1
}
& .venv/Scripts/python.exe scripts/setup.py @args
exit $LASTEXITCODE
