# MCPanel CLI installer for Windows (PowerShell)
#
#   .\install.ps1              -> pip install --user (recommended)
#   .\install.ps1 -Uninstall   -> uninstall

param([switch]$Uninstall)

$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "  MCPanel CLI installer"
Write-Host "  ====================="
Write-Host ""

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] Python is not installed or not on PATH." -ForegroundColor Red
    exit 1
}

if ($Uninstall) {
    python -m pip uninstall -y mcpanel-cli 2>$null
    Write-Host "[done] Removed mcpanel-cli."
    exit 0
}

Write-Host "[1/2] Installing with pip (--user)..."
python -m pip install --user --upgrade $Repo
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "[2/2] Done. Run 'mcpanel --help' to get started."
Write-Host ""

# Check that the Scripts folder is on PATH
$scripts = python -c "import sysconfig; print(sysconfig.get_path('scripts', 'nt_user'))" 2>$null
if ($scripts -and ($env:PATH -notlike "*$scripts*")) {
    Write-Host "  NOTE: Add Python's user Scripts folder to your PATH:" -ForegroundColor Yellow
    Write-Host "    $scripts" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  To add it permanently, run this in PowerShell (as yourself, not admin):"
    Write-Host '    [Environment]::SetEnvironmentVariable("PATH", $env:PATH + ";' + $scripts + '", "User")'
    Write-Host ""
}
