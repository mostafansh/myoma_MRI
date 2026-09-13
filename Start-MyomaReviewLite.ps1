[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ReaderDir,

    [string]$SlicerPath,

    [switch]$Wait,

    [switch]$ResolveOnly
)

$ErrorActionPreference = 'Stop'

function Resolve-PackageValue {
    param(
        [Parameter(Mandatory = $true)][string]$Value,
        [Parameter(Mandatory = $true)][string]$BasePath
    )

    $expanded = [Environment]::ExpandEnvironmentVariables($Value.Trim())
    if ([System.IO.Path]::IsPathRooted($expanded)) {
        return [System.IO.Path]::GetFullPath($expanded)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $BasePath $expanded))
}

function Resolve-ShortcutTarget {
    param([Parameter(Mandatory = $true)][string]$ShortcutPath)

    $resolvedShortcut = (Resolve-Path -LiteralPath $ShortcutPath).Path
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($resolvedShortcut)
    if ([string]::IsNullOrWhiteSpace($shortcut.TargetPath)) {
        throw "Shortcut has no target: $resolvedShortcut"
    }
    return [System.IO.Path]::GetFullPath($shortcut.TargetPath)
}

function Resolve-SlicerCandidate {
    param([Parameter(Mandatory = $true)][string]$Candidate)

    if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
        throw "Slicer path does not exist: $Candidate"
    }
    $resolved = (Resolve-Path -LiteralPath $Candidate).Path
    if ([System.IO.Path]::GetExtension($resolved) -ieq '.lnk') {
        $resolved = Resolve-ShortcutTarget -ShortcutPath $resolved
    }
    if (-not [System.IO.File]::Exists($resolved)) {
        throw "Slicer executable does not exist: $resolved"
    }
    return [System.IO.Path]::GetFullPath($resolved)
}

function Resolve-AutomaticSlicer {
    $directCandidates = @()
    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        $slicerBase = Join-Path $env:LOCALAPPDATA 'slicer.org'
        try {
            if (Test-Path -LiteralPath $slicerBase -PathType Container) {
                $directCandidates = Get-ChildItem -LiteralPath $slicerBase -Directory -Filter '3D Slicer 5.10*' |
                    Sort-Object Name -Descending |
                    ForEach-Object { Join-Path $_.FullName 'Slicer.exe' }
            }
        }
        catch {
            $directCandidates = @()
        }
    }
    foreach ($candidate in $directCandidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return Resolve-SlicerCandidate -Candidate $candidate
        }
    }

    $searchRoots = @()
    if (-not [string]::IsNullOrWhiteSpace($env:APPDATA)) {
        $searchRoots += Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
    }
    if (-not [string]::IsNullOrWhiteSpace($env:ProgramData)) {
        $searchRoots += Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs'
    }
    if (-not [string]::IsNullOrWhiteSpace($env:PUBLIC)) {
        $searchRoots += Join-Path $env:PUBLIC 'Desktop'
    }
    foreach ($root in $searchRoots | Where-Object { Test-Path -LiteralPath $_ -PathType Container }) {
        $shortcuts = Get-ChildItem -LiteralPath $root -Recurse -File -Filter '*Slicer*5.10*.lnk' |
            Sort-Object FullName
        foreach ($shortcut in $shortcuts) {
            try {
                $target = Resolve-ShortcutTarget -ShortcutPath $shortcut.FullName
                if ([System.IO.File]::Exists($target)) {
                    return $target
                }
                $shortcutProfile = [regex]::Match(
                    $shortcut.FullName,
                    '^(?<profile>[A-Za-z]:\\Users\\[^\\]+)\\AppData\\Roaming\\'
                )
                $localMarker = '\AppData\Local\'
                $localIndex = $target.IndexOf(
                    $localMarker,
                    [System.StringComparison]::OrdinalIgnoreCase
                )
                if ($shortcutProfile.Success -and $localIndex -ge 0) {
                    $relativeLocalPath = $target.Substring(
                        $localIndex + $localMarker.Length
                    )
                    $profileTarget = Join-Path $shortcutProfile.Groups['profile'].Value (
                        Join-Path 'AppData\Local' $relativeLocalPath
                    )
                    return [System.IO.Path]::GetFullPath($profileTarget)
                }
            }
            catch {
                continue
            }
        }
    }
    throw 'Could not find 3D Slicer 5.10. Set slicer_path in review-config.json or pass -SlicerPath.'
}

function Resolve-SlicerExecutable {
    param(
        [string]$CommandLinePath,
        [string]$ConfiguredPath
    )

    if (-not [string]::IsNullOrWhiteSpace($CommandLinePath)) {
        $candidate = Resolve-PackageValue -Value $CommandLinePath -BasePath (Get-Location).Path
        return Resolve-SlicerCandidate -Candidate $candidate
    }
    if (-not [string]::IsNullOrWhiteSpace($ConfiguredPath)) {
        return Resolve-SlicerCandidate -Candidate $ConfiguredPath
    }
    return Resolve-AutomaticSlicer
}

function Read-ReviewConfiguration {
    param([Parameter(Mandatory = $true)][string]$PackageRoot)

    $configPath = Join-Path $PackageRoot 'review-config.json'
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        throw "Configuration file was not found: $configPath"
    }
    try {
        $configuration = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json
    }
    catch {
        throw "Configuration is malformed: $configPath"
    }
    if ($null -eq $configuration -or $configuration.schema_version -ne '1.0') {
        throw 'Configuration schema_version must be 1.0.'
    }
    if (
        $configuration.PSObject.Properties.Name -notcontains 'dataset_root' -or
        $configuration.dataset_root -isnot [string] -or
        [string]::IsNullOrWhiteSpace($configuration.dataset_root)
    ) {
        throw 'Configuration dataset_root must be a non-empty string.'
    }
    if (
        $configuration.PSObject.Properties.Name -notcontains 'slicer_path' -or
        $configuration.slicer_path -isnot [string]
    ) {
        throw 'Configuration slicer_path must be a string; use an empty string for auto-detection.'
    }
    $datasetRoot = Resolve-PackageValue -Value $configuration.dataset_root -BasePath $PackageRoot
    if (-not (Test-Path -LiteralPath $datasetRoot -PathType Container)) {
        throw "Configured dataset root does not exist: $datasetRoot"
    }
    $configuredSlicer = ''
    if (-not [string]::IsNullOrWhiteSpace($configuration.slicer_path)) {
        $configuredSlicer = Resolve-PackageValue -Value $configuration.slicer_path -BasePath $PackageRoot
    }
    return [PSCustomObject]@{
        DatasetRoot = [System.IO.Path]::GetFullPath($datasetRoot)
        SlicerPath = $configuredSlicer
    }
}

try {
    $resolvedReader = (Resolve-Path -LiteralPath $ReaderDir).Path
    $queuePath = Join-Path $resolvedReader 'queue.json'
    if (-not (Test-Path -LiteralPath $queuePath -PathType Leaf)) {
        throw "Reader folder does not contain queue.json: $resolvedReader"
    }
    $moduleRoot = Join-Path $PSScriptRoot 'MyomaReviewLite'
    if (-not (Test-Path -LiteralPath (Join-Path $moduleRoot 'MyomaReviewLite.py') -PathType Leaf)) {
        throw "Bundled MyomaReviewLite module was not found: $moduleRoot"
    }
    $configuration = Read-ReviewConfiguration -PackageRoot $PSScriptRoot
    $slicerExecutable = Resolve-SlicerExecutable `
        -CommandLinePath $SlicerPath `
        -ConfiguredPath $configuration.SlicerPath
    if ($ResolveOnly) {
        [Console]::Out.WriteLine($slicerExecutable)
        exit 0
    }

    $hadPriorCase = Test-Path Env:MYOMA_REVIEW_LITE_CASE
    $priorCase = $env:MYOMA_REVIEW_LITE_CASE
    $hadPriorDataRoot = Test-Path Env:MYOMA_REVIEW_LITE_DATA_ROOT
    $priorDataRoot = $env:MYOMA_REVIEW_LITE_DATA_ROOT
    try {
        $env:MYOMA_REVIEW_LITE_CASE = [System.IO.Path]::GetFullPath($queuePath)
        $env:MYOMA_REVIEW_LITE_DATA_ROOT = $configuration.DatasetRoot
        $arguments = @(
            '--additional-module-paths',
            ('"' + $moduleRoot + '"'),
            '--python-code',
            '"slicer.util.selectModule(''MyomaReviewLite'')"'
        )
        $process = Start-Process -FilePath $slicerExecutable -ArgumentList $arguments -PassThru
        if ($Wait) {
            $process.WaitForExit()
            if ($process.ExitCode -ne 0) {
                throw "Slicer exited with code $($process.ExitCode)"
            }
        }
    }
    finally {
        if ($hadPriorCase) {
            $env:MYOMA_REVIEW_LITE_CASE = $priorCase
        }
        else {
            Remove-Item Env:MYOMA_REVIEW_LITE_CASE -ErrorAction SilentlyContinue
        }
        if ($hadPriorDataRoot) {
            $env:MYOMA_REVIEW_LITE_DATA_ROOT = $priorDataRoot
        }
        else {
            Remove-Item Env:MYOMA_REVIEW_LITE_DATA_ROOT -ErrorAction SilentlyContinue
        }
    }
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
