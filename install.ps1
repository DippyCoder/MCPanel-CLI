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

# Ensure the Python user Scripts folder is on PATH
$scripts = python -c "import sysconfig; print(sysconfig.get_path('scripts', 'nt_user'))" 2>$null
$userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($scripts -and ($userPath -notlike "*$scripts*")) {
    Write-Host "[2/2] Adding Python Scripts folder to your PATH..."
    $newPath = if ($userPath) { "$userPath;$scripts" } else { $scripts }
    [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
    $env:PATH = "$env:PATH;$scripts"
    Write-Host "      $scripts" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "[done] PATH updated." -ForegroundColor Green
    Write-Host ""
    Write-Host "  Open a new terminal window, then run 'mcpanel --help' to get started." -ForegroundColor Yellow
    Write-Host "  (PATH changes don't apply to the session that launched this installer.)" -ForegroundColor DarkGray
} else {
    Write-Host "[2/2] Done. Run 'mcpanel --help' to get started." -ForegroundColor Green
}
Write-Host ""
