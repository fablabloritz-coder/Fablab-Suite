# FabInventory - Script inventaire master
# Genere un rapport HTML + CSV, avec choix de l'emplacement via une fenetre.
# Le HTML contient le bloc JSON requis par FabInventory:
# <script id="inventoryData" type="application/json">{...}</script>

$ErrorActionPreference = "Stop"

function Escape-Html {
    param([string]$Text)
    if ($null -eq $Text) { return "" }
    return ($Text -replace '&', '&amp;' -replace '<', '&lt;' -replace '>', '&gt;' -replace '"', '&quot;')
}

function Show-Info {
    param([string]$Message, [string]$Title = "FabInventory")
    [System.Windows.Forms.MessageBox]::Show($Message, $Title, [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
}

function Show-Error {
    param([string]$Message, [string]$Title = "FabInventory - Erreur")
    [System.Windows.Forms.MessageBox]::Show($Message, $Title, [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
}

function Select-OutputHtmlPath {
    param(
        [string]$DefaultDirectory,
        [string]$DefaultFileName
    )

    $dialog = New-Object System.Windows.Forms.SaveFileDialog
    $dialog.Title = "FabInventory - Choisir ou enregistrer le rapport"
    $dialog.InitialDirectory = $DefaultDirectory
    $dialog.FileName = $DefaultFileName
    $dialog.DefaultExt = "html"
    $dialog.Filter = "Rapport HTML (*.html)|*.html|Tous les fichiers (*.*)|*.*"
    $dialog.OverwritePrompt = $true

    $result = $dialog.ShowDialog()
    if ($result -ne [System.Windows.Forms.DialogResult]::OK -or [string]::IsNullOrWhiteSpace($dialog.FileName)) {
        return ""
    }

    return $dialog.FileName
}

function Get-WindowsReleaseLabel {
    try {
        $cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
        $displayVersion = ($cv.DisplayVersion | Out-String).Trim()
        $releaseId = ($cv.ReleaseId | Out-String).Trim()
        $build = ($cv.CurrentBuild | Out-String).Trim()
        $ubr = ($cv.UBR | Out-String).Trim()

        $versionLabel = ""
        if (-not [string]::IsNullOrWhiteSpace($displayVersion)) {
            $versionLabel = $displayVersion
        }
        elseif (-not [string]::IsNullOrWhiteSpace($releaseId)) {
            $versionLabel = $releaseId
        }

        $buildLabel = ""
        if (-not [string]::IsNullOrWhiteSpace($build)) {
            if (-not [string]::IsNullOrWhiteSpace($ubr)) {
                $buildLabel = "$build.$ubr"
            }
            else {
                $buildLabel = $build
            }
        }

        if (-not [string]::IsNullOrWhiteSpace($versionLabel) -and -not [string]::IsNullOrWhiteSpace($buildLabel)) {
            return "$versionLabel (build $buildLabel)"
        }
        if (-not [string]::IsNullOrWhiteSpace($versionLabel)) {
            return $versionLabel
        }
        if (-not [string]::IsNullOrWhiteSpace($buildLabel)) {
            return "build $buildLabel"
        }
    }
    catch {
    }

    return ""
}

function Get-SoftwareCategory {
    param(
        [string]$Name,
        [string]$Editor
    )

    $nameLower = (($Name | Out-String).Trim()).ToLowerInvariant()
    $editorLower = (($Editor | Out-String).Trim()).ToLowerInvariant()

    if ($nameLower -match '\b(kb\d{4,}|update|hotfix|patch|cumulative|service\s*pack|security\s*update)\b') {
        return 'update'
    }

    if ($nameLower -match '\b(runtime|redistributable|framework|sdk|driver|component|plugin|library|module|webview2)\b') {
        return 'composant'
    }

    if ($editorLower -match '\b(microsoft|intel|nvidia|amd)\b' -and $nameLower -match '\b(driver|runtime|framework|redistributable)\b') {
        return 'composant'
    }

    return 'main'
}

function Get-SoftwareList {
    $items = @()
    $paths = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )

    foreach ($path in $paths) {
        $apps = Get-ItemProperty $path | Where-Object { $_.DisplayName } | ForEach-Object {
            $name = ($_.DisplayName | Out-String).Trim()
            $version = ($_.DisplayVersion | Out-String).Trim()
            $editor = ($_.Publisher | Out-String).Trim()
            [PSCustomObject]@{
                n   = $name
                v   = $version
                e   = $editor
                d   = ($_.InstallDate | Out-String).Trim()
                s   = 0
                src = 'Registre'
                cat = (Get-SoftwareCategory -Name $name -Editor $editor)
            }
        }
        if ($apps) { $items += $apps }
    }

    return $items |
        Group-Object -Property n |
        ForEach-Object { $_.Group | Select-Object -First 1 } |
        Sort-Object -Property n
}

try {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing

    $pcName = $env:COMPUTERNAME
    $scanDate = Get-Date -Format 'dd/MM/yyyy HH:mm'
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'

    $software = Get-SoftwareList

    $osBase = (Get-CimInstance Win32_OperatingSystem).Caption
    $windowsRelease = Get-WindowsReleaseLabel
    $os = $osBase
    if (-not [string]::IsNullOrWhiteSpace($windowsRelease)) {
        $os = "$osBase $windowsRelease"
    }
    $cpu = (Get-CimInstance Win32_Processor | Select-Object -First 1).Name
    $ramGo = [math]::Round(((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB), 2)
    $fabricant = (Get-CimInstance Win32_ComputerSystem).Manufacturer
    $numSerie = (Get-CimInstance Win32_BIOS).SerialNumber
    $domaine = (Get-CimInstance Win32_ComputerSystem).Domain

    $inventoryData = [ordered]@{
        pcName   = $pcName
        date     = $scanDate
        software = $software
    }

    $json = $inventoryData | ConvertTo-Json -Depth 6 -Compress

    $defaultDir = [Environment]::GetFolderPath('Desktop')
    if ([string]::IsNullOrWhiteSpace($defaultDir)) {
        $defaultDir = $PSScriptRoot
    }

    $defaultFileName = "${pcName}_${stamp}.html"
    $htmlPath = Select-OutputHtmlPath -DefaultDirectory $defaultDir -DefaultFileName $defaultFileName

    if ([string]::IsNullOrWhiteSpace($htmlPath)) {
        Show-Info -Title "FabInventory" -Message "Operation annulee: aucun fichier n'a ete enregistre."
        exit 0
    }

    $csvPath = [System.IO.Path]::ChangeExtension($htmlPath, ".csv")

    $html = @"
<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Inventaire $pcName</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:Segoe UI,Arial,sans-serif;padding:24px;background:#f6f8fb;color:#1b2430}
.card{background:#fff;border:1px solid #dbe3ef;border-radius:10px;padding:16px;margin-bottom:14px}
.row{display:grid;grid-template-columns:220px 1fr;gap:8px 12px}
.label{font-weight:600;color:#374151}
code{background:#f0f3f8;padding:2px 6px;border-radius:4px}
</style>
</head>
<body>
<h1>Inventaire master - $(Escape-Html $pcName)</h1>
<div class="card">
  <div class="row"><span class="label">PC</span><span>$(Escape-Html $pcName)</span></div>
  <div class="row"><span class="label">Date</span><span>$(Escape-Html $scanDate)</span></div>
  <div class="row"><span class="label">OS</span><span>$(Escape-Html $os)</span></div>
    <div class="row"><span class="label">Version Windows</span><span>$(Escape-Html $windowsRelease)</span></div>
  <div class="row"><span class="label">CPU</span><span>$(Escape-Html $cpu)</span></div>
  <div class="row"><span class="label">RAM</span><span>$ramGo Go</span></div>
  <div class="row"><span class="label">Fabricant</span><span>$(Escape-Html $fabricant)</span></div>
  <div class="row"><span class="label">Numero serie</span><span>$(Escape-Html $numSerie)</span></div>
  <div class="row"><span class="label">Domaine</span><span>$(Escape-Html $domaine)</span></div>
  <div class="row"><span class="label">Logiciels</span><span>$($software.Count)</span></div>
</div>

<p>Fichier genere pour import dans <strong>FabInventory</strong>.</p>
<p>Le bloc JSON ci-dessous est lu automatiquement par l'application.</p>

<script id="inventoryData" type="application/json">$json</script>
</body>
</html>
"@

    Set-Content -Path $htmlPath -Value $html -Encoding UTF8

    $software |
        Select-Object \
            @{Name='pc_name';Expression={$pcName}},
            @{Name='scan_date';Expression={$scanDate}},
            @{Name='name';Expression={$_.n}},
            @{Name='version';Expression={$_.v}},
            @{Name='editor';Expression={$_.e}},
            @{Name='install_date';Expression={$_.d}},
            @{Name='size_mb';Expression={$_.s}},
            @{Name='source';Expression={$_.src}} |
        Export-Csv -Path $csvPath -NoTypeInformation -Encoding UTF8 -Delimiter ';'

    Show-Info -Title "FabInventory - Terminé" -Message "Fichiers enregistres:`n$htmlPath`n$csvPath"
    Write-Host "HTML: $htmlPath"
    Write-Host "CSV : $csvPath"
}
catch {
    $msg = $_.Exception.Message
    try {
        Show-Error -Message "Echec generation inventaire:`n$msg"
    }
    catch {
        Write-Error "Echec generation inventaire: $msg"
    }
    exit 1
}
