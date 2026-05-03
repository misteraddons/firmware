param(
    [switch]$ListFirmware
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonScript = Join-Path $RepoRoot 'firmware_installer.py'
$env:PYTHONIOENCODING = 'utf-8'

function Invoke-InstallerPython {
    param([string[]]$Arguments)

    $output = & python $PythonScript @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw ($output -join [Environment]::NewLine)
    }
    return $output
}

function Get-FirmwareChoices {
    try {
        $json = (Invoke-InstallerPython @('--catalog-json')) -join [Environment]::NewLine
        $records = $json | ConvertFrom-Json
        $choices = @()
        foreach ($record in $records) {
            $choices += [pscustomobject]@{
                Id = [string]$record.id
                Label = [string]$record.label
                Path = [string]$record.source
                Verify = [bool]$record.controller_check
                InstallMethod = [string]$record.install_method
                Status = [string]$record.status
            }
        }
        return $choices
    } catch {
        $choices = @()
        Get-ChildItem -LiteralPath $RepoRoot -Recurse -File -Filter '*.uf2' |
            Where-Object { $_.FullName -notmatch '\\(\.git|__pycache__|__MACOSX|firmware-cache)\\' } |
            Sort-Object FullName |
            ForEach-Object {
                $choices += [pscustomobject]@{
                    Id = ''
                    Label = $_.Name
                    Path = $_.FullName
                    Verify = $true
                    InstallMethod = 'rp2040'
                    Status = 'bundled'
                }
            }
        return $choices
    }
}

if ($ListFirmware) {
    Get-FirmwareChoices | ForEach-Object {
        "$($_.Id)`t$($_.Label)`t$($_.InstallMethod)`t$($_.Status)`t$($_.Path)"
    }
    exit 0
}

function Quote-Arg {
    param([string]$Value)
    if ($null -eq $Value) { return '""' }
    if ($Value -notmatch '[\s"]') { return $Value }
    return '"' + ($Value -replace '"', '\"') + '"'
}

function Add-LogLine {
    param([string]$Message)
    if ([string]::IsNullOrWhiteSpace($Message)) { return }
    $logBox.AppendText(("[{0:HH:mm:ss}] {1}{2}" -f (Get-Date), $Message, [Environment]::NewLine))
}

function Set-InstallerStatus {
    param(
        [string]$Message,
        [string]$ColorName = 'DimGray'
    )
    $status.Text = $Message
    $status.ForeColor = [System.Drawing.Color]::FromName($ColorName)
}

function Set-Busy {
    param([bool]$Busy)
    $combo.Enabled = -not $Busy
    $downloadButton.Enabled = -not $Busy
    $browseButton.Enabled = -not $Busy
    $verifyBox.Enabled = -not $Busy
    $startButton.Enabled = -not $Busy
    $stopButton.Enabled = $Busy
}

function Get-SelectedChoice {
    if (-not $combo.SelectedItem) { return $null }
    return $choiceByLabel[[string]$combo.SelectedItem]
}

function Get-ControllerFlagArgs {
    if ($verifyBox.Checked) { return @('--controller-check') }
    return @('--no-controller-check')
}

function Get-StartArgs {
    param($Choice)

    if ($Choice.InstallMethod -eq 'coming_soon') {
        throw "$($Choice.Label) firmware is coming soon."
    }

    if ($Choice.Id) {
        if ($Choice.InstallMethod -ne 'rp2040') {
            return @('--product', $Choice.Id, '--download')
        }
        return @('--product', $Choice.Id) + (Get-ControllerFlagArgs)
    }

    if ($Choice.Path -and (Test-Path -LiteralPath $Choice.Path)) {
        return @('--firmware', $Choice.Path) + (Get-ControllerFlagArgs)
    }

    throw "No firmware path is available for $($Choice.Label)."
}

function Get-DownloadArgs {
    param($Choice)

    if ($Choice.InstallMethod -eq 'coming_soon') {
        throw "$($Choice.Label) firmware is coming soon."
    }
    if (-not $Choice.Id) {
        throw 'Custom firmware is already local; nothing to download.'
    }
    return @('--product', $Choice.Id, '--download', '--refresh')
}

function Start-InstallerProcess {
    param(
        [string[]]$Arguments,
        [string]$Mode
    )

    if ($script:ActiveProcess -and -not $script:ActiveProcess.HasExited) {
        return
    }

    $script:ActiveMode = $Mode
    $script:Stopping = $false
    $script:ActiveOut = Join-Path ([System.IO.Path]::GetTempPath()) ("firmware-installer-{0}.out.log" -f ([guid]::NewGuid()))
    $script:ActiveErr = Join-Path ([System.IO.Path]::GetTempPath()) ("firmware-installer-{0}.err.log" -f ([guid]::NewGuid()))
    New-Item -ItemType File -Path $script:ActiveOut -Force | Out-Null
    New-Item -ItemType File -Path $script:ActiveErr -Force | Out-Null
    $script:LogOffsets = @{
        $script:ActiveOut = 0L
        $script:ActiveErr = 0L
    }

    $argText = (@($PythonScript) + $Arguments | ForEach-Object { Quote-Arg $_ }) -join ' '
    Add-LogLine "python $argText"

    $script:ActiveProcess = Start-Process -FilePath 'python' `
        -ArgumentList $argText `
        -WorkingDirectory $RepoRoot `
        -RedirectStandardOutput $script:ActiveOut `
        -RedirectStandardError $script:ActiveErr `
        -WindowStyle Hidden `
        -PassThru

    Set-Busy $true
    if ($Mode -eq 'download') {
        Set-InstallerStatus 'Downloading firmware...' 'DarkOrange'
    } else {
        Set-InstallerStatus 'Starting installer...' 'DarkOrange'
    }
}

function Read-NewLogText {
    param([string]$Path)

    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return '' }
    $offset = [int64]$script:LogOffsets[$Path]
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try {
        if ($stream.Length -le $offset) { return '' }
        $stream.Seek($offset, [System.IO.SeekOrigin]::Begin) | Out-Null
        $reader = New-Object System.IO.StreamReader($stream)
        $text = $reader.ReadToEnd()
        $script:LogOffsets[$Path] = $stream.Position
        return $text
    } finally {
        $stream.Dispose()
    }
}

function Poll-InstallerProcess {
    if (-not $script:ActiveProcess) { return }

    foreach ($path in @($script:ActiveOut, $script:ActiveErr)) {
        $text = Read-NewLogText $path
        if ($text) {
            foreach ($line in ($text -split "`r?`n")) {
                if ([string]::IsNullOrWhiteSpace($line)) { continue }
                Add-LogLine $line
                if ($line -match 'Waiting for RPI-RP2') { Set-InstallerStatus $line 'Blue' }
                elseif ($line -match 'Copying|Downloading|Checking cache') { Set-InstallerStatus $line 'DarkOrange' }
                elseif ($line -match 'Flash complete|Cached') { Set-InstallerStatus $line 'Green' }
                elseif ($line -match '^Error|Firmware error') { Set-InstallerStatus $line 'Red' }
            }
        }
    }

    if ($script:ActiveProcess.HasExited) {
        $exitCode = $script:ActiveProcess.ExitCode
        $mode = $script:ActiveMode
        $script:ActiveProcess.Dispose()
        $script:ActiveProcess = $null
        Set-Busy $false

        if ($script:Stopping) {
            Add-LogLine 'Stopped.'
            Set-InstallerStatus 'Stopped.' 'DimGray'
        } elseif ($exitCode -eq 0) {
            if ($mode -eq 'download') {
                Set-InstallerStatus 'Cached. Select Start to flash RP2040 firmware.' 'Green'
            } else {
                Set-InstallerStatus 'Installer process finished.' 'Green'
            }
        } else {
            Set-InstallerStatus "Installer exited with code $exitCode." 'Red'
        }
    }
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$choices = @(Get-FirmwareChoices)
$choiceByLabel = @{}
foreach ($choice in $choices) { $choiceByLabel[$choice.Label] = $choice }

$script:ActiveProcess = $null
$script:ActiveOut = $null
$script:ActiveErr = $null
$script:LogOffsets = @{}
$script:ActiveMode = ''
$script:Stopping = $false

$form = New-Object System.Windows.Forms.Form
$form.Text = 'UF2 Firmware Installer'
$form.Size = New-Object System.Drawing.Size(780, 540)
$form.MinimumSize = New-Object System.Drawing.Size(700, 460)
$form.StartPosition = 'CenterScreen'

$title = New-Object System.Windows.Forms.Label
$title.Text = 'RP2040 UF2 Firmware Installer'
$title.Font = New-Object System.Drawing.Font($title.Font.FontFamily, 14, [System.Drawing.FontStyle]::Bold)
$title.AutoSize = $true
$title.Location = New-Object System.Drawing.Point(16, 16)
$form.Controls.Add($title)

$firmwareLabel = New-Object System.Windows.Forms.Label
$firmwareLabel.Text = 'Firmware'
$firmwareLabel.AutoSize = $true
$firmwareLabel.Location = New-Object System.Drawing.Point(16, 60)
$form.Controls.Add($firmwareLabel)

$combo = New-Object System.Windows.Forms.ComboBox
$combo.DropDownStyle = 'DropDownList'
$combo.Location = New-Object System.Drawing.Point(16, 82)
$combo.Size = New-Object System.Drawing.Size(460, 28)
$combo.Anchor = 'Top, Left, Right'
foreach ($choice in $choices) { [void]$combo.Items.Add($choice.Label) }
$form.Controls.Add($combo)

$downloadButton = New-Object System.Windows.Forms.Button
$downloadButton.Text = 'Download/Refresh'
$downloadButton.Location = New-Object System.Drawing.Point(488, 80)
$downloadButton.Size = New-Object System.Drawing.Size(124, 30)
$downloadButton.Anchor = 'Top, Right'
$form.Controls.Add($downloadButton)

$browseButton = New-Object System.Windows.Forms.Button
$browseButton.Text = 'Browse...'
$browseButton.Location = New-Object System.Drawing.Point(620, 80)
$browseButton.Size = New-Object System.Drawing.Size(120, 30)
$browseButton.Anchor = 'Top, Right'
$form.Controls.Add($browseButton)

$verifyBox = New-Object System.Windows.Forms.CheckBox
$verifyBox.Text = 'Wait for controller/gamepad after flashing'
$verifyBox.AutoSize = $true
$verifyBox.Location = New-Object System.Drawing.Point(16, 122)
$form.Controls.Add($verifyBox)

$status = New-Object System.Windows.Forms.Label
$status.Text = 'Select firmware, then connect an RPI-RP2 drive.'
$status.Font = New-Object System.Drawing.Font($status.Font.FontFamily, 10, [System.Drawing.FontStyle]::Bold)
$status.ForeColor = [System.Drawing.Color]::FromName('DimGray')
$status.Location = New-Object System.Drawing.Point(16, 154)
$status.Size = New-Object System.Drawing.Size(724, 28)
$status.Anchor = 'Top, Left, Right'
$form.Controls.Add($status)

$logBox = New-Object System.Windows.Forms.TextBox
$logBox.Multiline = $true
$logBox.ScrollBars = 'Vertical'
$logBox.ReadOnly = $true
$logBox.Location = New-Object System.Drawing.Point(16, 188)
$logBox.Size = New-Object System.Drawing.Size(724, 250)
$logBox.Anchor = 'Top, Bottom, Left, Right'
$form.Controls.Add($logBox)

$startButton = New-Object System.Windows.Forms.Button
$startButton.Text = 'Start'
$startButton.Location = New-Object System.Drawing.Point(500, 452)
$startButton.Size = New-Object System.Drawing.Size(110, 32)
$startButton.Anchor = 'Bottom, Right'
$form.Controls.Add($startButton)

$stopButton = New-Object System.Windows.Forms.Button
$stopButton.Text = 'Stop'
$stopButton.Enabled = $false
$stopButton.Location = New-Object System.Drawing.Point(630, 452)
$stopButton.Size = New-Object System.Drawing.Size(110, 32)
$stopButton.Anchor = 'Bottom, Right'
$form.Controls.Add($stopButton)

$combo.add_SelectedIndexChanged({
    $choice = Get-SelectedChoice
    if (-not $choice) { return }
    $verifyBox.Checked = [bool]$choice.Verify
    if ($choice.InstallMethod -eq 'coming_soon') {
        Set-InstallerStatus 'Coming soon; no firmware source configured yet.' 'DarkOrange'
    } elseif ($choice.InstallMethod -ne 'rp2040') {
        Set-InstallerStatus "$($choice.Status); download/cache only for this 32u4 package." 'DarkOrange'
    } elseif ($choice.Path) {
        Set-InstallerStatus "Ready ($($choice.Status))." 'DimGray'
    } else {
        Set-InstallerStatus 'Download required; Start will download first.' 'DarkOrange'
    }
})

$browseButton.add_Click({
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = 'Select UF2 firmware'
    $dialog.Filter = 'UF2 firmware (*.uf2)|*.uf2|All files (*.*)|*.*'
    if ($dialog.ShowDialog($form) -ne [System.Windows.Forms.DialogResult]::OK) { return }

    $label = [System.IO.Path]::GetFileName($dialog.FileName)
    if (-not $choiceByLabel.ContainsKey($label)) {
        $choice = [pscustomobject]@{
            Id = ''
            Label = $label
            Path = $dialog.FileName
            Verify = $true
            InstallMethod = 'rp2040'
            Status = 'custom'
        }
        $choiceByLabel[$label] = $choice
        [void]$combo.Items.Add($label)
    }
    $combo.SelectedItem = $label
})

$downloadButton.add_Click({
    try {
        $choice = Get-SelectedChoice
        if (-not $choice) { throw 'Select firmware first.' }
        Start-InstallerProcess (Get-DownloadArgs $choice) 'download'
    } catch {
        Add-LogLine "Error: $($_.Exception.Message)"
        Set-InstallerStatus "Error: $($_.Exception.Message)" 'Red'
    }
})

$startButton.add_Click({
    try {
        $choice = Get-SelectedChoice
        if (-not $choice) { throw 'Select firmware first.' }
        Start-InstallerProcess (Get-StartArgs $choice) 'start'
    } catch {
        Add-LogLine "Error: $($_.Exception.Message)"
        Set-InstallerStatus "Error: $($_.Exception.Message)" 'Red'
    }
})

$stopButton.add_Click({
    if ($script:ActiveProcess -and -not $script:ActiveProcess.HasExited) {
        $script:Stopping = $true
        Set-InstallerStatus 'Stopping...' 'DarkOrange'
        try { $script:ActiveProcess.Kill() } catch { }
    }
})

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 250
$timer.add_Tick({ Poll-InstallerProcess })

$form.add_FormClosing({
    if ($script:ActiveProcess -and -not $script:ActiveProcess.HasExited) {
        try { $script:ActiveProcess.Kill() } catch { }
    }
})

if ($choices.Count -gt 0) {
    $combo.SelectedIndex = 0
    Add-LogLine "Found $($choices.Count) catalog firmware option(s)."
} else {
    $combo.Enabled = $false
    Set-InstallerStatus 'No repo UF2 firmware found. Use Browse for a custom UF2.' 'DarkOrange'
}

Add-LogLine 'Select firmware, then connect a board in BOOTSEL/RPI-RP2 mode.'
$timer.Start()
[void]$form.ShowDialog()
