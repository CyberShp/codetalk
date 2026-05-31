param(
    [switch]$SkipSlow,
    [switch]$SkipFrontendBuild,
    [switch]$RunE2E,
    [int]$E2EFrontendPort = 3005
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Invoke-GateStep {
    param(
        [string]$Name,
        [string]$WorkDir,
        [string[]]$Command
    )

    Write-Host ""
    Write-Host "==> $Name" -ForegroundColor Cyan
    Push-Location $WorkDir
    try {
        & $Command[0] @($Command[1..($Command.Length - 1)])
        if ($LASTEXITCODE -ne 0) {
            throw "$Name failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}

function Ensure-PythonVenv {
    param(
        [string]$Name,
        [string]$WorkDir,
        [string]$VenvDir
    )

    $venvPath = Join-Path $WorkDir $VenvDir
    $pythonPath = if ($IsWindows -or $env:OS -eq "Windows_NT") {
        Join-Path $venvPath "Scripts\python.exe"
    }
    else {
        Join-Path $venvPath "bin/python"
    }

    if (-not (Test-Path $pythonPath)) {
        Invoke-GateStep `
            -Name "${Name}: create runtime venv" `
            -WorkDir $WorkDir `
            -Command @("python", "-m", "venv", $VenvDir)
    }

    Invoke-GateStep `
        -Name "${Name}: install runtime deps" `
        -WorkDir $WorkDir `
        -Command @($pythonPath, "-m", "pip", "install", "-r", "requirements.txt")
}

Ensure-PythonVenv `
    -Name "backend" `
    -WorkDir (Join-Path $root "backend") `
    -VenvDir ".venv311"

Ensure-PythonVenv `
    -Name "deployer" `
    -WorkDir (Join-Path $root "deployer") `
    -VenvDir ".venv"

Invoke-GateStep `
    -Name "backend: collect tests" `
    -WorkDir (Join-Path $root "backend") `
    -Command @("python", "-m", "pytest", "--collect-only", "-q")

Invoke-GateStep `
    -Name "backend: product route contracts" `
    -WorkDir (Join-Path $root "backend") `
    -Command @("python", "-m", "pytest", "-q", "tests\test_repo_analysis_routes.py", "tests\test_repo_paths.py")

Invoke-GateStep `
    -Name "frontend: lint" `
    -WorkDir (Join-Path $root "frontend") `
    -Command @("npm", "run", "lint")

if (-not $SkipFrontendBuild) {
    Invoke-GateStep `
        -Name "frontend: production build" `
        -WorkDir (Join-Path $root "frontend") `
        -Command @("npm", "run", "build")
}

if ($RunE2E) {
    $previousFrontendPort = $env:CODETALK_FRONTEND_PORT
    try {
        $env:CODETALK_FRONTEND_PORT = [string]$E2EFrontendPort
        Invoke-GateStep `
            -Name "release: browser click-through e2e" `
            -WorkDir (Join-Path $root "frontend") `
            -Command @("npm", "run", "test:e2e:release")
    }
    finally {
        if ($null -eq $previousFrontendPort) {
            Remove-Item Env:\CODETALK_FRONTEND_PORT -ErrorAction SilentlyContinue
        }
        else {
            $env:CODETALK_FRONTEND_PORT = $previousFrontendPort
        }
    }
}

Invoke-GateStep `
    -Name "deployer: collect tests" `
    -WorkDir (Join-Path $root "deployer") `
    -Command @("python", "-m", "pytest", "--collect-only", "-q")

if (-not $SkipSlow) {
    Invoke-GateStep `
        -Name "deployer: full tests" `
        -WorkDir (Join-Path $root "deployer") `
        -Command @("python", "-m", "pytest", "-q")
}

Write-Host ""
Write-Host "Release gate passed." -ForegroundColor Green
