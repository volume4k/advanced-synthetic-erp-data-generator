param(
    [string]$OutputFile
)

$ErrorActionPreference = "Stop"

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command,
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Arguments
    )

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

$ScriptDir = $PSScriptRoot
$RepoRoot = Split-Path -Parent $ScriptDir

$GeneratedPkl = Join-Path $ScriptDir "generated_tool_config.pkl"
$ConfigPkl = Join-Path $ScriptDir "main.pkl"
$OutputDir = Join-Path $ScriptDir "build"

if (-not $OutputFile) {
    $OutputFile = Join-Path $OutputDir "main.yaml"
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "Missing required command: uv"
}

if (-not (Get-Command pkl -ErrorAction SilentlyContinue)) {
    Write-Error "Missing required command: pkl"
}

$OutputParent = Split-Path -Parent $OutputFile
if ($OutputParent) {
    New-Item -ItemType Directory -Force -Path $OutputParent | Out-Null
}

Invoke-NativeCommand uv run --project (Join-Path $RepoRoot "trace_generator") `
    python (Join-Path $ScriptDir "generate_tool_config.py") `
    --output $GeneratedPkl

Invoke-NativeCommand pkl format --write `
    (Join-Path $ScriptDir "objects.pkl") `
    (Join-Path $ScriptDir "actors.pkl") `
    (Join-Path $ScriptDir "technical_users.pkl") `
    (Join-Path $ScriptDir "identity_mapping.pkl") `
    (Join-Path $ScriptDir "master_data.pkl") `
    (Join-Path $ScriptDir "processes.pkl") `
    (Join-Path $ScriptDir "fraud_scenarios.pkl") `
    (Join-Path $ScriptDir "run_settings.pkl") `
    $GeneratedPkl `
    $ConfigPkl

Invoke-NativeCommand pkl eval $GeneratedPkl | Out-Null
Invoke-NativeCommand pkl eval $ConfigPkl | Out-Null
Invoke-NativeCommand pkl eval -f yaml -o $OutputFile $ConfigPkl

Write-Output "Wrote $OutputFile"
