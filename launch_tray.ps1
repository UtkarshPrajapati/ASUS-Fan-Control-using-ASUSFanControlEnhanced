param(
    [Parameter(Mandatory=$true)]
    [string]$PythonExe,
    [Parameter(Mandatory=$true)]
    [string]$MainScript,
    [Parameter(Mandatory=$false)]
    [string]$CoreTaskName = "ASUSFanControlEnhanced"
)

# Stop the startup/background task before launching tray UI.
try {
    Stop-ScheduledTask -TaskName $CoreTaskName -ErrorAction SilentlyContinue
} catch {
    # Ignore; tray can still run.
}

Start-Sleep -Milliseconds 500

& $PythonExe $MainScript --tray
exit $LASTEXITCODE
