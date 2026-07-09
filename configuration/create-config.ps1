param(
    [string]$OutputFile
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-RequiredCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $CommandInfo = Get-Command -Name $Name -ErrorAction SilentlyContinue

    if (-not $CommandInfo) {
        throw "Missing required command: $Name"
    }

    if ($CommandInfo.Path) {
        return $CommandInfo.Path
    }

    return $CommandInfo.Source
}

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $Command @Arguments

    $ExitCode = $LASTEXITCODE

    if ($ExitCode -ne 0) {
        exit $ExitCode
    }
}

$ScriptDir = $PSScriptRoot
$RepoRoot = Split-Path -Parent $ScriptDir

$UvExe = Get-RequiredCommand "uv"
$PklExe = Get-RequiredCommand "pkl"

$GeneratedPkl = Join-Path $ScriptDir "generated_tool_config.pkl"
$ConfigPkl = Join-Path $ScriptDir "main.pkl"
$OutputDir = Join-Path $ScriptDir "build"

if (-not $OutputFile) {
    $OutputFile = Join-Path $OutputDir "main.yaml"
}

$OutputParent = Split-Path -Parent $OutputFile

if ($OutputParent) {
    New-Item -ItemType Directory -Force -Path $OutputParent | Out-Null
}

Invoke-NativeCommand -Command $UvExe -Arguments @(
    "run",
    "--project",
    (Join-Path $RepoRoot "trace_generator"),
    "python",
    (Join-Path $ScriptDir "generate_tool_config.py"),
    "--output",
    $GeneratedPkl
)

Invoke-NativeCommand -Command $PklExe -Arguments @(
    "format",
    "--write",
    (Join-Path $ScriptDir "objects.pkl"),
    (Join-Path $ScriptDir "actors.pkl"),
    (Join-Path $ScriptDir "technical_users.pkl"),
    (Join-Path $ScriptDir "identity_mapping.pkl"),
    (Join-Path $ScriptDir "master_data.pkl"),
    (Join-Path $ScriptDir "processes.pkl"),
    (Join-Path $ScriptDir "fraud_scenarios.pkl"),
    (Join-Path $ScriptDir "run_settings.pkl"),
    $GeneratedPkl,
    $ConfigPkl
)

Invoke-NativeCommand -Command $PklExe -Arguments @(
    "eval",
    $GeneratedPkl
) | Out-Null

Invoke-NativeCommand -Command $PklExe -Arguments @(
    "eval",
    $ConfigPkl
) | Out-Null

Invoke-NativeCommand -Command $PklExe -Arguments @(
    "eval",
    "-f",
    "yaml",
    "-o",
    $OutputFile,
    $ConfigPkl
)

Write-Output "Wrote $OutputFile"
