$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

$oldErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
python -c "import PyInstaller" *> $null
$hasPyInstaller = $LASTEXITCODE -eq 0
$ErrorActionPreference = $oldErrorActionPreference

if (-not $hasPyInstaller) {
    python -m pip install pyinstaller
}

python -m PyInstaller `
    --noconfirm `
    --clean `
    --name FirmwareInstaller `
    --distpath dist `
    --workpath build `
    --specpath build `
    --add-data "$RepoRoot\firmware_catalog.json;." `
    --add-data "$RepoRoot\firmware_installer_windows.ps1;." `
    firmware_installer.py

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

Write-Host "Built: $RepoRoot\dist\FirmwareInstaller\FirmwareInstaller.exe"
