param([int]$Port = 7878)
$ErrorActionPreference = 'Stop'
$projectDirectory = $PSScriptRoot
$pythonPath = Join-Path $projectDirectory '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Create the environment first: py -3.14 -m venv .venv; .\.venv\Scripts\python -m pip install -r requirements.txt'
}
Set-Location -LiteralPath $projectDirectory
$runtimeTemp = Join-Path $projectDirectory '.venv\runtime-temp'
New-Item -ItemType Directory -Force -Path $runtimeTemp | Out-Null
$env:TEMP = $runtimeTemp
$env:TMP = $runtimeTemp
& $pythonPath -m uvicorn backend.main:app --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
