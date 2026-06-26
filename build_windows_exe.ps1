$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

$PythonExe = $env:FIRMWARE_INSTALLER_PYTHON
if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $PythonExe = 'python'
}

Write-Host "Using Python: $PythonExe"
& $PythonExe -c "import sys; import tkinter; print(sys.executable); print('Tk', tkinter.TkVersion)"
if ($LASTEXITCODE -ne 0) {
    throw "FirmwareInstaller Windows build requires a Python install with tkinter. Set FIRMWARE_INSTALLER_PYTHON to a Tk-capable python.exe."
}

$oldErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $PythonExe -c "import PyInstaller" *> $null
$hasPyInstaller = $LASTEXITCODE -eq 0
$ErrorActionPreference = $oldErrorActionPreference

if (-not $hasPyInstaller) {
    & $PythonExe -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install PyInstaller"
    }
}

& $PythonExe -m pip install pyserial
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install pyserial"
}

$bundledData = @(
    "$RepoRoot\firmware_catalog.json;.",
    "$RepoRoot\firmware_installer_windows.ps1;.",
    "$RepoRoot\checksums.sha256;.",
    "$RepoRoot\mistercade-v1;mistercade-v1",
    "$RepoRoot\mistercade-v2;mistercade-v2",
    "$RepoRoot\reflex-adapt;reflex-adapt",
    "$RepoRoot\reflex-ctrl;reflex-ctrl",
    "$RepoRoot\reflex-encode;reflex-encode",
    "$RepoRoot\reflex-prism;reflex-prism"
)

$pyInstallerArgs = @(
    "--noconfirm",
    "--clean",
    "--name", "FirmwareInstaller",
    "--distpath", "dist",
    "--workpath", "build",
    "--specpath", "build"
)

foreach ($data in $bundledData) {
    $source = ($data -split ';', 2)[0]
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Bundled data path not found: $source"
    }
    $pyInstallerArgs += @("--add-data", $data)
}

$pyInstallerArgs += "firmware_installer.py"

& $PythonExe -m PyInstaller @pyInstallerArgs

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

Write-Host "Built: $RepoRoot\dist\FirmwareInstaller\FirmwareInstaller.exe"
