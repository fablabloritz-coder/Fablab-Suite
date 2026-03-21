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

function Test-FabSuiteWorkspace {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $false
    }

    if (-not (Test-Path -Path $Path -PathType Container)) {
        return $false
    }

    if (-not (Test-Path -Path (Join-Path $Path "docker-compose.yml") -PathType Leaf)) {
        return $false
    }

    foreach ($d in @("FabHome", "Fabtrack", "PretGo", "FabBoard", "FabInventory")) {
        if (-not (Test-Path -Path (Join-Path $Path $d) -PathType Container)) {
            return $false
        }
    }

    return $true
}

function Ensure-LocalWorkspace {
    param(
        [Parameter(Mandatory = $true)][string]$AppRoot,
        [Parameter(Mandatory = $true)][string]$RepoOwner,
        [Parameter(Mandatory = $true)][string]$RepoName,
        [Parameter(Mandatory = $true)][string]$Branch,
        [Parameter(Mandatory = $false)][bool]$ForceRefresh = $false
    )

    $cwdPath = (Get-Location).Path
    if (Test-FabSuiteWorkspace -Path $cwdPath) {
        Write-Info "Workspace local detecte depuis le dossier courant: $cwdPath"
        return $cwdPath
    }

    $workspaceRoot = Join-Path $AppRoot "workspace"
    $hasGit = $null -ne (Get-Command git -ErrorAction SilentlyContinue)

    if (Test-FabSuiteWorkspace -Path $workspaceRoot) {
        if ($ForceRefresh -and $hasGit -and (Test-Path -Path (Join-Path $workspaceRoot ".git") -PathType Container)) {
            Write-Info "Mise a jour du workspace local: $workspaceRoot"
            & git -C $workspaceRoot fetch origin $Branch
            if ($LASTEXITCODE -eq 0) {
                & git -C $workspaceRoot reset --hard ("origin/" + $Branch)
            }
            if ($LASTEXITCODE -ne 0) {
                Write-WarnMsg "Impossible de mettre a jour le workspace local (git). Utilisation de la version existante."
            }
        }
        return $workspaceRoot
    }

    if (-not $hasGit) {
        Write-WarnMsg "Git introuvable: mode local indisponible sans workspace monorepo preexistant."
        return ""
    }

    $repoUrl = "https://github.com/$RepoOwner/$RepoName.git"
    Write-Info "Initialisation du workspace local pour le mode local: $workspaceRoot"

    if (Test-Path -Path $workspaceRoot -PathType Container) {
        $children = @(Get-ChildItem -Force -Path $workspaceRoot -ErrorAction SilentlyContinue)
        if ($children.Count -gt 0) {
            Write-WarnMsg "Workspace local existant non reconnu: $workspaceRoot"
            Write-WarnMsg "Conserve ce dossier et configure FABSUITE_LOCAL_WORKSPACE manuellement si besoin."
            return ""
        }
        Remove-Item -Force -Path $workspaceRoot
    }

    $workspaceParent = Split-Path -Parent $workspaceRoot
    if (-not (Test-Path $workspaceParent)) {
        New-Item -ItemType Directory -Force -Path $workspaceParent | Out-Null
    }

    & git clone --branch $Branch $repoUrl $workspaceRoot
    if ($LASTEXITCODE -ne 0) {
        Write-WarnMsg "Clone du workspace local echoue. Le mode local sera indisponible."
        return ""
    }

    if (Test-FabSuiteWorkspace -Path $workspaceRoot) {
        return $workspaceRoot
    }

    Write-WarnMsg "Workspace clone mais structure locale non valide pour docker compose racine."
    return ""
}

# Compat TLS pour certains environnements Windows PowerShell 5.1.
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
} catch {
}

$cacheRoot = Join-Path $env:LOCALAPPDATA "FabSuite\ssh-gui"
$appRoot = Join-Path $env:LOCALAPPDATA "FabSuite"
$manifestPath = Join-Path $cacheRoot "cache-manifest.json"
$guiPath = Join-Path $cacheRoot "fabsuite_ssh_gui.py"
$rawBase = "https://raw.githubusercontent.com/$RepoOwner/$RepoName/$Branch"
$cacheSchemaVersion = 2

$requiredFiles = @(
    "fabsuite_ssh_gui.py",
    "fabsuite-ubuntu.sh",
    "fabsuite-ubuntu.env.example",
    "INSTALL_UBUNTU.md",
    "web/index.html",
    "web/css/app.css",
    "web/js/app.js",
    "web/vendor/bootstrap.min.css",
    "web/vendor/bootstrap.bundle.min.js"
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
        $manifestSchema = 0
        if ($manifest.PSObject.Properties.Name -contains "schema_version") {
            $manifestSchema = [int]$manifest.schema_version
        }
        if (($manifest.raw_base -ne $rawBase) -or ($manifest.file_count -ne $expectedCount) -or ($manifestSchema -ne $cacheSchemaVersion)) {
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
    Write-Info "Mise a jour du cache GUI depuis GitHub ($rawBase)..."
    foreach ($rel in $requiredFiles) {
        $url = "$rawBase/$rel"
        $out = Join-Path $cacheRoot $rel
        Write-Info "Telechargement: $rel"
        Invoke-Download -Url $url -OutFile $out
    }

    $optionalMissing = @()
    foreach ($rel in $optionalFiles) {
        $url = "$rawBase/$rel"
        $out = Join-Path $cacheRoot $rel
        try {
            Write-Info "Telechargement: $rel"
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
        throw "Le GUI telecharge necessite deploy_core, mais certains fichiers sont manquants sur GitHub: $($optionalMissing -join ', ')"
    }

    $manifestObj = @{
        schema_version = $cacheSchemaVersion
        raw_base   = $rawBase
        file_count = ($requiredFiles.Count + $optionalFiles.Count)
        updated_at = (Get-Date).ToString("o")
    }
    $manifestObj | ConvertTo-Json | Set-Content -Path $manifestPath -Encoding UTF8
    Write-Info "Cache pret: $cacheRoot"
}
else {
    Write-Info "Cache deja pret: $cacheRoot"
    Write-WarnMsg "Ajoute -Update pour forcer la recuperation de la derniere version."
}

$localWorkspace = Ensure-LocalWorkspace -AppRoot $appRoot -RepoOwner $RepoOwner -RepoName $RepoName -Branch $Branch -ForceRefresh:$Update.IsPresent
if (-not [string]::IsNullOrWhiteSpace($localWorkspace)) {
    $env:FABSUITE_LOCAL_WORKSPACE = $localWorkspace
    Write-Info "FABSUITE_LOCAL_WORKSPACE=$localWorkspace"
}
else {
    Write-WarnMsg "Aucun workspace local valide detecte. Le mode local GUI restera indisponible."
}

$pythonCmd = Resolve-PythonCommand

$scriptRoot = $null
if ($MyInvocation -and $MyInvocation.MyCommand -and $MyInvocation.MyCommand.Path) {
    $scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}
elseif (-not [string]::IsNullOrWhiteSpace($PSScriptRoot)) {
    $scriptRoot = $PSScriptRoot
}
elseif (-not [string]::IsNullOrWhiteSpace($localWorkspace)) {
    $scriptRoot = $localWorkspace
}
else {
    $scriptRoot = $cacheRoot
}

$localGuiPath = Join-Path $scriptRoot "fabsuite_ssh_gui.py"
$localHelperPath = Join-Path $scriptRoot "fabsuite-ubuntu.sh"
$localWebPath = Join-Path $scriptRoot "web\index.html"

$guiToRun = $guiPath
if ((Test-Path $localGuiPath -PathType Leaf) -and (Test-Path $localHelperPath -PathType Leaf) -and (Test-Path $localWebPath -PathType Leaf)) {
    $guiToRun = $localGuiPath
    Write-Info "GUI locale detectee: $guiToRun"
}
else {
    Write-Info "GUI en cache utilisee: $guiToRun"
}

Write-Info "Lancement de fabsuite_ssh_gui.py"
if ($pythonCmd.Count -eq 2) {
    & $pythonCmd[0] $pythonCmd[1] $guiToRun
}
else {
    & $pythonCmd[0] $guiToRun
}

# Fermeture automatique du terminal quand le GUI se ferme
exit $LASTEXITCODE
