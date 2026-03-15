param(
    [string]$RepoOwner = "fablabloritz-coder",
    [string]$RepoName = "Fablab-Suite",
    [string]$Branch = "main",
    [switch]$Update
)

$ErrorActionPreference = "Stop"

function Write-Info {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor Cyan
}

function Write-WarnMsg {
    param([string]$Message)
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Invoke-Download {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$OutFile
    )

    $dir = Split-Path -Parent $OutFile
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }

    $invokeParams = @{
        Uri     = $Url
        OutFile = $OutFile
    }

    if ($PSVersionTable.PSVersion.Major -lt 6) {
        $invokeParams["UseBasicParsing"] = $true
    }

    Invoke-WebRequest @invokeParams
}

function Resolve-PythonCommand {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @("py", "-3")
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @("python")
    }
    throw "Python introuvable. Installe Python 3 puis relance."
}

# Compat TLS pour certains environnements Windows PowerShell 5.1.
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
} catch {
}

$cacheRoot = Join-Path $env:LOCALAPPDATA "FabSuite\ssh-gui"
$manifestPath = Join-Path $cacheRoot "cache-manifest.json"
$guiPath = Join-Path $cacheRoot "fabsuite_ssh_gui.py"
$rawBase = "https://raw.githubusercontent.com/$RepoOwner/$RepoName/$Branch"

$requiredFiles = @(
    "fabsuite_ssh_gui.py",
    "fabsuite-ubuntu.sh",
    "fabsuite-ubuntu.env.example",
    "INSTALL_UBUNTU.md"
)

$optionalFiles = @(
    "deploy_core/__init__.py",
    "deploy_core/models.py",
    "deploy_core/service.py",
    "deploy_core/workflows.py",
    "deploy_core/adapters/__init__.py",
    "deploy_core/adapters/base.py",
    "deploy_core/adapters/local.py",
    "deploy_core/adapters/ssh.py"
)

if (-not (Test-Path $cacheRoot)) {
    New-Item -ItemType Directory -Force -Path $cacheRoot | Out-Null
}

$needRefresh = $false
if ($Update) {
    $needRefresh = $true
}

if (-not (Test-Path $guiPath)) {
    $needRefresh = $true
}

if (-not $needRefresh -and (Test-Path $manifestPath)) {
    try {
        $manifest = Get-Content -Raw -Path $manifestPath | ConvertFrom-Json
        $expectedCount = $requiredFiles.Count + $optionalFiles.Count
        if (($manifest.raw_base -ne $rawBase) -or ($manifest.file_count -ne $expectedCount)) {
            $needRefresh = $true
        }
    }
    catch {
        $needRefresh = $true
    }
}

if (-not (Test-Path $manifestPath)) {
    $needRefresh = $true
}

if ($needRefresh) {
    Write-Info "Mise à jour du cache GUI depuis GitHub ($rawBase)..."
    foreach ($rel in $requiredFiles) {
        $url = "$rawBase/$rel"
        $out = Join-Path $cacheRoot $rel
        Write-Info "Téléchargement: $rel"
        Invoke-Download -Url $url -OutFile $out
    }

    $optionalMissing = @()
    foreach ($rel in $optionalFiles) {
        $url = "$rawBase/$rel"
        $out = Join-Path $cacheRoot $rel
        try {
            Write-Info "Téléchargement: $rel"
            Invoke-Download -Url $url -OutFile $out
        }
        catch {
            $optionalMissing += $rel
            Write-WarnMsg "Fichier optionnel indisponible: $rel"
        }
    }

    $guiText = ""
    try {
        $guiText = Get-Content -Raw -Path $guiPath
    }
    catch {
        $guiText = ""
    }

    $requiresCore = ($guiText -match "(?m)^from deploy_core import") -or ($guiText -match "(?m)^import deploy_core")
    if ($requiresCore -and $optionalMissing.Count -gt 0) {
        throw "Le GUI téléchargé nécessite deploy_core, mais certains fichiers sont manquants sur GitHub: $($optionalMissing -join ', ')"
    }

    $manifestObj = @{
        raw_base   = $rawBase
        file_count = ($requiredFiles.Count + $optionalFiles.Count)
        updated_at = (Get-Date).ToString("o")
    }
    $manifestObj | ConvertTo-Json | Set-Content -Path $manifestPath -Encoding UTF8
    Write-Info "Cache prêt: $cacheRoot"
}
else {
    Write-Info "Cache déjà prêt: $cacheRoot"
    Write-WarnMsg "Ajoute -Update pour forcer la récupération de la dernière version."
}

$pythonCmd = Resolve-PythonCommand

Write-Info "Lancement de fabsuite_ssh_gui.py"
if ($pythonCmd.Count -eq 2) {
    & $pythonCmd[0] $pythonCmd[1] $guiPath
}
else {
    & $pythonCmd[0] $guiPath
}
