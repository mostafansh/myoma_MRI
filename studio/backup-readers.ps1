[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ReadersRoot,

    [Parameter(Mandatory = $true)]
    [string]$PortalDataDir,

    [Parameter(Mandatory = $true)]
    [string]$BackupRoot,

    [ValidateRange(1, 1000)]
    [int]$Keep = 7
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $ReadersRoot -PathType Container)) {
    Write-Error "ReadersRoot does not exist: $ReadersRoot"
    exit 1
}

$stamp = [DateTime]::Now.ToString('yyyyMMdd-HHmmss-fff')
$target = Join-Path $BackupRoot "readers-$stamp"
if (Test-Path -LiteralPath $target) {
    Write-Error "Backup folder already exists: $target"
    exit 1
}
New-Item -ItemType Directory -Path (Join-Path $target 'readers') -Force | Out-Null

# robocopy exit codes below 8 mean success (files copied / extras / mismatches).
& robocopy.exe $ReadersRoot (Join-Path $target 'readers') /E /R:2 /W:2 /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) {
    Write-Error "robocopy failed with exit code $LASTEXITCODE"
    exit 1
}

# Only readers.json is copied; secret.key stays off the backup drive on purpose.
$readersFile = Join-Path $PortalDataDir 'readers.json'
if (Test-Path -LiteralPath $readersFile -PathType Leaf) {
    Copy-Item -LiteralPath $readersFile -Destination (Join-Path $target 'readers.json')
} else {
    Write-Warning "readers.json not found in $PortalDataDir; backed up readers only."
}

# Prune only our own snapshot folders; leave anything else in BackupRoot alone.
$snapshots = @(
    Get-ChildItem -LiteralPath $BackupRoot -Directory |
        Where-Object { $_.Name -cmatch '^readers-\d{8}-\d{6}-\d{3}$' } |
        Sort-Object Name -Descending
)
if ($snapshots.Count -gt $Keep) {
    $snapshots | Select-Object -Skip $Keep | ForEach-Object {
        Remove-Item -LiteralPath $_.FullName -Recurse -Force
    }
}

Write-Output "Backup written to $target"
exit 0
