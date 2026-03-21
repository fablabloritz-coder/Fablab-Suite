# FabInventory - Script inventaire master
# Genere un rapport HTML avec le bloc JSON requis par FabInventory:
# <script id="inventoryData" type="application/json">{...}</script>

$ErrorActionPreference = "SilentlyContinue"

function Escape-Html {
    param([string]$Text)
    if ($null -eq $Text) { return "" }
    return ($Text -replace '&', '&amp;' -replace '<', '&lt;' -replace '>', '&gt;' -replace '"', '&quot;')
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
            [PSCustomObject]@{
                n   = ($_.DisplayName | Out-String).Trim()
                v   = ($_.DisplayVersion | Out-String).Trim()
                e   = ($_.Publisher | Out-String).Trim()
                d   = ($_.InstallDate | Out-String).Trim()
                s   = 0
                src = 'Registre'
            }
        }
        if ($apps) { $items += $apps }
    }

    return $items |
        Group-Object -Property n |
        ForEach-Object { $_.Group | Select-Object -First 1 } |
        Sort-Object -Property n
}

$pcName = $env:COMPUTERNAME
$scanDate = Get-Date -Format 'dd/MM/yyyy HH:mm'
$software = Get-SoftwareList

$os = (Get-CimInstance Win32_OperatingSystem).Caption
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

$outDir = Join-Path $PSScriptRoot 'INVENTAIRES'
if (-not (Test-Path $outDir)) {
    New-Item -ItemType Directory -Path $outDir | Out-Null
}

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$fileName = "${pcName}_${stamp}.html"
$outPath = Join-Path $outDir $fileName

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

Set-Content -Path $outPath -Value $html -Encoding UTF8

Write-Host "Inventaire genere: $outPath"
Write-Host "Importez ce fichier HTML dans FabInventory via la page Importer."
