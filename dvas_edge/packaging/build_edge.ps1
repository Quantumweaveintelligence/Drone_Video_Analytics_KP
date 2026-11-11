<#
.SYNOPSIS
    Build script for the DVAS Edge Operator portable distribution.
.DESCRIPTION
    Creates a self-contained dist\ folder with embedded Python 3.11,
    all pip dependencies, edge application code, and launchers.
    Run from dvas_edge\packaging\:
        powershell -ExecutionPolicy Bypass -File build_edge.ps1
#>

param(
    [string]$PythonVersion = "3.11.9",
    [string]$AppVersion    = "2.0.2",
    [string]$SigningCertThumbprint = $env:DVAS_SIGNING_CERT_THUMBPRINT,
    [string]$TimestampUrl = "http://timestamp.digicert.com",
    [switch]$SkipFrontend,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"

# Run a native tool: success is its exit code, not stderr (PowerShell 5.1 turns any
# stderr line of a native command into a terminating error under "Stop").
function Invoke-Native([scriptblock]$Command, [string]$What) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Command 2>&1 | ForEach-Object { Write-Host "  $_" } }
    finally { $ErrorActionPreference = $prev }
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit $LASTEXITCODE)" }
}
Set-StrictMode -Version Latest

# Paths
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Definition
$EdgeRoot    = Split-Path -Parent $ScriptDir
$RepoRoot    = Split-Path -Parent $EdgeRoot
$FrontendDir = Join-Path (Join-Path $RepoRoot "DVAS_Dashboard") "frontend"
$DistDir     = Join-Path $ScriptDir "dist"
$RuntimeDir  = Join-Path $DistDir "python_runtime"
$StaticDir   = Join-Path $DistDir "static"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  DVAS Edge Operator - Build Script v$AppVersion"
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Edge root  : $EdgeRoot"
Write-Host "  Dist output: $DistDir"
Write-Host ""

# Step 1: Clean dist
if (Test-Path $DistDir) {
    Write-Host "[1/7] Cleaning previous dist..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force $DistDir
}
New-Item -ItemType Directory -Force $DistDir | Out-Null
New-Item -ItemType Directory -Force $RuntimeDir | Out-Null

# Step 2: Download embedded Python
Write-Host "[2/7] Downloading Python $PythonVersion embeddable..." -ForegroundColor Yellow
$zipName = "python-$PythonVersion-embed-amd64.zip"
$zipUrl  = "https://www.python.org/ftp/python/$PythonVersion/$zipName"
$zipPath = Join-Path $ScriptDir $zipName

if (-not (Test-Path $zipPath)) {
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing
    Write-Host "  Downloaded $zipName"
} else {
    Write-Host "  Using cached $zipName"
}

Expand-Archive -Path $zipPath -DestinationPath $RuntimeDir -Force
Write-Host "  Extracted to $RuntimeDir"

# Enable site-packages in the embedded Python
$pthFile = Get-ChildItem $RuntimeDir -Filter "python*._pth" | Select-Object -First 1
if ($pthFile) {
    $pthContent = Get-Content $pthFile.FullName
    $pthContent = $pthContent -replace "^#import site", "import site"
    $pthContent += ".."
    Set-Content $pthFile.FullName $pthContent
    Write-Host "  Enabled site-packages in $($pthFile.Name)"
}

# Step 3: Install pip + dependencies
Write-Host "[3/7] Installing pip and dependencies..." -ForegroundColor Yellow
$python = Join-Path $RuntimeDir "python.exe"
$getPip = Join-Path $ScriptDir "get-pip.py"

if (-not (Test-Path $getPip)) {
    Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip -UseBasicParsing
}

& $python $getPip --no-warn-script-location 2>&1 | Out-Null
Write-Host "  pip installed"

$reqFile = Join-Path $ScriptDir "requirements-edge-win.txt"
Invoke-Native { & $python -m pip install --no-warn-script-location -r $reqFile } "pip install"
Write-Host "  All dependencies installed"

# Step 4: Copy application code
Write-Host "[4/7] Copying application code..." -ForegroundColor Yellow

$filesToCopy = @("edge_api_server.py", "main.py")
foreach ($f in $filesToCopy) {
    $src = Join-Path $EdgeRoot $f
    if (Test-Path $src) {
        Copy-Item $src -Destination $DistDir
        Write-Host "  Copied $f"
    }
}

$dirsToCopy = @("src", "ingest", "config")
foreach ($d in $dirsToCopy) {
    $src = Join-Path $EdgeRoot $d
    if (Test-Path $src) {
        Copy-Item -Recurse $src -Destination (Join-Path $DistDir $d)
        Write-Host "  Copied $d/"
    }
}

# Remove wireguard from copied config
$wgDir = Join-Path (Join-Path $DistDir "config") "wireguard"
if (Test-Path $wgDir) {
    Remove-Item -Recurse -Force $wgDir
    Write-Host "  Removed wireguard/ from dist"
}

# Remove __pycache__ from dist
Get-ChildItem -Recurse -Directory -Filter "__pycache__" -Path $DistDir |
    Remove-Item -Recurse -Force
Write-Host "  Cleaned __pycache__"

# Copy launcher
Copy-Item (Join-Path $ScriptDir "edge_launcher.py") -Destination $DistDir
Write-Host "  Copied edge_launcher.py"

# Step 5: Launchers
# The installer shortcuts start python_runtime\pythonw.exe edge_launcher.py directly
# (no console window); the "support console" shortcut uses python.exe --console.
Write-Host "[5/7] Launchers: shortcuts point at pythonw.exe (no .bat/.vbs needed)" -ForegroundColor Yellow
if (-not (Test-Path (Join-Path $DistDir "python_runtime\pythonw.exe"))) {
    throw "python_runtime\pythonw.exe missing from the embedded runtime"
}

# Step 6: Build frontend (optional)
if ($SkipFrontend) {
    Write-Host "[6/7] Skipping frontend build (--SkipFrontend)" -ForegroundColor DarkYellow
} else {
    Write-Host "[6/7] Building edge frontend..." -ForegroundColor Yellow
    if (Test-Path $FrontendDir) {
        Push-Location $FrontendDir
        try {
            $env:REACT_APP_DEPLOYMENT_TARGET = "edge"
            $env:REACT_APP_BACKEND_URL = ""
            $env:GENERATE_SOURCEMAP = "false"
            Invoke-Native { & npm run build } "frontend build"

            $buildOut = Join-Path $FrontendDir "build"
            if (Test-Path $buildOut) {
                if (Test-Path $StaticDir) { Remove-Item -Recurse -Force $StaticDir }
                Copy-Item -Recurse $buildOut $StaticDir
                Write-Host "  Frontend build copied to dist/static/"
            } else {
                Write-Host "  WARNING: Frontend build/ not found!" -ForegroundColor Red
            }
        } finally {
            Pop-Location
            $env:REACT_APP_DEPLOYMENT_TARGET = $null
            $env:REACT_APP_BACKEND_URL = $null
            $env:GENERATE_SOURCEMAP = $null
        }
    } else {
        Write-Host "  WARNING: Frontend directory not found at $FrontendDir" -ForegroundColor Red
    }
}

# Step 7: Copy ffmpeg (if available)
Write-Host "[7/8] Checking for ffmpeg..." -ForegroundColor Yellow
$ffCmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($ffCmd) {
    Copy-Item $ffCmd.Source -Destination $DistDir
    $fpCmd = Get-Command ffprobe -ErrorAction SilentlyContinue
    if ($fpCmd) { Copy-Item $fpCmd.Source -Destination $DistDir }
    Write-Host "  Copied ffmpeg + ffprobe"
} else {
    Write-Host "  ffmpeg not found in PATH - add it manually for evidence extraction" -ForegroundColor DarkYellow
}

# Step 8: Compile Inno Setup installer
if (-not $SkipInstaller) {
    Write-Host "[8/8] Checking for Inno Setup compiler..." -ForegroundColor Yellow
    $isccPaths = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    )
    $iscc = $isccPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($iscc) {
        Write-Host "  Found Inno Setup at: $iscc"
        $compileArgs = @("/DAppVersion=$AppVersion")
        $thumbprint = ($SigningCertThumbprint -replace '\s', '').ToUpperInvariant()
        if ($thumbprint) {
            $signTool = Get-Command signtool.exe -ErrorAction SilentlyContinue
            if (-not $signTool) {
                $kitsRoot = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
                $signTool = Get-ChildItem -LiteralPath $kitsRoot -Filter signtool.exe -File -Recurse -ErrorAction SilentlyContinue |
                    Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
                    Sort-Object FullName -Descending | Select-Object -First 1
            }
            if (-not $signTool) { throw "Signing certificate was configured, but signtool.exe was not found." }
            $signToolPath = if ($signTool.Source) { $signTool.Source } else { $signTool.FullName }
            $env:PATH = "$(Split-Path -Parent $signToolPath);$env:PATH"
            Write-Host "  Signing packaged executable and library files..." -ForegroundColor Yellow
            $signableFiles = Get-ChildItem -LiteralPath $DistDir -Recurse -File |
                Where-Object { $_.Extension -in @('.exe', '.dll', '.pyd') }
            foreach ($file in $signableFiles) {
                Invoke-Native { & $signToolPath sign /sha1 $thumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 $file.FullName } "Sign $($file.Name)"
            }
            $compileArgs += "/DSigningThumbprint=$thumbprint", "/DTimestampUrl=$TimestampUrl"
        } else {
            Write-Warning "No trusted signing certificate is configured. The installer will be unsigned and Smart App Control may block it."
        }
        Write-Host "  Compiling installer..." -ForegroundColor Yellow
        $issFile = Join-Path $ScriptDir "dvas_edge_installer.iss"
        & $iscc @compileArgs $issFile
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed (exit $LASTEXITCODE)" }
        $exeOutput = Join-Path (Join-Path $ScriptDir "output") "DVAS_Edge_Setup_$AppVersion.exe"
        if (Test-Path $exeOutput) {
            if ($thumbprint) {
                Invoke-Native { & $signToolPath sign /sha1 $thumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 $exeOutput } "Sign installer"
            }
            $exeSize = [math]::Round((Get-Item $exeOutput).Length / 1MB, 1)
            Write-Host "  Installer generated: $exeOutput ($exeSize MB)" -ForegroundColor Green
        }
    } else {
        Write-Host "  Inno Setup (ISCC.exe) not found - skipping installer compilation." -ForegroundColor DarkYellow
    }
} else {
    Write-Host "[8/8] Skipping installer compilation (--SkipInstaller)" -ForegroundColor DarkYellow
}

# Summary
$distSize = (Get-ChildItem -Recurse $DistDir | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  BUILD COMPLETE"
Write-Host "  Output: $DistDir"
Write-Host "  Size:   $([math]::Round($distSize, 1)) MB"
$installerExe = Join-Path (Join-Path $ScriptDir "output") "DVAS_Edge_Setup_$AppVersion.exe"
if (Test-Path $installerExe) {
    Write-Host "  Setup:  $installerExe ($([math]::Round((Get-Item $installerExe).Length / 1MB, 1)) MB)"
}
Write-Host "============================================================" -ForegroundColor Green
