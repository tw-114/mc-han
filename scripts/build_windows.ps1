[CmdletBinding()]
param(
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $PythonExe) {
    $PythonExe = Join-Path $RepoRoot ".venv-release\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Release Python was not found. Create .venv-release and install .[qt,dev,release]."
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Program,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE."
    }
}

function Remove-ReleaseDirectory {
    param([Parameter(Mandatory = $true)][string]$Name)
    $Candidate = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $Name))
    $ExpectedParent = [System.IO.Path]::GetFullPath($RepoRoot).TrimEnd("\") + "\"
    if (-not $Candidate.StartsWith($ExpectedParent, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a directory outside the repository."
    }
    if (Test-Path -LiteralPath $Candidate) {
        Remove-Item -LiteralPath $Candidate -Recurse -Force
    }
}

function Prune-UnusedQtComponents {
    param([Parameter(Mandatory = $true)][string]$ReleaseDirectory)

    $QtRoot = Join-Path $ReleaseDirectory "_internal\PySide6"
    $PluginRoot = Join-Path $QtRoot "plugins"
    $AllowedPlugins = @(
        "platforms\qwindows.dll",
        "styles\qmodernwindowsstyle.dll"
    )
    if (Test-Path -LiteralPath $PluginRoot) {
        Get-ChildItem -LiteralPath $PluginRoot -Recurse -File | ForEach-Object {
            $RelativePlugin = $_.FullName.Substring($PluginRoot.Length + 1)
            if ($RelativePlugin -notin $AllowedPlugins) {
                Remove-Item -LiteralPath $_.FullName -Force
            }
        }
        Get-ChildItem -LiteralPath $PluginRoot -Recurse -Directory |
            Sort-Object FullName -Descending |
            Where-Object { -not (Get-ChildItem -LiteralPath $_.FullName -Force) } |
            Remove-Item -Force
    }

    $UnusedQtBinaries = @(
        "Qt6Pdf.dll",
        "Qt6Qml.dll",
        "Qt6QmlMeta.dll",
        "Qt6QmlModels.dll",
        "Qt6QmlWorkerScript.dll",
        "Qt6Quick.dll",
        "Qt6Svg.dll",
        "Qt6VirtualKeyboard.dll"
    )
    foreach ($Binary in $UnusedQtBinaries) {
        $BinaryPath = Join-Path $QtRoot $Binary
        if (Test-Path -LiteralPath $BinaryPath -PathType Leaf) {
            Remove-Item -LiteralPath $BinaryPath -Force
        }
    }
}

function Assert-ReleaseContents {
    param([Parameter(Mandatory = $true)][string]$ReleaseDirectory)
    $ExePath = Join-Path $ReleaseDirectory "mc-han.exe"
    if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
        throw "PyInstaller did not produce dist\mc-han\mc-han.exe."
    }
    foreach ($Required in @("README.md", "THIRD_PARTY_NOTICES.txt", "licenses")) {
        if (-not (Test-Path -LiteralPath (Join-Path $ReleaseDirectory $Required))) {
            throw "Required release component is missing: $Required"
        }
    }
    $PlatformPlugin = Get-ChildItem -LiteralPath $ReleaseDirectory -Recurse -File -Filter "qwindows.dll"
    if (-not $PlatformPlugin) {
        throw "The Qt Windows platform plugin qwindows.dll is missing."
    }
    $UnexpectedQtComponents = @(
        "Qt6Pdf.dll",
        "Qt6Qml.dll",
        "Qt6Quick.dll",
        "Qt6Svg.dll",
        "Qt6VirtualKeyboard.dll"
    ) | Where-Object {
        Test-Path -LiteralPath (Join-Path $ReleaseDirectory "_internal\PySide6\$_")
    }
    if ($UnexpectedQtComponents) {
        throw "Release audit found unused Qt modules."
    }

    $ForbiddenNames = @(
        "config.json",
        "translations.sqlite",
        "translation_cache.jsonl",
        "install_manifest.json"
    )
    $Forbidden = Get-ChildItem -LiteralPath $ReleaseDirectory -Recurse -Force | Where-Object {
        $_.Name -in $ForbiddenNames -or
        $_.Name -eq ".git" -or
        $_.Name -eq ".venv" -or
        $_.Name -eq ".venv-release" -or
        $_.Extension -eq ".jar"
    }
    if ($Forbidden) {
        throw "Release audit found forbidden local or user data."
    }

    $SensitiveRoots = @($RepoRoot, $env:USERPROFILE) | Where-Object { $_ }
    $TextFiles = Get-ChildItem -LiteralPath $ReleaseDirectory -Recurse -File | Where-Object {
        $_.Extension -in @(".txt", ".md", ".json", ".ini", ".cfg", ".yaml", ".yml")
    }
    foreach ($TextFile in $TextFiles) {
        foreach ($SensitiveRoot in $SensitiveRoots) {
            if (Select-String -LiteralPath $TextFile.FullName -SimpleMatch $SensitiveRoot -Quiet) {
                throw "Release text contains an absolute development path."
            }
        }
        if (Select-String -LiteralPath $TextFile.FullName -Pattern "sk-[A-Za-z0-9_-]{12,}" -Quiet) {
            throw "Release text contains a value resembling an API key."
        }
    }
}

Push-Location $RepoRoot
try {
    Remove-ReleaseDirectory "build"
    Remove-ReleaseDirectory "dist"

    $TestTemp = Join-Path $RepoRoot "build\pytest-temp"
    New-Item -ItemType Directory -Path $TestTemp -Force | Out-Null
    $env:PYTHONNOUSERSITE = "1"
    $env:QT_QPA_PLATFORM = "offscreen"
    $env:TEMP = $TestTemp
    $env:TMP = $TestTemp

    Invoke-Checked -Program $PythonExe -Arguments @(
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "--basetemp",
        $TestTemp
    )
    Invoke-Checked -Program $PythonExe -Arguments @("-m", "compileall", "-q", "src")
    Invoke-Checked -Program $PythonExe -Arguments @(
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        (Join-Path $RepoRoot "dist"),
        "--workpath",
        (Join-Path $RepoRoot "build"),
        (Join-Path $RepoRoot "packaging\mc-han-qt.spec")
    )

    $ReleaseDirectory = Join-Path $RepoRoot "dist\mc-han"
    Prune-UnusedQtComponents $ReleaseDirectory
    Copy-Item -LiteralPath (Join-Path $RepoRoot "README.md") `
        -Destination (Join-Path $ReleaseDirectory "README.md")
    Copy-Item -LiteralPath (Join-Path $RepoRoot "THIRD_PARTY_NOTICES.txt") `
        -Destination (Join-Path $ReleaseDirectory "THIRD_PARTY_NOTICES.txt")
    Copy-Item -LiteralPath (Join-Path $RepoRoot "licenses") `
        -Destination (Join-Path $ReleaseDirectory "licenses") -Recurse
    Assert-ReleaseContents $ReleaseDirectory
    Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
    & (Join-Path $PSScriptRoot "smoke_windows.ps1") `
        -ReleaseDirectory $ReleaseDirectory `
        -CheckEndToEnd

    $ArchiveName = (& $PythonExe -c "from mc_han.release_info import windows_archive_name; print(windows_archive_name())").Trim()
    if ($LASTEXITCODE -ne 0 -or -not $ArchiveName) {
        throw "Could not derive the release archive name."
    }
    $ArchivePath = Join-Path $RepoRoot "dist\$ArchiveName"
    Compress-Archive -LiteralPath $ReleaseDirectory -DestinationPath $ArchivePath -CompressionLevel Optimal

    $Digest = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    $ChecksumName = (& $PythonExe -c "from mc_han.release_info import windows_checksum_name; print(windows_checksum_name())").Trim()
    if ($LASTEXITCODE -ne 0 -or -not $ChecksumName) {
        throw "Could not derive the release checksum name."
    }
    $ChecksumPath = Join-Path $RepoRoot "dist\$ChecksumName"
    [System.IO.File]::WriteAllText(
        $ChecksumPath,
        "$Digest  $ArchiveName`n",
        [System.Text.UTF8Encoding]::new($false)
    )

    Write-Host "Release directory: $ReleaseDirectory"
    Write-Host "Release archive: $ArchivePath"
    Write-Host "SHA-256: $Digest"
}
finally {
    Pop-Location
}
