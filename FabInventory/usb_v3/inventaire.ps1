# ============================================================
# SCRIPT D'INVENTAIRE DE POSTE v3
# Rapport HTML interactif avec tri, coches, notes, comparaison
# ============================================================

$scriptDir = $PSScriptRoot
Write-Host "Collecte des informations en cours..." -ForegroundColor Cyan

# --- SYSTEM INFO ---
Write-Host "  > Systeme..." -ForegroundColor Gray
$os = Get-CimInstance Win32_OperatingSystem
$cs = Get-CimInstance Win32_ComputerSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$bios = Get-CimInstance Win32_BIOS
$pcName = $env:COMPUTERNAME
$dateNow = Get-Date -Format "yyyy-MM-dd_HH-mm"
$dateFr = Get-Date -Format "dd/MM/yyyy HH:mm"
$ramGo = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
$cpuInfo = "$($cpu.Name) ($($cpu.NumberOfCores) coeurs)"
$osInfo = "$($os.Caption) $($os.Version)"
$fabricant = "$($cs.Manufacturer) $($cs.Model)"
$numSerie = $bios.SerialNumber
$domaine = $cs.Domain

# --- DISKS ---
Write-Host "  > Disques..." -ForegroundColor Gray
$disks = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | ForEach-Object {
    @{ l=$_.DeviceID; t=[math]::Round($_.Size/1GB,1); f=[math]::Round($_.FreeSpace/1GB,1); u=[math]::Round(($_.Size-$_.FreeSpace)/1GB,1) }
}

# --- SOFTWARE: REGISTRY MACHINE ---
Write-Host "  > Logiciels (registre machine)..." -ForegroundColor Gray
$regM = @("HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*","HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*")
$swReg = $regM | ForEach-Object { Get-ItemProperty $_ -EA SilentlyContinue } |
    Where-Object { $_.DisplayName -and $_.DisplayName -notmatch "^(Update for|Security Update|Hotfix|KB\d)" }

# --- SOFTWARE: REGISTRY USER ---
Write-Host "  > Logiciels (registre utilisateur)..." -ForegroundColor Gray
$swUser = Get-ItemProperty "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*" -EA SilentlyContinue |
    Where-Object { $_.DisplayName -and $_.DisplayName -notmatch "^(Update for|Security Update|Hotfix|KB\d)" }

# --- SOFTWARE: APPX ---
Write-Host "  > Applications AppX / Store..." -ForegroundColor Gray
$swAppx = @()
try {
    $swAppx = Get-AppxPackage -AllUsers -EA SilentlyContinue |
        Where-Object { !$_.IsFramework -and $_.SignatureKind -ne "System" -and $_.Name -notmatch "^Microsoft\.(NET|VCLibs|UI|Services|Windows)" }
} catch {}

# --- SOFTWARE: PROGRAM FILES SCAN ---
Write-Host "  > Scan dossiers Program Files..." -ForegroundColor Gray
$pfItems = @()
@("$env:ProgramFiles","${env:ProgramFiles(x86)}","$env:LOCALAPPDATA\Programs","$env:LOCALAPPDATA\Autodesk") | ForEach-Object {
    if (Test-Path $_) {
        Get-ChildItem $_ -Directory -EA SilentlyContinue |
            Where-Object { $_.Name -notmatch "^(Common Files|WindowsApps|Windows|Microsoft|Uninstall|Reference)" } |
            ForEach-Object { $pfItems += @{n=$_.Name;p=$_.FullName} }
    }
}

# --- MERGE ALL SOFTWARE ---
Write-Host "  > Fusion..." -ForegroundColor Gray
$allSw = @()
$seen = @{}

foreach ($s in $swReg) {
    $key = $s.DisplayName.ToLower().Trim()
    if (!$seen[$key]) {
        $seen[$key] = $true
        $dt = "-"
        if ($s.InstallDate) { try { $dt = [datetime]::ParseExact($s.InstallDate,"yyyyMMdd",$null).ToString("dd/MM/yyyy") } catch { $dt = $s.InstallDate } }
        $sz = if ($s.EstimatedSize) { [math]::Round($s.EstimatedSize/1024,1) } else { 0 }
        $allSw += @{n=$s.DisplayName;v=$s.DisplayVersion;e=$s.Publisher;d=$dt;s=$sz;src="Registre"}
    }
}
foreach ($s in $swUser) {
    $key = $s.DisplayName.ToLower().Trim()
    if (!$seen[$key]) {
        $seen[$key] = $true
        $dt = "-"
        if ($s.InstallDate) { try { $dt = [datetime]::ParseExact($s.InstallDate,"yyyyMMdd",$null).ToString("dd/MM/yyyy") } catch { $dt = $s.InstallDate } }
        $sz = if ($s.EstimatedSize) { [math]::Round($s.EstimatedSize/1024,1) } else { 0 }
        $allSw += @{n=$s.DisplayName;v=$s.DisplayVersion;e=$s.Publisher;d=$dt;s=$sz;src="Utilisateur"}
    }
}
foreach ($s in $swAppx) {
    $key = $s.Name.ToLower().Trim()
    if (!$seen[$key]) {
        $seen[$key] = $true
        $allSw += @{n=$s.Name;v=$s.Version;e=$s.PublisherDisplayName;d="-";s=0;src="AppX"}
    }
}
foreach ($pf in $pfItems) {
    $key = $pf.n.ToLower().Trim()
    $found = $false
    foreach ($k in $seen.Keys) { if ($k -like "*$key*" -or $key -like "*$k*") { $found=$true; break } }
    if (!$found) {
        $seen[$key] = $true
        $allSw += @{n=$pf.n;v="(dossier)";e="-";d="-";s=0;src="Dossier"}
    }
}

# --- SMART FILTERING & DEDUP ---
Write-Host "  > Filtrage intelligent..." -ForegroundColor Gray

$updatePatterns = @(
    '(?i)\bupdate\b', '(?i)\bhotfix\b', '(?i)\bpatch\b', '(?i)\bservice pack\b',
    '(?i)\bSP\d', '(?i)\bKB\d{6,}', '(?i)\bsecurity update\b', '(?i)\bcumulative\b',
    '(?i)\bAddon\b', '(?i)\bAdd-On\b', '(?i)\bLanguage Pack\b', '(?i)\bMUI\b',
    '(?i)\bContent \d', '(?i)\bMaterial Library\b'
)
$componentPatterns = @(
    '(?i)Visual C\+\+.*Redistrib', '(?i)Microsoft \.NET', '(?i)MSVC',
    '(?i)OpenCL', '(?i)Vulkan', '(?i)DirectX', '(?i)Microsoft XNA',
    '(?i)^Microsoft-Windows-', '(?i)^Windows Driver',
    '(?i)^NVIDIA (PhysX|FrameView|Nsight|USB)', '(?i)^Intel\(R\) (Management|Chipset|Serial|USB|Rapid|Trusted)',
    '(?i)^Realtek.*Driver', '(?i)^AMD (Catalyst|Settings|Software)',
    '(?i)^Python \d.*(pip|Launcher|Documentation|Executables|Standard Library|Development|Core|Utility|Tcl|Test|Path)',
    '(?i)^Microsoft (Update|Policy)', '(?i)^Windows.*Reboot',
    '(?i)^OPC ', '(?i)^vs_', '(?i)^Microsoft ASP', '(?i)^Microsoft System CLR',
    '(?i)^Autodesk Material Library', '(?i)^Autodesk Advanced Material',
    '(?i)^Autodesk Content Service', '(?i)^Autodesk Identity Manager',
    '(?i)^Autodesk Genuine', '(?i)^Autodesk Desktop App', '(?i)^Autodesk Single Sign',
    '(?i)^Autodesk Save to Web', '(?i)^Autodesk Featured Apps',
    '(?i)^Autodesk Shared', '(?i)^Autodesk Licensing',
    '(?i)^Adobe Genuine', '(?i)^Adobe Creative Cloud',
    '(?i)^Microsoft Edge WebView', '(?i)^Windows App Runtime'
)

foreach ($sw in $allSw) {
    $name = $sw.n
    $sw.cat = "main"
    foreach ($pat in $updatePatterns) { if ($name -match $pat) { $sw.cat = "update"; break } }
    if ($sw.cat -eq "main") {
        foreach ($pat in $componentPatterns) { if ($name -match $pat) { $sw.cat = "composant"; break } }
    }
}

# Dedup: "Revit 2023" vs "Autodesk Revit 2023" -> garder le plus complet
$byLower = @{}
foreach ($sw in $allSw) { $byLower[$sw.n.ToLower().Trim()] = $sw }
$sortedKeys = $byLower.Keys | Sort-Object { $_.Length }
$hideKeys = @{}

foreach ($short in $sortedKeys) {
    if ($hideKeys[$short]) { continue }
    foreach ($long in $sortedKeys) {
        if ($short -eq $long -or $hideKeys[$long]) { continue }
        if ($long.Contains($short) -and $long.Length -gt $short.Length) {
            $diff = ($long -replace [regex]::Escape($short), '').Trim(' -_')
            $isUpdate = $false
            foreach ($pat in $updatePatterns) { if ($diff -match $pat) { $isUpdate = $true; break } }
            if (-not $isUpdate -and $diff.Length -lt 25) {
                $hideKeys[$short] = $true
                $longSw = $byLower[$long]; $shortSw = $byLower[$short]
                if (-not $longSw.e -and $shortSw.e) { $longSw.e = $shortSw.e }
                if ($longSw.s -eq 0 -and $shortSw.s -gt 0) { $longSw.s = $shortSw.s }
            }
        }
    }
}
foreach ($sw in $allSw) { if ($hideKeys[$sw.n.ToLower().Trim()]) { $sw.cat = "doublon" } }

$allSw = $allSw | Sort-Object { $_.n }
$mainCount = ($allSw | Where-Object { $_.cat -eq "main" }).Count
$updateCount = ($allSw | Where-Object { $_.cat -eq "update" }).Count
$compCount = ($allSw | Where-Object { $_.cat -eq "composant" }).Count
$dupCount = ($allSw | Where-Object { $_.cat -eq "doublon" }).Count
Write-Host "    Principaux : $mainCount | Updates : $updateCount | Composants : $compCount | Doublons : $dupCount" -ForegroundColor Gray

# --- NETWORK ---
Write-Host "  > Reseau..." -ForegroundColor Gray
$net = Get-CimInstance Win32_NetworkAdapterConfiguration | Where-Object { $_.IPEnabled } |
    ForEach-Object { @{c=$_.Description;ip=($_.IPAddress|Where-Object{$_ -match '^\d+\.\d+'})-join', ';mac=$_.MACAddress;dhcp=if($_.DHCPEnabled){"Oui"}else{"Non"}} }

# --- SERVICES ---
Write-Host "  > Services..." -ForegroundColor Gray
$svc = Get-Service | Where-Object { $_.Status -eq 'Running' -and $_.StartType -eq 'Automatic' } |
    Sort-Object DisplayName | ForEach-Object { @{n=$_.DisplayName;sc=$_.Name} }

# --- BUILD JSON DATA ---
$jsonSw = ($allSw | ForEach-Object {
    $nom = $_.n -replace '"','\"' -replace '\\','\\\\'
    $ver = $_.v -replace '"','\"' -replace '\\','\\\\'
    $ed = $_.e -replace '"','\"' -replace '\\','\\\\'
    $dt = $_.d -replace '"','\"'
    "{`"n`":`"$nom`",`"v`":`"$ver`",`"e`":`"$ed`",`"d`":`"$dt`",`"s`":$($_.s),`"src`":`"$($_.src)`",`"cat`":`"$($_.cat)`"}"
}) -join ","

# --- GENERATE HTML ---
Write-Host "  > Generation du rapport HTML..." -ForegroundColor Gray

$disksHtml = ""
foreach ($d in $disks) { $disksHtml += "<tr><td>$($d.l)</td><td>$($d.t) Go</td><td>$($d.f) Go</td><td>$($d.u) Go</td></tr>" }

$netHtml = ""
foreach ($n in $net) { $netHtml += "<tr><td>$($n.c)</td><td>$($n.ip)</td><td>$($n.mac)</td><td>$($n.dhcp)</td></tr>" }

$svcHtml = ""
foreach ($s in $svc) { $svcHtml += "<tr><td>$($s.n)</td><td>$($s.sc)</td></tr>" }

$totalSw = $allSw.Count

$html = @"
<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Inventaire $pcName - $dateFr</title>
<style>
:root{--pri:#1b4f72;--sec:#2e86c1;--acc:#1e8449;--bg:#f5f7fa;--card:#fff;--border:#ecf0f1;--txt:#2c3e50;--gray:#7f8c8d;--warn:#e67e22;--appx:#8e44ad;--dir:#e67e22}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:Calibri,'Segoe UI',sans-serif;background:var(--bg);color:var(--txt);padding:20px}
.header{background:var(--pri);color:#fff;padding:20px 25px;border-radius:8px;margin-bottom:20px;display:flex;justify-content:space-between;align-items:center}
.header h1{font-size:22px}.header p{font-size:13px;opacity:.8}
.toolbar{background:var(--card);border-radius:8px;padding:12px 18px;margin-bottom:15px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;box-shadow:0 2px 4px rgba(0,0,0,.06)}
.toolbar input[type=text]{flex:1;min-width:200px;padding:8px 12px;border:2px solid var(--border);border-radius:6px;font-size:14px}
.toolbar input:focus{outline:none;border-color:var(--sec)}
.btn{padding:7px 14px;border:none;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600;transition:.2s}
.btn-pri{background:var(--pri);color:#fff}.btn-pri:hover{background:#154360}
.btn-acc{background:var(--acc);color:#fff}.btn-acc:hover{background:#196f3d}
.btn-warn{background:var(--warn);color:#fff}.btn-warn:hover{background:#d35400}
.btn-out{background:transparent;border:2px solid var(--pri);color:var(--pri)}.btn-out:hover{background:var(--pri);color:#fff}
.btn-sm{padding:4px 10px;font-size:12px}
.stats{display:flex;gap:12px;margin-bottom:15px;flex-wrap:wrap}
.stat{background:var(--card);border-radius:8px;padding:12px 16px;flex:1;min-width:130px;box-shadow:0 2px 4px rgba(0,0,0,.06);border-left:4px solid var(--sec)}
.stat .label{font-size:11px;color:var(--gray);text-transform:uppercase}.stat .value{font-size:20px;font-weight:700;color:var(--pri)}
.section{background:var(--card);border-radius:8px;padding:18px;margin-bottom:15px;box-shadow:0 2px 4px rgba(0,0,0,.06)}
.section h2{font-size:16px;color:var(--pri);margin-bottom:12px;padding-bottom:6px;border-bottom:2px solid #ebf5fb;cursor:pointer;user-select:none}
.section h2::after{content:' \u25BC';font-size:11px;color:var(--gray)}
.section.collapsed h2::after{content:' \u25B6'}
.section.collapsed .section-body{display:none}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:var(--pri);color:#fff;padding:9px 10px;text-align:left;font-weight:600;position:sticky;top:0;cursor:pointer;user-select:none;white-space:nowrap}
th:hover{background:#154360}
th .arrow{font-size:10px;margin-left:4px;opacity:.5}
th .arrow.active{opacity:1}
td{padding:7px 10px;border-bottom:1px solid var(--border)}
tr:nth-child(even){background:#f8f9fa}
tr:hover{background:#ebf5fb}
tr.checked-row{background:#d5f5e3!important}
.cb-cell{width:35px;text-align:center}
.cb-cell input{width:16px;height:16px;cursor:pointer}
.note-cell{min-width:150px}
.note-cell input{width:100%;padding:3px 6px;border:1px solid var(--border);border-radius:4px;font-size:12px;background:transparent}
.note-cell input:focus{border-color:var(--sec);background:#fff;outline:none}
.src-reg{color:var(--pri)}.src-user{color:var(--sec)}.src-appx{color:var(--appx)}.src-dir{color:var(--dir)}
.info-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.info-item{display:flex;gap:8px;padding:4px 0}.info-item .label{font-weight:600;color:var(--pri);min-width:110px}
.drop-zone{border:3px dashed var(--border);border-radius:8px;padding:30px;text-align:center;color:var(--gray);transition:.3s;margin-bottom:15px}
.drop-zone.over{border-color:var(--sec);background:#ebf5fb;color:var(--pri)}
.drop-zone p{font-size:14px}.drop-zone .icon{font-size:36px;margin-bottom:8px}
.diff-added{background:#d5f5e3!important}.diff-missing{background:#fadbd8!important}.diff-same{background:#fff}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;margin-left:6px}
.badge-green{background:#d5f5e3;color:var(--acc)}.badge-red{background:#fadbd8;color:#c0392b}.badge-blue{background:#d6eaf8;color:var(--pri)}
.footer{text-align:center;color:var(--gray);font-size:12px;margin-top:20px;padding-top:12px;border-top:1px solid var(--border)}
.tab-bar{display:flex;gap:0;margin-bottom:15px}.tab-bar .tab{padding:10px 20px;cursor:pointer;border:2px solid var(--border);border-bottom:none;border-radius:8px 8px 0 0;background:var(--bg);font-weight:600;color:var(--gray)}
.tab-bar .tab.active{background:var(--card);color:var(--pri);border-color:var(--pri);border-bottom:2px solid var(--card);margin-bottom:-2px;position:relative;z-index:1}
.tab-content{border:2px solid var(--pri);border-radius:0 8px 8px 8px;padding:18px;background:var(--card);margin-bottom:15px}
.tab-panel{display:none}.tab-panel.active{display:block}
.counter-bar{background:var(--card);border-radius:8px;padding:10px 18px;margin-bottom:15px;display:flex;justify-content:space-between;align-items:center;box-shadow:0 2px 4px rgba(0,0,0,.06)}
@media print{.toolbar,.tab-bar,.drop-zone,.btn,.note-cell,.cb-cell,#searchBox,.counter-bar,.no-print{display:none!important}body{padding:5px}
.header{-webkit-print-color-adjust:exact;print-color-adjust:exact}
th{-webkit-print-color-adjust:exact;print-color-adjust:exact}
tr.hide-print{display:none!important}}
</style>
</head>
<body>

<div class="header">
  <div><h1>$pcName</h1><p>Inventaire du $dateFr</p></div>
  <div style="text-align:right"><div style="font-size:28px;font-weight:700">$totalSw</div><div style="font-size:12px;opacity:.7">logiciels</div></div>
</div>

<!-- TABS -->
<div class="tab-bar no-print">
  <div class="tab active" onclick="switchTab('inventory')">Inventaire</div>
  <div class="tab" onclick="switchTab('compare')">Comparaison</div>
</div>

<!-- ==================== TAB INVENTAIRE ==================== -->
<div class="tab-content">
<div id="tab-inventory" class="tab-panel active">

<div class="toolbar no-print">
  <input type="text" id="searchBox" placeholder="Rechercher un logiciel..." oninput="filterTable()">
  <button class="btn btn-acc" onclick="toggleCheckedOnly()">Importants uniquement</button>
  <button class="btn btn-pri" onclick="window.print()">Imprimer / PDF</button>
  <button class="btn btn-out" onclick="uncheckAll()">Tout decocher</button>
</div>

<div class="counter-bar no-print">
  <span>Logiciels importants coches : <strong id="checkedCount">0</strong> / $totalSw</span>
  <span id="filterStatus" style="color:var(--gray);font-size:13px"></span>
</div>

<div class="stats">
  <div class="stat"><div class="label">Principaux</div><div class="value">$mainCount</div></div>
  <div class="stat"><div class="label">Updates masques</div><div class="value">$updateCount</div></div>
  <div class="stat"><div class="label">Composants masques</div><div class="value">$compCount</div></div>
  <div class="stat"><div class="label">Doublons retires</div><div class="value">$dupCount</div></div>
  <div class="stat"><div class="label">RAM</div><div class="value">$ramGo Go</div></div>
</div>

<div class="section">
  <h2 onclick="toggleSection(this)">Informations systeme</h2>
  <div class="section-body">
    <div class="info-grid">
      <div class="info-item"><span class="label">Nom</span><span>$pcName</span></div>
      <div class="info-item"><span class="label">OS</span><span>$osInfo</span></div>
      <div class="info-item"><span class="label">CPU</span><span>$cpuInfo</span></div>
      <div class="info-item"><span class="label">RAM</span><span>$ramGo Go</span></div>
      <div class="info-item"><span class="label">Fabricant</span><span>$fabricant</span></div>
      <div class="info-item"><span class="label">N serie</span><span>$numSerie</span></div>
      <div class="info-item"><span class="label">Domaine</span><span>$domaine</span></div>
      <div class="info-item"><span class="label">Utilisateur</span><span>$env:USERNAME</span></div>
    </div>
  </div>
</div>

<div class="section">
  <h2 onclick="toggleSection(this)">Disques</h2>
  <div class="section-body">
    <table><tr><th>Lettre</th><th>Taille</th><th>Libre</th><th>Utilise</th></tr>$disksHtml</table>
  </div>
</div>

<div class="section">
  <h2 onclick="toggleSection(this)">Reseau</h2>
  <div class="section-body">
    <table><tr><th>Carte</th><th>IP</th><th>MAC</th><th>DHCP</th></tr>$netHtml</table>
  </div>
</div>

<div class="section">
  <h2 onclick="toggleSection(this)">Logiciels ($totalSw)</h2>
  <div class="section-body">
    <div style="margin-bottom:12px;display:flex;gap:6px;flex-wrap:wrap;align-items:center">
      <span style="font-size:13px;color:var(--gray);margin-right:4px">Afficher :</span>
      <button onclick="toggleCat('main')" id="btn-main" class="btn btn-sm" style="background:#d6eaf8;color:#1b4f72;border:2px solid #1b4f72;font-weight:600">Principaux ($mainCount)</button>
      <button onclick="toggleCat('update')" id="btn-update" class="btn btn-sm" style="background:transparent;color:#e67e22;border:2px solid #e67e22;font-weight:600;opacity:.5">Updates ($updateCount)</button>
      <button onclick="toggleCat('composant')" id="btn-composant" class="btn btn-sm" style="background:transparent;color:#7f8c8d;border:2px solid #7f8c8d;font-weight:600;opacity:.5">Composants ($compCount)</button>
      <button onclick="toggleCat('doublon')" id="btn-doublon" class="btn btn-sm" style="background:transparent;color:#c0392b;border:2px solid #c0392b;font-weight:600;opacity:.5">Doublons ($dupCount)</button>
    </div>
    <table id="softTable">
      <thead><tr>
        <th class="cb-cell no-print"><input type="checkbox" id="checkAll" onchange="toggleAll(this)" title="Tout cocher"></th>
        <th onclick="sortTable(1)">Nom <span class="arrow" id="arrow1">&#9650;</span></th>
        <th onclick="sortTable(2)">Version <span class="arrow" id="arrow2"></span></th>
        <th onclick="sortTable(3)">Editeur <span class="arrow" id="arrow3"></span></th>
        <th onclick="sortTable(4)">Date <span class="arrow" id="arrow4"></span></th>
        <th onclick="sortTable(5)">Taille (Mo) <span class="arrow" id="arrow5"></span></th>
        <th onclick="sortTable(6)">Source <span class="arrow" id="arrow6"></span></th>
        <th class="note-cell no-print">Notes</th>
      </tr></thead>
      <tbody id="softBody"></tbody>
    </table>
  </div>
</div>

<div class="section">
  <h2 onclick="toggleSection(this)">Services automatiques ($($svc.Count))</h2>
  <div class="section-body">
    <table><tr><th>Nom</th><th>Nom court</th></tr>$svcHtml</table>
  </div>
</div>

</div><!-- /tab-inventory -->

<!-- ==================== TAB COMPARAISON ==================== -->
<div id="tab-compare" class="tab-panel">

<div class="drop-zone" id="dropZone">
  <div class="icon">&#128196; &#8644; &#128196;</div>
  <p>Glissez un autre fichier d'inventaire HTML ici pour comparer</p>
  <p style="font-size:12px;margin-top:5px">(ou cliquez pour parcourir)</p>
  <input type="file" id="fileInput" accept=".html" style="display:none" onchange="handleFile(this.files[0])">
</div>

<div id="compareResult" style="display:none">
  <div class="stats" id="compareStats"></div>
  <div class="section">
    <h2>Resultat de la comparaison</h2>
    <div class="section-body">
      <div style="margin-bottom:10px;font-size:13px">
        <span class="badge badge-green">Present ici, absent dans l'autre</span>
        <span class="badge badge-red">Absent ici, present dans l'autre</span>
        <span class="badge badge-blue">Present dans les deux</span>
      </div>
      <table id="compareTable">
        <thead><tr><th>Logiciel</th><th>Version (ce master)</th><th>Version (autre master)</th><th>Statut</th></tr></thead>
        <tbody id="compareBody"></tbody>
      </table>
    </div>
  </div>
</div>

</div><!-- /tab-compare -->
</div><!-- /tab-content -->

<div class="footer">Inventaire v3 - $pcName - $dateFr</div>

<!-- EMBEDDED DATA FOR COMPARISON -->
<script id="inventoryData" type="application/json">{"pcName":"$pcName","date":"$dateFr","software":[$jsonSw]}</script>

<script>
// ===== DATA & STATE =====
const PC_NAME = "$pcName";
const STORAGE_KEY = "inv_" + PC_NAME;
let showCheckedOnly = false;
let currentSort = {col:1, asc:true};
let visibleCats = {main:true, update:false, composant:false, doublon:false};
let softData = JSON.parse(document.getElementById('inventoryData').textContent).software;

// ===== INIT =====
window.addEventListener('DOMContentLoaded', function() {
  renderTable();
  loadState();
  updateCounter();
  initDropZone();
});

// ===== RENDER SOFTWARE TABLE =====
function renderTable() {
  const tbody = document.getElementById('softBody');
  let html = '';
  for (let i = 0; i < softData.length; i++) {
    const s = softData[i];
    const srcClass = s.src === 'Registre' ? 'src-reg' : s.src === 'Utilisateur' ? 'src-user' : s.src === 'AppX' ? 'src-appx' : 'src-dir';
    const sizeStr = s.s > 0 ? s.s.toFixed(1) : '-';
    const cat = s.cat || 'main';
    const catBadge = cat === 'update' ? ' <span style="font-size:10px;padding:1px 6px;border-radius:3px;background:#fdebd0;color:#e67e22">update</span>' :
                     cat === 'composant' ? ' <span style="font-size:10px;padding:1px 6px;border-radius:3px;background:#f2f3f4;color:#7f8c8d">composant</span>' :
                     cat === 'doublon' ? ' <span style="font-size:10px;padding:1px 6px;border-radius:3px;background:#fadbd8;color:#c0392b">doublon</span>' : '';
    const rowOpacity = cat !== 'main' ? ' style="opacity:.65"' : '';
    html += '<tr id="row'+i+'" data-idx="'+i+'"'+rowOpacity+'>' +
      '<td class="cb-cell no-print"><input type="checkbox" onchange="onCheck('+i+')" id="cb'+i+'"></td>' +
      '<td>'+esc(s.n)+catBadge+'</td><td>'+esc(s.v)+'</td><td>'+esc(s.e)+'</td><td>'+s.d+'</td><td>'+sizeStr+'</td>' +
      '<td class="'+srcClass+'">'+s.src+'</td>' +
      '<td class="note-cell no-print"><input type="text" placeholder="Note..." onchange="onNote('+i+',this.value)" id="note'+i+'"></td></tr>';
  }
  tbody.innerHTML = html;
}

function esc(s) { if(!s) return '-'; return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// ===== CHECKBOX & NOTES =====
function onCheck(i) {
  const row = document.getElementById('row'+i);
  const cb = document.getElementById('cb'+i);
  row.classList.toggle('checked-row', cb.checked);
  if (!cb.checked && showCheckedOnly) row.style.display = 'none';
  saveState();
  updateCounter();
}

function onNote(i, val) { saveState(); }

function toggleAll(master) {
  for (let i = 0; i < softData.length; i++) {
    const cb = document.getElementById('cb'+i);
    const row = document.getElementById('row'+i);
    if (row.style.display !== 'none' || master.checked) {
      cb.checked = master.checked;
      row.classList.toggle('checked-row', master.checked);
    }
  }
  saveState(); updateCounter();
}

function uncheckAll() {
  for (let i = 0; i < softData.length; i++) {
    document.getElementById('cb'+i).checked = false;
    document.getElementById('row'+i).classList.remove('checked-row');
  }
  showCheckedOnly = false;
  document.getElementById('filterStatus').textContent = '';
  filterTable();
  saveState(); updateCounter();
}

function updateCounter() {
  let count = 0;
  for (let i = 0; i < softData.length; i++) { if (document.getElementById('cb'+i).checked) count++; }
  document.getElementById('checkedCount').textContent = count;
}

// ===== SAVE / LOAD STATE (localStorage) =====
function saveState() {
  const state = {};
  for (let i = 0; i < softData.length; i++) {
    const cb = document.getElementById('cb'+i).checked;
    const note = document.getElementById('note'+i).value;
    if (cb || note) state[softData[i].n] = {c:cb, t:note};
  }
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch(e) {}
}

function loadState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const state = JSON.parse(raw);
    for (let i = 0; i < softData.length; i++) {
      const s = state[softData[i].n];
      if (s) {
        if (s.c) { document.getElementById('cb'+i).checked = true; document.getElementById('row'+i).classList.add('checked-row'); }
        if (s.t) document.getElementById('note'+i).value = s.t;
      }
    }
  } catch(e) {}
}

// ===== SEARCH & FILTER =====
function filterTable() {
  const q = document.getElementById('searchBox').value.toLowerCase();
  const rows = document.getElementById('softBody').rows;
  for (let i = 0; i < rows.length; i++) {
    const idx = parseInt(rows[i].dataset.idx);
    const cb = document.getElementById('cb'+idx);
    const cat = softData[idx].cat || 'main';
    const matchSearch = !q || rows[i].textContent.toLowerCase().includes(q);
    const matchChecked = !showCheckedOnly || cb.checked;
    const matchCat = visibleCats[cat] === true;
    const show = matchSearch && matchChecked && matchCat;
    rows[i].style.display = show ? '' : 'none';
    rows[i].classList.toggle('hide-print', !show);
  }
}

function toggleCat(cat) {
  visibleCats[cat] = !visibleCats[cat];
  const btn = document.getElementById('btn-'+cat);
  if (btn) {
    btn.style.opacity = visibleCats[cat] ? '1' : '.5';
    btn.style.background = visibleCats[cat] ? (cat==='main'?'#d6eaf8':cat==='update'?'#fdebd0':cat==='composant'?'#f2f3f4':'#fadbd8') : 'transparent';
  }
  filterTable();
}

function toggleCheckedOnly() {
  showCheckedOnly = !showCheckedOnly;
  document.getElementById('filterStatus').textContent = showCheckedOnly ? 'Filtre : importants uniquement' : '';
  filterTable();
}

// ===== SORT =====
function sortTable(col) {
  if (currentSort.col === col) { currentSort.asc = !currentSort.asc; }
  else { currentSort.col = col; currentSort.asc = true; }
  // Update arrows
  for (let c = 1; c <= 6; c++) {
    const a = document.getElementById('arrow'+c);
    if (c === col) { a.innerHTML = currentSort.asc ? '&#9650;' : '&#9660;'; a.classList.add('active'); }
    else { a.innerHTML = ''; a.classList.remove('active'); }
  }
  const tbody = document.getElementById('softBody');
  const rowsArr = Array.from(tbody.rows);
  rowsArr.sort(function(a,b) {
    let va = a.cells[col].textContent.trim();
    let vb = b.cells[col].textContent.trim();
    if (col === 5) { va = parseFloat(va) || 0; vb = parseFloat(vb) || 0; return currentSort.asc ? va-vb : vb-va; }
    va = va.toLowerCase(); vb = vb.toLowerCase();
    if (va < vb) return currentSort.asc ? -1 : 1;
    if (va > vb) return currentSort.asc ? 1 : -1;
    return 0;
  });
  rowsArr.forEach(function(r) { tbody.appendChild(r); });
}

// ===== COLLAPSIBLE SECTIONS =====
function toggleSection(h2) { h2.parentElement.classList.toggle('collapsed'); }

// ===== TABS =====
function switchTab(tab) {
  document.querySelectorAll('.tab').forEach(function(t,i) { t.classList.toggle('active', (tab==='inventory'?i===0:i===1)); });
  document.getElementById('tab-inventory').classList.toggle('active', tab==='inventory');
  document.getElementById('tab-compare').classList.toggle('active', tab!=='inventory');
}

// ===== COMPARISON =====
function initDropZone() {
  const dz = document.getElementById('dropZone');
  const fi = document.getElementById('fileInput');
  dz.addEventListener('click', function() { fi.click(); });
  dz.addEventListener('dragover', function(e) { e.preventDefault(); dz.classList.add('over'); });
  dz.addEventListener('dragleave', function() { dz.classList.remove('over'); });
  dz.addEventListener('drop', function(e) { e.preventDefault(); dz.classList.remove('over'); if(e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]); });
}

function handleFile(file) {
  if (!file || !file.name.endsWith('.html')) { alert('Veuillez glisser un fichier HTML d\'inventaire'); return; }
  const reader = new FileReader();
  reader.onload = function(e) {
    try {
      const parser = new DOMParser();
      const doc = parser.parseFromString(e.target.result, 'text/html');
      const dataEl = doc.getElementById('inventoryData');
      if (!dataEl) { alert('Ce fichier ne contient pas de donnees d\'inventaire v3'); return; }
      const otherData = JSON.parse(dataEl.textContent);
      doCompare(otherData);
    } catch(err) { alert('Erreur de lecture : '+err.message); }
  };
  reader.readAsText(file);
}

function doCompare(other) {
  const myMap = {}; softData.forEach(function(s) { myMap[s.n.toLowerCase()] = s; });
  const otherMap = {}; other.software.forEach(function(s) { otherMap[s.n.toLowerCase()] = s; });
  const allKeys = new Set([...Object.keys(myMap), ...Object.keys(otherMap)]);
  let rows = [];
  let added=0, missing=0, common=0;
  allKeys.forEach(function(k) {
    const mine = myMap[k]; const theirs = otherMap[k];
    if (mine && theirs) { common++; rows.push({n:mine.n, vMe:mine.v, vOther:theirs.v, status:'commun', cls:'diff-same'}); }
    else if (mine && !theirs) { added++; rows.push({n:mine.n, vMe:mine.v, vOther:'-', status:'uniquement ici', cls:'diff-added'}); }
    else { missing++; rows.push({n:theirs.n, vMe:'-', vOther:theirs.v, status:'manquant ici', cls:'diff-missing'}); }
  });
  rows.sort(function(a,b) { return a.n.toLowerCase().localeCompare(b.n.toLowerCase()); });

  document.getElementById('compareStats').innerHTML =
    '<div class="stat" style="border-left-color:var(--acc)"><div class="label">Uniquement ici</div><div class="value" style="color:var(--acc)">'+added+'</div></div>' +
    '<div class="stat" style="border-left-color:#c0392b"><div class="label">Manquants ici</div><div class="value" style="color:#c0392b">'+missing+'</div></div>' +
    '<div class="stat" style="border-left-color:var(--sec)"><div class="label">En commun</div><div class="value">'+common+'</div></div>' +
    '<div class="stat"><div class="label">Compare avec</div><div class="value" style="font-size:14px">'+other.pcName+'</div></div>';

  let tbody = '';
  rows.forEach(function(r) {
    const badge = r.status==='commun'?'badge-blue':r.status==='uniquement ici'?'badge-green':'badge-red';
    tbody += '<tr class="'+r.cls+'"><td>'+esc(r.n)+'</td><td>'+esc(r.vMe)+'</td><td>'+esc(r.vOther)+'</td><td><span class="badge '+badge+'">'+r.status+'</span></td></tr>';
  });
  document.getElementById('compareBody').innerHTML = tbody;
  document.getElementById('compareResult').style.display = 'block';
  document.getElementById('dropZone').innerHTML = '<div class="icon">&#9989;</div><p>Comparaison avec <strong>'+other.pcName+'</strong> ('+other.date+')</p><p style="font-size:12px;margin-top:5px">Glissez un autre fichier pour une nouvelle comparaison</p>';
}
</script>
</body>
</html>
"@

# --- SAVE ---
$outputDir = Join-Path $scriptDir "INVENTAIRES"
if (!(Test-Path $outputDir)) { New-Item -ItemType Directory -Path $outputDir -Force | Out-Null }
$fileName = "${pcName}_${dateNow}.html"
$html | Out-File -FilePath (Join-Path $outputDir $fileName) -Encoding UTF8

# Alias for CSV export
$software = $allSw | ForEach-Object { [PSCustomObject]@{Nom=$_.n;Version=$_.v;Editeur=$_.e;Date=$_.d;Taille=$_.s;Source=$_.src;Categorie=$_.cat} }
$software | Export-Csv -Path (Join-Path $outputDir "${pcName}_${dateNow}_logiciels.csv") -NoTypeInformation -Encoding UTF8 -Delimiter ";"

Write-Host ""
Write-Host "  TERMINE !" -ForegroundColor Green
Write-Host "  Rapport : $fileName" -ForegroundColor Green
Write-Host "  Dossier : $outputDir" -ForegroundColor Green
Write-Host "  Total   : $totalSw logiciels detectes" -ForegroundColor Green
Write-Host "    Principaux : $mainCount (affiches par defaut)" -ForegroundColor Cyan
Write-Host "    Updates    : $updateCount (masques)" -ForegroundColor Yellow
Write-Host "    Composants : $compCount (masques)" -ForegroundColor Gray
Write-Host "    Doublons   : $dupCount (retires)" -ForegroundColor DarkGray
Write-Host ""
