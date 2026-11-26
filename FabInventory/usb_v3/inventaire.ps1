#requires -Version 5.1
<#
.SYNOPSIS
    FabInventory - inventaire de poste Windows.

.DESCRIPTION
    Collecte le materiel, les disques, le reseau, les services automatiques et
    les logiciels installes, puis produit :
      - un rapport HTML autonome (consultation, recherche, notes, comparaison) ;
      - un CSV a cote du HTML ;
      - un bloc JSON schema 3 lisible par FabInventory.

    Le script ne necessite aucun module externe. Il fonctionne avec Windows
    PowerShell 5.1 et PowerShell 7+. Les erreurs de collecte (WMI indisponible,
    AppX refuse, etc.) sont consignees dans le rapport sans interrompre le reste.

.PARAMETER OutputPath
    Fichier HTML a creer. Si le chemin est un dossier, le nom est genere.

.PARAMETER OutputDirectory
    Dossier de sortie utilise quand OutputPath est absent.

.PARAMETER NoGui
    N'affiche aucune fenetre. Le fichier est ecrit dans OutputDirectory ou dans
    le dossier du script (sous INVENTAIRES).

.PARAMETER SkipAppx
    Ne collecte pas les applications Microsoft Store / AppX.

.PARAMETER SkipFolderScan
    Ne scanne pas les dossiers Program Files.

.PARAMETER SkipServices
    Ne collecte pas les services automatiques en cours d'execution.

.PARAMETER SelfTest
    Genere un rapport avec des donnees synthetiques, sans interroger Windows.
    Utile pour verifier le JSON, l'echappement et le rendu du rapport.

.EXAMPLE
    .\inventaire_master.ps1

.EXAMPLE
    .\inventaire_master.ps1 -NoGui -OutputDirectory C:\Temp\Inventaires

.EXAMPLE
    .\inventaire_master.ps1 -SelfTest -OutputPath C:\Temp\selftest.html
#>
[CmdletBinding()]
param(
    [string]$OutputPath = "",
    [string]$OutputDirectory = "",
    [switch]$NoGui,
    [switch]$SkipAppx,
    [switch]$SkipFolderScan,
    [switch]$SkipServices,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$script:ScriptVersion = "3.1.0"
$script:Warnings = New-Object System.Collections.Generic.List[string]
$script:FormsReady = $false
$script:RunningOnWindows = ($env:OS -eq "Windows_NT")

function Add-Warning {
    param([string]$Message)
    if ([string]::IsNullOrWhiteSpace($Message)) { return }
    $script:Warnings.Add($Message) | Out-Null
    Write-Host "  ! $Message" -ForegroundColor Yellow
}

function Get-Text {
    param($Value)
    if ($null -eq $Value) { return "" }
    return ([string]$Value).Trim()
}

function Get-Number {
    param($Value, [double]$Default = 0)
    if ($null -eq $Value) { return $Default }
    try {
        $text = ([string]$Value).Trim().Replace(",", ".")
        if ([string]::IsNullOrWhiteSpace($text)) { return $Default }
        return [double]::Parse($text, [Globalization.CultureInfo]::InvariantCulture)
    }
    catch {
        return $Default
    }
}

function Escape-Html {
    param($Value)
    $text = Get-Text $Value
    if ($text.Length -eq 0) { return "" }
    return ($text -replace "&", "&amp;" -replace "<", "&lt;" -replace ">", "&gt;" -replace '"', "&quot;" -replace "'", "&#39;")
}

function Safe-FilePart {
    param([string]$Value, [string]$Fallback = "poste")
    $part = [regex]::Replace((Get-Text $Value), "[^A-Za-z0-9._-]+", "-").Trim("-._")
    if ([string]::IsNullOrWhiteSpace($part)) { return $Fallback }
    if ($part.Length -gt 60) { return $part.Substring(0, 60).TrimEnd("-._") }
    return $part
}

function ConvertTo-InventoryDate {
    param($Value)
    $text = Get-Text $Value
    if ([string]::IsNullOrWhiteSpace($text) -or $text -eq "-") { return "-" }
    $formats = @(
        "yyyyMMdd",
        "yyyy-MM-dd",
        "yyyy/MM/dd",
        "dd/MM/yyyy",
        "MM/dd/yyyy",
        "yyyyMMddHHmmss"
    )
    foreach ($format in $formats) {
        try {
            $date = [datetime]::ParseExact(
                $text,
                $format,
                [Globalization.CultureInfo]::InvariantCulture
            )
            return $date.ToString("dd/MM/yyyy")
        }
        catch { }
    }
    try {
        $date = [datetime]::Parse($text, [Globalization.CultureInfo]::InvariantCulture)
        if ($date.Year -ge 1990 -and $date.Year -le 2100) {
            return $date.ToString("dd/MM/yyyy")
        }
    }
    catch { }
    return $text
}

function Get-SoftwareCategory {
    param([string]$Name, [string]$Editor)
    $n = (Get-Text $Name).ToLowerInvariant()
    $e = (Get-Text $Editor).ToLowerInvariant()

    if ($n -match "(^|\s)(kb\d{4,}|update|hotfix|patch|cumulative|service pack|security update|mise\s+[\u00e0a]\s+jour|correctif)(\s|$)") {
        return "update"
    }
    if ($n -match "(^|\s)(runtime|redistributable|framework|sdk|driver|component|plugin|library|module|webview2)(\s|$)") {
        return "composant"
    }
    if ($e -match "(microsoft|intel|nvidia|amd)" -and $n -match "(driver|runtime|framework|redistributable)") {
        return "composant"
    }
    return "main"
}

function Test-UpdateName {
    param([string]$Name)
    return (Get-SoftwareCategory -Name $Name -Editor "") -eq "update"
}

function New-SoftwareRecord {
    param(
        [string]$Name,
        [string]$Version,
        [string]$Editor,
        $InstallDate,
        $SizeMb,
        [string]$Source
    )
    $cleanName = Get-Text $Name
    $cleanEditor = Get-Text $Editor
    return [ordered]@{
        n   = $cleanName
        v   = Get-Text $Version
        e   = $cleanEditor
        d   = ConvertTo-InventoryDate $InstallDate
        s   = [math]::Round((Get-Number $SizeMb), 1)
        src = Get-Text $Source
        cat = Get-SoftwareCategory -Name $cleanName -Editor $cleanEditor
    }
}

function Get-CimData {
    param([string]$ClassName, [string]$Filter = "")
    if (-not $script:RunningOnWindows) { return @() }
    $params = @{ ClassName = $ClassName; ErrorAction = "Stop" }
    if (-not [string]::IsNullOrWhiteSpace($Filter)) {
        $params["Filter"] = $Filter
    }
    try {
        return @(Get-CimInstance @params)
    }
    catch {
        Add-Warning "WMI $ClassName indisponible : $($_.Exception.Message)"
        return @()
    }
}

function Get-CimFirst {
    param([string]$ClassName, [string]$Filter = "")
    $items = @(Get-CimData -ClassName $ClassName -Filter $Filter)
    if ($items.Count -gt 0) { return $items[0] }
    return $null
}

function Get-WindowsReleaseLabel {
    try {
        $cv = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion" -ErrorAction Stop
        $displayVersion = Get-Text $cv.DisplayVersion
        $releaseId = Get-Text $cv.ReleaseId
        $build = Get-Text $cv.CurrentBuild
        $ubr = Get-Text $cv.UBR
        $versionLabel = ""
        if ($displayVersion) { $versionLabel = $displayVersion }
        elseif ($releaseId) { $versionLabel = $releaseId }
        $buildLabel = ""
        if ($build) {
            if ($ubr) { $buildLabel = "$build.$ubr" }
            else { $buildLabel = $build }
        }
        if ($versionLabel -and $buildLabel) { return "$versionLabel (build $buildLabel)" }
        if ($versionLabel) { return $versionLabel }
        if ($buildLabel) { return "build $buildLabel" }
    }
    catch { }
    return ""
}

function Get-SystemInformation {
    $os = Get-CimFirst "Win32_OperatingSystem"
    $cs = Get-CimFirst "Win32_ComputerSystem"
    $cpu = Get-CimFirst "Win32_Processor"
    $bios = Get-CimFirst "Win32_BIOS"

    $caption = ""
    $osVersion = ""
    $build = ""
    $architecture = ""
    if ($os) {
        $caption = Get-Text $os.Caption
        $osVersion = Get-Text $os.Version
        $build = Get-Text $os.BuildNumber
        $architecture = Get-Text $os.OSArchitecture
    }
    if (-not $caption) {
        $caption = [Environment]::OSVersion.VersionString
    }

    $cpuName = ""
    $cores = 0
    $threads = 0
    $clock = 0
    if ($cpu) {
        $cpuName = Get-Text $cpu.Name
        $cores = [int](Get-Number $cpu.NumberOfCores)
        $threads = [int](Get-Number $cpu.NumberOfLogicalProcessors)
        $clock = [int](Get-Number $cpu.MaxClockSpeed)
    }

    $ram = 0
    $manufacturer = ""
    $model = ""
    $domain = ""
    if ($cs) {
        $ram = [math]::Round((Get-Number $cs.TotalPhysicalMemory) / 1GB, 1)
        $manufacturer = Get-Text $cs.Manufacturer
        $model = Get-Text $cs.Model
        $domain = Get-Text $cs.Domain
    }

    $serial = ""
    if ($bios) { $serial = Get-Text $bios.SerialNumber }

    $cpuLabel = $cpuName
    if ($cores -gt 0 -and $threads -gt 0) {
        $cpuLabel = "$cpuName ($cores coeurs / $threads threads)"
    }
    elseif ($cores -gt 0) {
        $cpuLabel = "$cpuName ($cores coeurs)"
    }
    if ($clock -gt 0) { $cpuLabel = "$cpuLabel @ $clock MHz" }

    return [ordered]@{
        os              = $caption
        windows_version = Get-WindowsReleaseLabel
        os_version      = $osVersion
        os_build        = $build
        architecture    = $architecture
        cpu             = $cpuLabel
        cpu_name        = $cpuName
        cpu_cores       = $cores
        cpu_threads     = $threads
        cpu_mhz         = $clock
        ram             = $ram
        fabricant       = $manufacturer
        modele          = $model
        num_serie       = $serial
        domaine         = $domain
        utilisateur     = "$env:USERDOMAIN\$env:USERNAME"
    }
}

function Get-Disks {
    $items = @()
    foreach ($disk in @(Get-CimData -ClassName "Win32_LogicalDisk" -Filter "DriveType=3")) {
        $size = [math]::Round((Get-Number $disk.Size) / 1GB, 1)
        $free = [math]::Round((Get-Number $disk.FreeSpace) / 1GB, 1)
        $used = [math]::Round($size - $free, 1)
        if ($used -lt 0) { $used = 0 }
        $label = Get-Text $disk.DeviceID
        $items += [ordered]@{
            label      = $label
            name       = $label
            volume     = Get-Text $disk.VolumeName
            filesystem = Get-Text $disk.FileSystem
            size_go    = $size
            free_go    = $free
            used_go    = $used
        }
    }
    return @($items | Sort-Object { $_.label })
}

function Get-NetworkInformation {
    $items = @()
    foreach ($adapter in @(Get-CimData -ClassName "Win32_NetworkAdapterConfiguration" -Filter "IPEnabled=TRUE")) {
        $addresses = @($adapter.IPAddress | Where-Object { (Get-Text $_) -match "^\d{1,3}(\.\d{1,3}){3}$" })
        $ip = ""
        if ($addresses.Count -gt 0) { $ip = Get-Text $addresses[0] }
        $gateways = @($adapter.DefaultIPGateway | Where-Object { $null -ne $_ } | ForEach-Object { Get-Text $_ })
        $dns = @($adapter.DNSServerSearchOrder | Where-Object { $null -ne $_ } | ForEach-Object { Get-Text $_ })
        $dhcp = "Non"
        if ($adapter.DHCPEnabled -eq $true) { $dhcp = "Oui" }
        $items += [ordered]@{
            name    = Get-Text $adapter.Description
            ip      = $ip
            mac     = Get-Text $adapter.MACAddress
            dhcp    = $dhcp
            gateway = ($gateways -join ", ")
            dns     = ($dns -join ", ")
        }
    }
    return @($items | Sort-Object { $_.name })
}

function Get-AutomaticServices {
    $items = @()
    foreach ($service in @(Get-CimData -ClassName "Win32_Service" -Filter "State='Running' AND StartMode='Auto'")) {
        $items += [ordered]@{
            n      = Get-Text $service.DisplayName
            sc     = Get-Text $service.Name
            status = Get-Text $service.State
            start  = Get-Text $service.StartMode
        }
    }
    if ($items.Count -eq 0) {
        try {
            foreach ($service in @(Get-Service -ErrorAction Stop | Where-Object {
                $_.Status -eq "Running" -and $_.StartType -eq "Automatic"
            })) {
                $items += [ordered]@{
                    n      = Get-Text $service.DisplayName
                    sc     = Get-Text $service.Name
                    status = "Running"
                    start  = "Auto"
                }
            }
        }
        catch { }
    }
    return @($items | Sort-Object { $_.n })
}

function Get-RegistrySoftware {
    param([string]$SourceLabel)
    $items = @()
    if ($SourceLabel -eq "Utilisateur") {
        $paths = @("HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*")
    }
    else {
        $paths = @(
            "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
            "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
        )
    }

    foreach ($path in $paths) {
        try {
            $properties = @(Get-ItemProperty -Path $path -ErrorAction SilentlyContinue)
        }
        catch {
            Add-Warning "Registre inaccessible : $path"
            continue
        }
        foreach ($item in $properties) {
            $name = Get-Text $item.DisplayName
            if ([string]::IsNullOrWhiteSpace($name)) { continue }
            if ($name -match "^(Update for|Security Update|Hotfix|KB\d)") { continue }
            $sizeKb = Get-Number $item.EstimatedSize
            $sizeMb = 0
            if ($sizeKb -gt 0) { $sizeMb = $sizeKb / 1024 }
            $items += New-SoftwareRecord `
                -Name $name `
                -Version (Get-Text $item.DisplayVersion) `
                -Editor (Get-Text $item.Publisher) `
                -InstallDate $item.InstallDate `
                -SizeMb $sizeMb `
                -Source $SourceLabel
        }
    }
    return @($items)
}

function Get-AppxSoftware {
    if (-not $script:RunningOnWindows) { return @() }
    try {
        $packages = @(Get-AppxPackage -AllUsers -ErrorAction Stop)
    }
    catch {
        try {
            $packages = @(Get-AppxPackage -ErrorAction Stop)
        }
        catch {
            Add-Warning "Applications AppX indisponibles : $($_.Exception.Message)"
            return @()
        }
    }

    $systemAppPattern = "^(Microsoft\.(NET|VCLibs|UI\.Xaml|Services|WindowsAppRuntime|DesktopAppInstaller|StorePurchaseApp|Xbox|Zune|Advertising|AccountsControl|Credential|LockApp|Media|OOBE|Passport|PPI|Print|SecHealthUI|Search|Shell|Speech|Store|VCLibs|WebView|Windows|WindowsApp))"
    $items = @()
    foreach ($package in $packages) {
        if ($package.IsFramework -eq $true) { continue }
        if ($package.SignatureKind -eq "System") { continue }
        $name = Get-Text $package.Name
        if ([string]::IsNullOrWhiteSpace($name)) { continue }
        if ($name -match $systemAppPattern) { continue }
        $items += New-SoftwareRecord `
            -Name $name `
            -Version (Get-Text $package.Version) `
            -Editor (Get-Text $package.PublisherDisplayName) `
            -InstallDate "" `
            -SizeMb 0 `
            -Source "AppX"
    }
    return @($items)
}

function Get-FolderSoftware {
    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)})
    if ($env:LOCALAPPDATA) {
        $roots += (Join-Path $env:LOCALAPPDATA "Programs")
        $roots += (Join-Path $env:LOCALAPPDATA "Autodesk")
    }
    $roots = @($roots | Where-Object { $_ } | Select-Object -Unique)
    $skipNames = "^(Common Files|WindowsApps|Windows|Microsoft|Uninstall|Reference|InstallShield|Temp|Package Cache|ModifiableWindowsApps)$"
    $items = @()
    $count = 0
    $maxItems = 600

    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root -PathType Container)) { continue }
        $remaining = $maxItems - $count
        if ($remaining -le 0) { break }
        try {
            $folders = @(Get-ChildItem -LiteralPath $root -Directory -ErrorAction Stop |
                Where-Object { $_.Name -notmatch $skipNames } |
                Sort-Object Name |
                Select-Object -First $remaining)
        }
        catch {
            Add-Warning "Dossier inaccessible : $root"
            continue
        }
        foreach ($folder in $folders) {
            if ($count -ge $maxItems) { break }
            $items += New-SoftwareRecord `
                -Name $folder.Name `
                -Version "(dossier)" `
                -Editor "" `
                -InstallDate "" `
                -SizeMb 0 `
                -Source "Dossier"
            $count++
        }
        if ($count -ge $maxItems) {
            Add-Warning "Scan des dossiers limite a $maxItems entrees."
            break
        }
    }
    return @($items)
}

function Merge-Software {
    param([object[]]$Records)

    $byName = [ordered]@{}
    foreach ($record in @($Records)) {
        $key = (Get-Text $record.n).ToLowerInvariant()
        if ([string]::IsNullOrWhiteSpace($key)) { continue }
        if (-not $byName.Contains($key)) {
            $byName[$key] = $record
            continue
        }
        $existing = $byName[$key]
        if (($existing.v -eq "" -or $existing.v -eq "(dossier)") -and $record.v -ne "" -and $record.v -ne "(dossier)") {
            $existing.v = $record.v
        }
        if (($existing.e -eq "" -or $existing.e -eq "-") -and $record.e -ne "") {
            $existing.e = $record.e
        }
        if ($existing.s -eq 0 -and $record.s -gt 0) {
            $existing.s = $record.s
        }
        if (($existing.d -eq "-" -or $existing.d -eq "") -and $record.d -ne "-") {
            $existing.d = $record.d
        }
    }

    $unique = @($byName.Values)
    $hidden = @{}
    $ordered = @($unique | Sort-Object { $_.n.Length })
    foreach ($short in $ordered) {
        $shortKey = (Get-Text $short.n).ToLowerInvariant()
        if ($shortKey.Length -lt 3 -or $hidden.ContainsKey($shortKey)) { continue }
        foreach ($long in $ordered) {
            if ([object]::ReferenceEquals($short, $long)) { continue }
            $longKey = (Get-Text $long.n).ToLowerInvariant()
            if ($hidden.ContainsKey($longKey)) { continue }
            if ($longKey.Length -le $shortKey.Length) { continue }
            if (-not $longKey.Contains($shortKey)) { continue }
            $diff = $longKey.Replace($shortKey, "").Trim(" -_")
            if ($diff.Length -ge 25) { continue }
            if (Test-UpdateName $diff) { continue }
            $hidden[$shortKey] = $true
            if (($long.e -eq "" -or $long.e -eq "-") -and $short.e -ne "") { $long.e = $short.e }
            if ($long.s -eq 0 -and $short.s -gt 0) { $long.s = $short.s }
            if (($long.d -eq "" -or $long.d -eq "-") -and $short.d -ne "-") { $long.d = $short.d }
            break
        }
    }

    foreach ($record in $unique) {
        $key = (Get-Text $record.n).ToLowerInvariant()
        if ($hidden.ContainsKey($key)) { $record.cat = "doublon" }
    }

    return @($unique | Sort-Object { (Get-Text $_.n).ToLowerInvariant() })
}

function Get-SoftwareList {
    $records = @()
    $records += Get-RegistrySoftware -SourceLabel "Registre"
    $records += Get-RegistrySoftware -SourceLabel "Utilisateur"
    if (-not $SkipAppx) { $records += Get-AppxSoftware }
    if (-not $SkipFolderScan) { $records += Get-FolderSoftware }
    return Merge-Software -Records $records
}

function Get-InventoryPayload {
    Write-Host "Collecte des informations en cours..." -ForegroundColor Cyan
    Write-Host "  > Systeme..." -ForegroundColor Gray
    $system = Get-SystemInformation

    Write-Host "  > Disques..." -ForegroundColor Gray
    $disks = Get-Disks

    Write-Host "  > Reseau..." -ForegroundColor Gray
    $network = Get-NetworkInformation

    Write-Host "  > Services automatiques..." -ForegroundColor Gray
    $services = @()
    if (-not $SkipServices) { $services = Get-AutomaticServices }

    Write-Host "  > Logiciels..." -ForegroundColor Gray
    $software = Get-SoftwareList
    $counts = @{ main = 0; update = 0; composant = 0; doublon = 0 }
    foreach ($item in $software) {
        if ($item.cat -and $counts.ContainsKey($item.cat)) { $counts[$item.cat]++ }
    }
    Write-Host "    Principaux : $($counts.main) | Updates : $($counts.update) | Composants : $($counts.composant) | Doublons : $($counts.doublon)" -ForegroundColor Gray

    return [ordered]@{
        schema        = 3
        script_version = $script:ScriptVersion
        generated_at  = (Get-Date).ToString("o")
        pcName        = Get-Text $env:COMPUTERNAME
        date          = (Get-Date).ToString("dd/MM/yyyy HH:mm")
        system        = $system
        disks         = @($disks)
        network       = @($network)
        services      = @($services)
        software      = @($software)
        warnings      = @($script:Warnings)
    }
}

function New-SelfTestPayload {
    $software = @(
        (New-SoftwareRecord -Name "Autodesk Revit 2024" -Version "24.2.1" -Editor "Autodesk" -InstallDate "20240312" -SizeMb 8420.5 -Source "Registre"),
        (New-SoftwareRecord -Name "Guillemet `" & <tag>" -Version "1.0 <beta>" -Editor "Atelier & Cie" -InstallDate "2024-04-05" -SizeMb 12.25 -Source "Utilisateur"),
        (New-SoftwareRecord -Name "Fin </script> test" -Version "2.0" -Editor "Test" -InstallDate "" -SizeMb 0 -Source "Dossier"),
        (New-SoftwareRecord -Name "Microsoft Visual C++ 2015-2022 Redistributable" -Version "14.38.33130" -Editor "Microsoft" -InstallDate "20240101" -SizeMb 25.1 -Source "Registre")
    )
    $software = Merge-Software -Records $software

    return [ordered]@{
        schema         = 3
        script_version = $script:ScriptVersion
        generated_at   = (Get-Date).ToString("o")
        pcName         = "PC-TEST `"Atelier`" & <lab>"
        date           = "26/11/2026 21:00"
        system         = [ordered]@{
            os              = "Microsoft Windows 11 Pro"
            windows_version = "23H2 (build 22631.4169)"
            os_version      = "10.0.22631"
            os_build        = "22631"
            architecture    = "64 bits"
            cpu             = "Intel Core i7-12700 (12 coeurs / 20 threads) @ 2100 MHz"
            cpu_name        = "Intel Core i7-12700"
            cpu_cores       = 12
            cpu_threads     = 20
            cpu_mhz         = 2100
            ram             = 15.9
            fabricant       = "Dell Inc."
            modele          = "OptiPlex 7000"
            num_serie       = "SERIE-<&>-01"
            domaine         = "FABLAB"
            utilisateur     = "FABLAB\technicien"
        }
        disks          = @(
            [ordered]@{ label = "C:"; name = "C:"; volume = "Systeme"; filesystem = "NTFS"; size_go = 476.9; free_go = 120.4; used_go = 356.5 },
            [ordered]@{ label = "D:"; name = "D:"; volume = "Donnees"; filesystem = "NTFS"; size_go = 931.5; free_go = 410.2; used_go = 521.3 }
        )
        network        = @(
            [ordered]@{ name = "Intel(R) Ethernet Connection"; ip = "192.168.10.42"; mac = "AA:BB:CC:DD:EE:FF"; dhcp = "Non"; gateway = "192.168.10.1"; dns = "192.168.10.1, 1.1.1.1" }
        )
        services       = @(
            [ordered]@{ n = "FabInventory Agent"; sc = "FabInventoryAgent"; status = "Running"; start = "Auto" },
            [ordered]@{ n = "Spouleur d'impression"; sc = "Spooler"; status = "Running"; start = "Auto" }
        )
        software       = @($software)
        warnings       = @("Mode autotest : aucune donnee reelle collectee.")
    }
}

function Resolve-InventoryPaths {
    param([string]$pcName)

    $stamp = Get-Date -Format "yyyy-MM-dd_HH-mm"
    $defaultName = "$(Safe-FilePart $pcName)_$stamp.html"
    $requested = Get-Text $OutputPath

    $htmlPath = ""
    if ($requested) {
        if ($requested.EndsWith("\") -or $requested.EndsWith("/")) {
            if (-not (Test-Path -LiteralPath $requested)) {
                New-Item -ItemType Directory -Path $requested -Force | Out-Null
            }
            $requested = Join-Path $requested $defaultName
        }
        if (Test-Path -LiteralPath $requested -PathType Container) {
            $requested = Join-Path $requested $defaultName
        }
        if ([string]::IsNullOrWhiteSpace([IO.Path]::GetExtension($requested))) {
            $requested = "$requested.html"
        }
        $htmlPath = [IO.Path]::GetFullPath($requested)
    }
    else {
        $directory = Get-Text $OutputDirectory
        if (-not $directory) {
            if ($NoGui -or -not $script:RunningOnWindows) {
                $directory = Join-Path $PSScriptRoot "INVENTAIRES"
            }
            else {
                Add-Type -AssemblyName System.Windows.Forms
                Add-Type -AssemblyName System.Drawing
                $script:FormsReady = $true
                $dialog = New-Object System.Windows.Forms.SaveFileDialog
                $dialog.Title = "FabInventory - Enregistrer le rapport"
                $dialog.InitialDirectory = [Environment]::GetFolderPath("Desktop")
                if ([string]::IsNullOrWhiteSpace($dialog.InitialDirectory)) {
                    $dialog.InitialDirectory = $PSScriptRoot
                }
                $dialog.FileName = $defaultName
                $dialog.DefaultExt = "html"
                $dialog.Filter = "Rapport HTML (*.html)|*.html|Tous les fichiers (*.*)|*.*"
                $dialog.OverwritePrompt = $true
                if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
                    return $null
                }
                $htmlPath = [IO.Path]::GetFullPath($dialog.FileName)
            }
        }
        if (-not $htmlPath) {
            if (-not $directory) { $directory = $PSScriptRoot }
            $htmlPath = [IO.Path]::GetFullPath((Join-Path $directory $defaultName))
        }
    }

    $parent = [IO.Path]::GetDirectoryName($htmlPath)
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    return [ordered]@{
        html = $htmlPath
        csv  = [IO.Path]::ChangeExtension($htmlPath, ".csv")
    }
}

$htmlTemplate = @'
<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Inventaire __PC_NAME__</title>
<style>
:root{--pri:#173b57;--sec:#2e86c1;--acc:#1e8449;--bg:#f2f5f9;--card:#fff;--border:#dfe7ef;--txt:#23313f;--muted:#6b7c8f;--warn:#b45309;--danger:#c0392b;--appx:#7c3aed;--dir:#b45309}
*{box-sizing:border-box}body{margin:0;padding:24px;background:var(--bg);color:var(--txt);font-family:Segoe UI,Calibri,Arial,sans-serif;line-height:1.45}
header{background:linear-gradient(135deg,var(--pri),#245b7d);color:#fff;border-radius:14px;padding:22px 26px;display:flex;justify-content:space-between;align-items:center;gap:18px;box-shadow:0 8px 22px rgba(23,59,87,.18)}
header h1{margin:0;font-size:1.45rem}header p{margin:4px 0 0;opacity:.82;font-size:.92rem}
.total{text-align:right}.total strong{display:block;font-size:2rem;line-height:1}.total span{font-size:.8rem;opacity:.8}
.tabs{display:flex;gap:8px;margin:18px 0 14px}
.tab{border:0;background:#e3ebf3;color:var(--pri);padding:10px 18px;border-radius:10px;font-weight:700;cursor:pointer}.tab.active{background:var(--pri);color:#fff}
main{display:block}.panel{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px;margin-bottom:16px;box-shadow:0 2px 6px rgba(0,0,0,.04)}
.panel h2{margin:0 0 14px;font-size:1.02rem;color:var(--pri);padding-bottom:8px;border-bottom:1px solid #eef3f8}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--border);border-left:4px solid var(--sec);border-radius:11px;padding:13px 15px;box-shadow:0 2px 6px rgba(0,0,0,.04)}
.card .label{font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:700}.card .value{font-size:.98rem;font-weight:700;margin-top:3px;overflow-wrap:anywhere}
.info-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:7px 20px}.info{display:flex;gap:10px;border-bottom:1px dashed #edf1f6;padding:5px 0}.info b{min-width:120px;color:var(--pri);font-size:.86rem}.info span{overflow-wrap:anywhere}
table{width:100%;border-collapse:collapse;font-size:.88rem}th{background:var(--pri);color:#fff;text-align:left;padding:9px 10px;white-space:nowrap}td{padding:8px 10px;border-bottom:1px solid #edf1f6;vertical-align:top}tr:nth-child(even) td{background:#fafcfe}tr:hover td{background:#f0f7fd}tr.checked td{background:#e9f7ef!important}
.cb{width:34px;text-align:center}.cb input{width:16px;height:16px}.note{min-width:150px}.note input{width:100%;border:1px solid #d7e0e9;border-radius:6px;padding:5px 7px;font-size:.82rem;background:#fff}
.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px}.toolbar input[type=search]{flex:1;min-width:210px;padding:9px 12px;border:1px solid #cfdae5;border-radius:8px;font-size:.9rem}
button.action{border:0;border-radius:8px;padding:8px 13px;font-weight:700;cursor:pointer;font-size:.84rem}.primary{background:var(--pri);color:#fff}.success{background:var(--acc);color:#fff}.outline{background:#fff;border:1px solid var(--pri);color:var(--pri)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:9px;margin-bottom:12px}.stat{background:#f7fafc;border:1px solid #e6eef6;border-radius:8px;padding:9px 11px}.stat b{display:block;font-size:1.15rem;color:var(--pri)}.stat span{font-size:.74rem;color:var(--muted);text-transform:uppercase}
.badge{display:inline-block;padding:1px 7px;border-radius:9px;font-size:.7rem;font-weight:700;margin-left:5px}.b-main{background:#e8f1f9;color:#1b4f72}.b-update{background:#fdebd0;color:#b45309}.b-composant{background:#eef0f3;color:#5b6b7b}.b-doublon{background:#fadbd8;color:#a93226}
.src-reg{color:#1b4f72}.src-user{color:#1e8449}.src-appx{color:var(--appx)}.src-dir{color:var(--dir)}
.muted{color:var(--muted)}.warning{background:#fff8e6;border:1px solid #f3d38a;color:#7a4b00;border-radius:9px;padding:10px 13px;margin-bottom:14px;font-size:.86rem}
.drop{border:2px dashed #c8d6e3;border-radius:12px;padding:28px;text-align:center;color:var(--muted);background:#fbfdff;cursor:pointer}.drop.over{border-color:var(--sec);background:#eef8ff}.drop strong{color:var(--pri)}
.diff-added td{background:#e9f7ef!important}.diff-missing td{background:#fdedec!important}.diff-same td{background:#f7fbff!important}
.footer{text-align:center;color:var(--muted);font-size:.76rem;margin:20px 0 4px}
[hidden]{display:none!important}
@media print{body{padding:0;background:#fff}.tabs,.toolbar,.note,.cb,.no-print,.footer{display:none!important}.panel,.card,header{box-shadow:none}header{-webkit-print-color-adjust:exact;print-color-adjust:exact}th{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
@media (max-width:640px){body{padding:12px}header{flex-direction:column;align-items:flex-start}.total{text-align:left}}
</style>
</head>
<body>
<header>
  <div><h1 id="pcName">Inventaire</h1><p id="scanDate"></p></div>
  <div class="total"><strong id="swTotal">0</strong><span>logiciels</span></div>
</header>

<div class="tabs no-print">
  <button class="tab active" type="button" data-tab="inventory">Inventaire</button>
  <button class="tab" type="button" data-tab="compare">Comparaison</button>
</div>

<main id="tab-inventory">
  <div id="warnings"></div>
  <div class="cards" id="summaryCards"></div>

  <section class="panel">
    <h2>Informations systeme</h2>
    <div class="info-grid" id="systemGrid"></div>
  </section>

  <section class="panel">
    <h2>Disques</h2>
    <div style="overflow:auto"><table><thead><tr><th>Lecteur</th><th>Volume</th><th>Systeme</th><th>Taille</th><th>Libre</th><th>Utilise</th></tr></thead><tbody id="diskBody"></tbody></table></div>
  </section>

  <section class="panel">
    <h2>Reseau</h2>
    <div style="overflow:auto"><table><thead><tr><th>Interface</th><th>IP</th><th>MAC</th><th>DHCP</th><th>Passerelle</th><th>DNS</th></tr></thead><tbody id="networkBody"></tbody></table></div>
  </section>

  <section class="panel">
    <h2>Services automatiques <span class="muted" id="serviceCount"></span></h2>
    <div style="overflow:auto"><table><thead><tr><th>Service</th><th>Nom court</th><th>Etat</th><th>Demarrage</th></tr></thead><tbody id="serviceBody"></tbody></table></div>
  </section>

  <section class="panel">
    <h2>Logiciels <span class="muted" id="softwareCount"></span></h2>
    <div class="stats" id="softwareStats"></div>
    <div class="toolbar no-print" style="margin-bottom:8px">
      <span class="muted" style="font-size:.8rem">Afficher :</span>
      <button class="action primary" type="button" data-cat="main">Principaux</button>
      <button class="action outline" type="button" data-cat="update">Updates</button>
      <button class="action outline" type="button" data-cat="composant">Composants</button>
      <button class="action outline" type="button" data-cat="doublon">Doublons</button>
    </div>
    <div class="toolbar no-print">
      <input type="search" id="searchBox" placeholder="Rechercher un logiciel, un editeur, une version...">
      <button class="action outline" type="button" id="btnImportant">Importants uniquement</button>
      <button class="action success" type="button" id="btnUncheck">Tout decocher</button>
      <button class="action primary" type="button" onclick="window.print()">Imprimer / PDF</button>
    </div>
    <div style="overflow:auto">
      <table id="softwareTable">
        <thead><tr>
          <th class="cb no-print"></th>
          <th data-sort="1">Nom</th>
          <th data-sort="2">Version</th>
          <th data-sort="3">Editeur</th>
          <th data-sort="4">Date</th>
          <th data-sort="5">Taille (Mo)</th>
          <th data-sort="6">Source</th>
          <th class="note no-print">Note</th>
        </tr></thead>
        <tbody id="softwareBody"></tbody>
      </table>
    </div>
  </section>
</main>

<main id="tab-compare" hidden>
  <section class="panel">
    <h2>Comparer avec un autre inventaire</h2>
    <div class="drop" id="dropZone">
      <div style="font-size:2rem">&#128196; &#8644; &#128196;</div>
      <p><strong>Glissez</strong> un autre rapport HTML ici, ou cliquez pour choisir un fichier.</p>
      <input type="file" id="fileInput" accept=".html,text/html" hidden>
    </div>
    <div id="compareResult" hidden>
      <div class="stats" id="compareStats"></div>
      <div style="overflow:auto"><table><thead><tr><th>Logiciel</th><th>Version ici</th><th>Version autre</th><th>Statut</th></tr></thead><tbody id="compareBody"></tbody></table></div>
    </div>
  </section>
</main>

<div class="footer">FabInventory &middot; script __SCRIPT_VERSION__ &middot; __GENERATED_AT__</div>

<script id="inventoryData" type="application/json">__INVENTORY_JSON__</script>
<script>
(function () {
  "use strict";
  var DATA = JSON.parse(document.getElementById("inventoryData").textContent);
  var software = Array.isArray(DATA.software) ? DATA.software.slice() : [];
  var system = DATA.system || {};
  var disks = Array.isArray(DATA.disks) ? DATA.disks : [];
  var network = Array.isArray(DATA.network) ? DATA.network : [];
  var services = Array.isArray(DATA.services) ? DATA.services : [];
  var stateKey = "fabinventory:" + String(DATA.pcName || "poste");
  var state = loadState();
  var visibleCats = { main: true, update: false, composant: false, doublon: false };
  var importantOnly = false;
  var sortCol = 1;
  var sortAsc = true;

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function key(name) { return String(name || "").toLowerCase(); }
  function num(value) { var n = Number(value); return isFinite(n) ? n : 0; }
  function fmtSize(value) { var n = num(value); return n > 0 ? n.toFixed(1) : "-"; }
  function loadState() {
    try { var raw = localStorage.getItem(stateKey); return raw ? JSON.parse(raw) : {}; }
    catch (e) { return {}; }
  }
  function saveState() {
    try { localStorage.setItem(stateKey, JSON.stringify(state)); } catch (e) { }
  }
  function getState(name) { return state[key(name)] || {}; }
  function setState(name, patch) {
    var k = key(name);
    var current = state[k] || {};
    for (var p in patch) { if (Object.prototype.hasOwnProperty.call(patch, p)) current[p] = patch[p]; }
    state[k] = current;
    saveState();
  }
  function dateKey(value) {
    var m = String(value || "").match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
    return m ? m[3] + m[2] + m[1] : String(value || "");
  }
  function compare(a, b) {
    if (sortCol === 2) return String(a.v || "").localeCompare(String(b.v || ""), undefined, { numeric: true, sensitivity: "base" });
    if (sortCol === 4) return dateKey(a.d).localeCompare(dateKey(b.d));
    if (sortCol === 5) return num(a.s) - num(b.s);
    if (sortCol === 6) return String(a.src || "").localeCompare(String(b.src || ""), undefined, { sensitivity: "base" });
    return String(a.n || "").localeCompare(String(b.n || ""), undefined, { numeric: true, sensitivity: "base" });
  }
  function catBadge(cat) {
    var c = cat || "main";
    var label = c === "update" ? "update" : c === "composant" ? "composant" : c === "doublon" ? "doublon" : "";
    return label ? ' <span class="badge b-' + c + '">' + label + "</span>" : "";
  }
  function srcClass(src) {
    if (src === "Registre") return "src-reg";
    if (src === "Utilisateur") return "src-user";
    if (src === "AppX") return "src-appx";
    return "src-dir";
  }
  function emptyRow(cols, text) {
    return '<tr><td colspan="' + cols + '" class="muted">' + esc(text) + "</td></tr>";
  }

  function renderSummary() {
    document.getElementById("pcName").textContent = DATA.pcName || "Poste inconnu";
    document.getElementById("scanDate").textContent = "Inventaire du " + (DATA.date || "-");
    document.getElementById("swTotal").textContent = software.length;
    var cards = [
      ["Systeme", system.os || "-"],
      ["Processeur", system.cpu || "-"],
      ["Memoire", system.ram ? system.ram + " Go" : "-"],
      ["Disques", disks.length],
      ["Services auto", services.length]
    ];
    document.getElementById("summaryCards").innerHTML = cards.map(function (c) {
      return '<div class="card"><div class="label">' + esc(c[0]) + '</div><div class="value">' + esc(c[1]) + "</div></div>";
    }).join("");
  }
  function renderWarnings() {
    var list = Array.isArray(DATA.warnings) ? DATA.warnings : [];
    if (!list.length) return;
    document.getElementById("warnings").innerHTML = '<div class="warning"><strong>Notes de collecte</strong><ul style="margin:6px 0 0 18px">' +
      list.map(function (w) { return "<li>" + esc(w) + "</li>"; }).join("") + "</ul></div>";
  }
  function renderSystem() {
    var fields = [
      ["OS", system.os], ["Version Windows", system.windows_version],
      ["Version / build", [system.os_version, system.os_build].filter(Boolean).join(" / ")],
      ["Architecture", system.architecture], ["CPU", system.cpu],
      ["RAM", system.ram ? system.ram + " Go" : ""], ["Fabricant", system.fabricant],
      ["Modele", system.modele], ["N. serie", system.num_serie],
      ["Domaine", system.domaine], ["Utilisateur", system.utilisateur]
    ];
    document.getElementById("systemGrid").innerHTML = fields.filter(function (f) { return f[1]; }).map(function (f) {
      return '<div class="info"><b>' + esc(f[0]) + "</b><span>" + esc(f[1]) + "</span></div>";
    }).join("") || '<div class="muted">Aucune information systeme.</div>';
  }
  function renderDisks() {
    var body = document.getElementById("diskBody");
    if (!disks.length) { body.innerHTML = emptyRow(6, "Aucun disque detecte."); return; }
    body.innerHTML = disks.map(function (d) {
      return "<tr><td><strong>" + esc(d.label || d.name || "?") + "</strong></td><td>" + esc(d.volume || "-") +
        "</td><td>" + esc(d.filesystem || "-") + "</td><td>" + fmtSize(d.size_go) + " Go</td><td>" +
        fmtSize(d.free_go) + " Go</td><td>" + fmtSize(d.used_go) + " Go</td></tr>";
    }).join("");
  }
  function renderNetwork() {
    var body = document.getElementById("networkBody");
    if (!network.length) { body.innerHTML = emptyRow(6, "Aucune interface active detectee."); return; }
    body.innerHTML = network.map(function (n) {
      return "<tr><td><strong>" + esc(n.name || "?") + "</strong></td><td><code>" + esc(n.ip || "-") +
        "</code></td><td><code>" + esc(n.mac || "-") + "</code></td><td>" + esc(n.dhcp || "-") +
        "</td><td>" + esc(n.gateway || "-") + "</td><td><code>" + esc(n.dns || "-") + "</code></td></tr>";
    }).join("");
  }
  function renderServices() {
    document.getElementById("serviceCount").textContent = "(" + services.length + ")";
    var body = document.getElementById("serviceBody");
    if (!services.length) { body.innerHTML = emptyRow(4, "Aucun service automatique en cours."); return; }
    body.innerHTML = services.map(function (s) {
      return "<tr><td>" + esc(s.n || "?") + "</td><td><code>" + esc(s.sc || "-") +
        "</code></td><td>" + esc(s.status || "-") + "</td><td>" + esc(s.start || "-") + "</td></tr>";
    }).join("");
  }
  function renderStats() {
    var counts = { main: 0, update: 0, composant: 0, doublon: 0 };
    software.forEach(function (s) { var c = s.cat || "main"; if (counts[c] == null) counts[c] = 0; counts[c]++; });
    var labels = [["main", "Principaux"], ["update", "Updates"], ["composant", "Composants"], ["doublon", "Doublons"]];
    document.getElementById("softwareStats").innerHTML = labels.map(function (l) {
      return '<div class="stat"><b>' + counts[l[0]] + "</b><span>" + l[1] + "</span></div>";
    }).join("");
    document.getElementById("softwareCount").textContent = "(" + software.length + ")";
  }
  function renderSoftware() {
    var q = String(document.getElementById("searchBox").value || "").toLowerCase();
    var rows = software.filter(function (s) {
      var cat = s.cat || "main";
      if (!visibleCats[cat]) return false;
      if (importantOnly && !getState(s.n).c) return false;
      if (!q) return true;
      return [s.n, s.v, s.e, s.src, s.d].join(" ").toLowerCase().indexOf(q) !== -1;
    });
    rows.sort(function (a, b) { return sortAsc ? compare(a, b) : compare(b, a); });
    var body = document.getElementById("softwareBody");
    if (!rows.length) {
      body.innerHTML = emptyRow(8, "Aucun logiciel ne correspond au filtre.");
      return;
    }
    body.innerHTML = rows.map(function (s) {
      var st = getState(s.n);
      return '<tr class="' + (st.c ? "checked" : "") + '">' +
        '<td class="cb no-print"><input type="checkbox" data-name="' + esc(s.n) + '"' + (st.c ? " checked" : "") + "></td>" +
        "<td>" + esc(s.n || "-") + catBadge(s.cat) + "</td><td>" + esc(s.v || "-") + "</td><td>" + esc(s.e || "-") +
        "</td><td>" + esc(s.d || "-") + "</td><td>" + fmtSize(s.s) + '</td><td class="' + srcClass(s.src) + '">' +
        esc(s.src || "-") + '</td><td class="note no-print"><input type="text" data-name="' + esc(s.n) +
        '" value="' + esc(st.t || "") + '" placeholder="Note..."></td></tr>';
    }).join("");
  }
  function bindSoftware() {
    document.getElementById("softwareBody").addEventListener("change", function (event) {
      var target = event.target;
      var name = target.getAttribute("data-name");
      if (!name) return;
      if (target.type === "checkbox") setState(name, { c: target.checked });
      else if (target.type === "text") setState(name, { t: target.value });
      renderSoftware();
    });
    document.getElementById("searchBox").addEventListener("input", renderSoftware);
    document.getElementById("btnImportant").addEventListener("click", function () {
      importantOnly = !importantOnly;
      this.classList.toggle("primary", importantOnly);
      this.classList.toggle("outline", !importantOnly);
      renderSoftware();
    });
    document.getElementById("btnUncheck").addEventListener("click", function () {
      software.forEach(function (s) { setState(s.n, { c: false }); });
      importantOnly = false;
      document.getElementById("btnImportant").classList.remove("primary");
      document.getElementById("btnImportant").classList.add("outline");
      renderSoftware();
    });
    document.querySelectorAll("[data-sort]").forEach(function (th) {
      th.style.cursor = "pointer";
      th.addEventListener("click", function () {
        var col = Number(th.getAttribute("data-sort"));
        if (sortCol === col) sortAsc = !sortAsc; else { sortCol = col; sortAsc = true; }
        renderSoftware();
      });
    });
    document.querySelectorAll("[data-cat]").forEach(function (button) {
      button.addEventListener("click", function () {
        var cat = button.getAttribute("data-cat");
        visibleCats[cat] = !visibleCats[cat];
        button.classList.toggle("primary", visibleCats[cat]);
        button.classList.toggle("outline", !visibleCats[cat]);
        renderSoftware();
      });
    });
  }

  function parseInventoryText(text) {
    var doc = new DOMParser().parseFromString(text, "text/html");
    var node = doc.getElementById("inventoryData");
    if (!node) throw new Error("Bloc inventoryData introuvable");
    return JSON.parse(node.textContent);
  }
  function compareInventories(other) {
    if (!other || !Array.isArray(other.software)) throw new Error("Logiciels absents du fichier");
    var mine = {}, theirs = {};
    software.forEach(function (s) { mine[key(s.n)] = s; });
    other.software.forEach(function (s) { theirs[key(s.n)] = s; });
    var keys = Object.keys(mine).concat(Object.keys(theirs).filter(function (k) { return !mine[k]; }));
    var rows = keys.map(function (k) {
      var a = mine[k], b = theirs[k];
      if (a && b) return { n: a.n, a: a.v, b: b.v, status: "commun", cls: "diff-same" };
      if (a) return { n: a.n, a: a.v, b: "-", status: "uniquement ici", cls: "diff-added" };
      return { n: b.n, a: "-", b: b.v, status: "manquant ici", cls: "diff-missing" };
    });
    rows.sort(function (a, b) { return String(a.n).localeCompare(String(b.n), undefined, { sensitivity: "base" }); });
    var added = rows.filter(function (r) { return r.status === "uniquement ici"; }).length;
    var missing = rows.filter(function (r) { return r.status === "manquant ici"; }).length;
    var common = rows.length - added - missing;
    document.getElementById("compareStats").innerHTML =
      '<div class="stat"><b>' + added + "</b><span>Uniquement ici</span></div>" +
      '<div class="stat"><b>' + missing + "</b><span>Manquants ici</span></div>" +
      '<div class="stat"><b>' + common + "</b><span>En commun</span></div>" +
      '<div class="stat"><b style="font-size:.9rem">' + esc(other.pcName || "?") + "</b><span>Compare avec</span></div>";
    document.getElementById("compareBody").innerHTML = rows.map(function (r) {
      return '<tr class="' + r.cls + '"><td>' + esc(r.n) + "</td><td>" + esc(r.a) + "</td><td>" + esc(r.b) +
        "</td><td>" + esc(r.status) + "</td></tr>";
    }).join("");
    document.getElementById("compareResult").hidden = false;
    document.getElementById("dropZone").innerHTML = '<div style="font-size:2rem">&#9989;</div><p>Comparaison avec <strong>' +
      esc(other.pcName || "?") + "</strong> (" + esc(other.date || "-") + ")</p><p class=\"muted\">Choisissez un autre fichier pour recommencer.</p>";
  }
  function bindComparison() {
    var zone = document.getElementById("dropZone");
    var input = document.getElementById("fileInput");
    function read(file) {
      if (!file) return;
      if (!/\.html?$/i.test(file.name)) { alert("Choisissez un rapport HTML."); return; }
      var reader = new FileReader();
      reader.onload = function (e) {
        try { compareInventories(parseInventoryText(e.target.result)); }
        catch (err) { alert("Lecture impossible : " + err.message); }
      };
      reader.readAsText(file, "utf-8");
    }
    zone.addEventListener("click", function () { input.click(); });
    zone.addEventListener("dragover", function (e) { e.preventDefault(); zone.classList.add("over"); });
    zone.addEventListener("dragleave", function () { zone.classList.remove("over"); });
    zone.addEventListener("drop", function (e) {
      e.preventDefault(); zone.classList.remove("over");
      if (e.dataTransfer.files.length) read(e.dataTransfer.files[0]);
    });
    input.addEventListener("change", function () { read(input.files[0]); });
  }
  function bindTabs() {
    document.querySelectorAll("[data-tab]").forEach(function (tab) {
      tab.addEventListener("click", function () {
        var name = tab.getAttribute("data-tab");
        document.querySelectorAll("[data-tab]").forEach(function (t) { t.classList.toggle("active", t === tab); });
        document.getElementById("tab-inventory").hidden = name !== "inventory";
        document.getElementById("tab-compare").hidden = name !== "compare";
      });
    });
  }

  renderSummary();
  renderWarnings();
  renderSystem();
  renderDisks();
  renderNetwork();
  renderServices();
  renderStats();
  renderSoftware();
  bindSoftware();
  bindComparison();
  bindTabs();
})();
</script>
</body>
</html>
'@

function Write-InventoryFiles {
    param($Payload, [string]$HtmlPath, [string]$CsvPath)

    $json = ConvertTo-Json -InputObject $Payload -Depth 12 -Compress
    if ([string]::IsNullOrWhiteSpace($json)) {
        throw "La serialisation JSON a produit un resultat vide."
    }
    # Empeche toute fermeture prematuree du bloc <script> et les commentaires HTML.
    $jsonSafe = $json.Replace("<", "\u003c")

    $html = $htmlTemplate.Replace("__PC_NAME__", (Escape-Html $Payload.pcName))
    $html = $html.Replace("__SCRIPT_VERSION__", (Escape-Html $script:ScriptVersion))
    $html = $html.Replace("__GENERATED_AT__", (Escape-Html $Payload.date))
    $html = $html.Replace("__INVENTORY_JSON__", $jsonSafe)

    # Verifie le bloc avant d'ecrire : un echec ne laisse pas de fichiers partiels.
    $match = [regex]::Match($html, '<script id="inventoryData" type="application/json">(.*?)</script>', [System.Text.RegularExpressions.RegexOptions]::Singleline)
    if (-not $match.Success) {
        throw "Le bloc inventoryData est absent du HTML genere."
    }
    $parsed = $match.Groups[1].Value | ConvertFrom-Json
    if ($parsed.schema -ne 3) {
        throw "Le bloc inventoryData n'a pas le schema 3."
    }
    if ($parsed.pcName -ne $Payload.pcName) {
        throw "Le bloc inventoryData ne restitue pas le nom du poste."
    }
    if (@($parsed.software).Count -ne @($Payload.software).Count) {
        throw "Le bloc inventoryData ne restitue pas tous les logiciels."
    }

    $csvRows = @($Payload.software | ForEach-Object {
        [PSCustomObject]@{
            pc_name      = $Payload.pcName
            scan_date    = $Payload.date
            name         = $_.n
            version      = $_.v
            editor       = $_.e
            install_date = $_.d
            size_mb      = $_.s
            source       = $_.src
            category     = $_.cat
        }
    })
    if ($csvRows.Count -eq 0) {
        $csvLines = @("pc_name;scan_date;name;version;editor;install_date;size_mb;source;category")
    }
    elseif ($PSVersionTable.PSVersion.Major -lt 6) {
        $csvLines = @($csvRows | ConvertTo-Csv -NoTypeInformation -Delimiter ";")
    }
    else {
        # PowerShell 7 signale -NoTypeInformation comme obsolete ; son defaut
        # est deja sans ligne de type. On garde donc une branche explicite.
        $csvLines = @($csvRows | ConvertTo-Csv -Delimiter ";")
    }

    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $utf8Csv = New-Object System.Text.UTF8Encoding($true)
    [IO.File]::WriteAllText($HtmlPath, $html, $utf8)
    [IO.File]::WriteAllLines($CsvPath, $csvLines, $utf8Csv)

    return [ordered]@{
        json_bytes = [Text.Encoding]::UTF8.GetByteCount($json)
        html_path  = $HtmlPath
        csv_path   = $CsvPath
    }
}

function Show-Info {
    param([string]$Message, [string]$Title = "FabInventory")
    if ($script:FormsReady) {
        [System.Windows.Forms.MessageBox]::Show($Message, $Title, [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
    }
    Write-Host $Message
}

function Show-Error {
    param([string]$Message, [string]$Title = "FabInventory - Erreur")
    if ($script:FormsReady) {
        [System.Windows.Forms.MessageBox]::Show($Message, $Title, [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
    }
    Write-Host $Message -ForegroundColor Red
}

try {
    if ($SelfTest) {
        $NoGui = $true
        $payload = New-SelfTestPayload
        Write-Host "Mode autotest : donnees synthetiques." -ForegroundColor Cyan
    }
    else {
        $payload = Get-InventoryPayload
    }

    if ([string]::IsNullOrWhiteSpace((Get-Text $payload.pcName))) {
        $payload.pcName = "Poste inconnu"
    }
    if ([string]::IsNullOrWhiteSpace((Get-Text $payload.date))) {
        $payload.date = (Get-Date).ToString("dd/MM/yyyy HH:mm")
    }

    $paths = Resolve-InventoryPaths -pcName $payload.pcName
    if ($null -eq $paths) {
        Write-Host "Operation annulee : aucun fichier n'a ete enregistre." -ForegroundColor Yellow
        exit 0
    }

    Write-Host "  > Generation du rapport..." -ForegroundColor Gray
    $result = Write-InventoryFiles -Payload $payload -HtmlPath $paths.html -CsvPath $paths.csv

    Write-Host ""
    Write-Host "  TERMINE" -ForegroundColor Green
    Write-Host "  Rapport : $($result.html_path)" -ForegroundColor Green
    Write-Host "  CSV     : $($result.csv_path)" -ForegroundColor Green
    Write-Host "  Logiciels : $($payload.software.Count)" -ForegroundColor Green
    if ($payload.warnings.Count -gt 0) {
        Write-Host "  Avertissements : $($payload.warnings.Count) (visibles dans le rapport)" -ForegroundColor Yellow
    }
    if (-not $SelfTest -and -not $NoGui -and $script:FormsReady) {
        Show-Info "Inventaire enregistre :`n$($result.html_path)`n$($result.csv_path)`n`nLogiciels : $($payload.software.Count)"
    }
    exit 0
}
catch {
    $message = $_.Exception.Message
    $logPath = Join-Path ([IO.Path]::GetTempPath()) ("FabInventory-inventaire-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
    try {
        $details = @(
            "Date : $(Get-Date -Format o)",
            "Script : $script:ScriptVersion",
            "Poste : $env:COMPUTERNAME",
            "Erreur : $message",
            "",
            ($_ | Out-String),
            ($_.ScriptStackTrace | Out-String)
        )
        [IO.File]::WriteAllText($logPath, ($details -join "`r`n"), (New-Object System.Text.UTF8Encoding($false)))
    }
    catch { }
    Show-Error "Echec de l'inventaire :`n$message`n`nJournal : $logPath"
    exit 1
}
