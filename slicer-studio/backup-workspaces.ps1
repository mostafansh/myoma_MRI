[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$DeployDir,

    [Parameter(Mandatory = $true)]
    [string]$BackupRoot,

    [ValidateRange(1, 1000)]
    [int]$Keep = 7
)

$ErrorActionPreference = 'Stop'

$usersDir = Join-Path $DeployDir 'users'
if (-not (Test-Path -LiteralPath $usersDir -PathType Container)) {
    Write-Error "Workspaces folder does not exist: $usersDir"
    exit 1
}

$stamp = [DateTime]::Now.ToString('yyyyMMdd-HHmmss-fff')
$final = Join-Path $BackupRoot "workspaces-$stamp"
# Build under a name the prune pattern does not match; rename only when complete.
$staging = "$final.partial"
if ((Test-Path -LiteralPath $final) -or (Test-Path -LiteralPath $staging)) {
    Write-Error "Backup folder already exists: $final"
    exit 1
}

try {
    New-Item -ItemType Directory -Path (Join-Path $staging 'users') -Force | Out-Null

    # robocopy exit codes below 8 mean success (files copied / extras / mismatches).
    & robocopy.exe $usersDir (Join-Path $staging 'users') /E /R:2 /W:2 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed with exit code $LASTEXITCODE"
    }

    # Only portal/users.json is copied; secret.key stays off the backup drive on purpose.
    $usersFile = Join-Path $DeployDir 'portal/users.json'
    if (Test-Path -LiteralPath $usersFile -PathType Leaf) {
        Copy-Item -LiteralPath $usersFile -Destination (Join-Path $staging 'users.json')
    } else {
        Write-Warning "users.json not found under $DeployDir/portal; backed up workspaces only."
    }

    Rename-Item -LiteralPath $staging -NewName (Split-Path $final -Leaf)
} catch {
    if (Test-Path -LiteralPath $staging) {
        Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Error "Backup failed: $_"
    exit 1
}

# Prune only our own snapshot folders; leave anything else in BackupRoot alone.
$snapshots = @(
    Get-ChildItem -LiteralPath $BackupRoot -Directory |
        Where-Object { $_.Name -cmatch '^workspaces-\d{8}-\d{6}-\d{3}$' } |
        Sort-Object Name -Descending
)
if ($snapshots.Count -gt $Keep) {
    $snapshots | Select-Object -Skip $Keep | ForEach-Object {
        Remove-Item -LiteralPath $_.FullName -Recurse -Force
    }
}

Write-Output "Backup written to $final"
exit 0
