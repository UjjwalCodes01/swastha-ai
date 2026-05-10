param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $PytestArgs
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    python (Join-Path $PSScriptRoot "bootstrap_test_env.py")
}

if ($PytestArgs.Count -eq 0) {
    & $Python -m pytest tests\ai_core tests\compliance
} else {
    & $Python -m pytest @PytestArgs
}
