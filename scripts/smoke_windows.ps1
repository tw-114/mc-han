[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ReleaseDirectory,
    [switch]$CheckVisibleWindow,
    [switch]$CheckEndToEnd
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ResolvedRelease = (Resolve-Path -LiteralPath $ReleaseDirectory).Path
$ExePath = Join-Path $ResolvedRelease "mc-han.exe"
if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
    throw "mc-han.exe was not found in the release directory."
}

$SmokeProcess = Start-Process -FilePath $ExePath -ArgumentList "--smoke-test" `
    -WorkingDirectory $ResolvedRelease -PassThru -WindowStyle Hidden
if (-not $SmokeProcess.WaitForExit(20000)) {
    $SmokeProcess.Kill()
    throw "The executable smoke test timed out."
}
if ($SmokeProcess.ExitCode -ne 0) {
    throw "The executable smoke test failed with exit code $($SmokeProcess.ExitCode)."
}

if ($CheckEndToEnd) {
    $EndToEndProcess = Start-Process -FilePath $ExePath `
        -ArgumentList "--e2e-smoke-test" `
        -WorkingDirectory $ResolvedRelease -PassThru -WindowStyle Hidden
    if (-not $EndToEndProcess.WaitForExit(120000)) {
        $EndToEndProcess.Kill()
        throw "The executable end-to-end smoke test timed out."
    }
    if ($EndToEndProcess.ExitCode -ne 0) {
        throw "The executable end-to-end smoke test failed with exit code $($EndToEndProcess.ExitCode)."
    }
}

if ($CheckVisibleWindow) {
    $WindowProcess = Start-Process -FilePath $ExePath -WorkingDirectory $ResolvedRelease -PassThru
    $Deadline = [DateTime]::UtcNow.AddSeconds(15)
    do {
        Start-Sleep -Milliseconds 200
        $WindowProcess.Refresh()
    } while (-not $WindowProcess.HasExited -and
        -not $WindowProcess.MainWindowHandle -and
        [DateTime]::UtcNow -lt $Deadline)

    if ($WindowProcess.HasExited -or -not $WindowProcess.MainWindowHandle) {
        if (-not $WindowProcess.HasExited) {
            $WindowProcess.Kill()
        }
        throw "The normal application window did not become visible."
    }
    Start-Sleep -Milliseconds 500
    $CloseRequested = $WindowProcess.CloseMainWindow()
    if (-not $CloseRequested) {
        $WindowShell = New-Object -ComObject WScript.Shell
        foreach ($Attempt in 1..5) {
            $CloseRequested = $WindowShell.AppActivate($WindowProcess.Id)
            if ($CloseRequested) {
                break
            }
            Start-Sleep -Milliseconds 200
        }
        if ($CloseRequested) {
            $WindowShell.SendKeys("%{F4}")
        }
    }
    if (-not $CloseRequested) {
        $WindowProcess.Kill()
        throw "The normal application window did not accept a close request."
    }
    if (-not $WindowProcess.WaitForExit(10000)) {
        $WindowProcess.Kill()
        throw "The normal application left a running process after close."
    }
}

Write-Host "Windows executable smoke test passed."
