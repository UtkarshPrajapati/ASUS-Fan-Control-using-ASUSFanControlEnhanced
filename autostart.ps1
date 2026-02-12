# autostart.ps1 - Register / unregister ASUSFanControlEnhanced as a startup task (D3)
#
# Usage:
#   .\autostart.ps1 install      - Create a Task Scheduler task that starts at system startup
#   .\autostart.ps1 uninstall    - Remove the Task Scheduler task
#
# Notes:
# - Configures "Run whether user is logged on or not".
# - Configures "Do not store password" by using S4U logon type.
# - Uses "Configure for: Windows 8".

param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("install", "uninstall")]
    [string]$Action
)

$TaskName = "ASUSFanControlEnhanced"
$TrayTaskName = "ASUSFanControlEnhancedTray"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$TrayLauncher = Join-Path $ScriptDir "launch_tray.ps1"
$UserId = "$env:USERDOMAIN\$env:USERNAME"

# Resolve a real python.exe path (NOT the WindowsApps alias, which fails with
# "Access is denied" when launched by Task Scheduler).
$PythonExe = $null

# 1) Prefer explicit non-WindowsApps python from PATH lookup.
$pythonCandidates = @(
    Get-Command python -All -ErrorAction SilentlyContinue `
        | Where-Object { $_.Source -and $_.Source -notlike "*\WindowsApps\python.exe" } `
        | Select-Object -ExpandProperty Source -Unique
)

# 2) Common per-user install locations (extra fallback).
$pythonCandidates += "$env:LOCALAPPDATA\Python\bin\python.exe"
$pythonCandidates += "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
$pythonCandidates += "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"
$pythonCandidates += "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe"

foreach ($candidate in $pythonCandidates) {
    if ($candidate -and (Test-Path $candidate) -and ($candidate -notlike "*\WindowsApps\python.exe")) {
        $PythonExe = $candidate
        break
    }
}

if ((-not $PythonExe) -or (-not (Test-Path $PythonExe)) -or ($PythonExe -like "*\WindowsApps\python.exe")) {
    Write-Error "Could not resolve a real python.exe path (WindowsApps alias is not supported for Task Scheduler)."
    exit 1
}

switch ($Action) {
    "install" {
        $MainScript = Join-Path $ScriptDir "main.py"
        if (-not (Test-Path $MainScript)) {
            Write-Error "main.py not found at $MainScript"
            exit 1
        }
        if (-not (Test-Path $TrayLauncher)) {
            Write-Error "launch_tray.ps1 not found at $TrayLauncher"
            exit 1
        }
        Write-Host "Using Python: $PythonExe" -ForegroundColor Green

        # Register-ScheduledTask -Force updates existing tasks in place.

        # Core/background task (runs pre-login for thermal safety)
        $TaskAction = New-ScheduledTaskAction `
            -Execute $PythonExe `
            -Argument "`"$MainScript`" --no-console --no-tray" `
            -WorkingDirectory $ScriptDir

        # Requirement 1: run at system startup (not logon)
        $TaskTrigger = New-ScheduledTaskTrigger -AtStartup

        # Requirement 2: configure compatibility
        $settingsParams = @{
            AllowStartIfOnBatteries   = $true
            DontStopIfGoingOnBatteries = $true
            ExecutionTimeLimit         = ([TimeSpan]::Zero)
            StartWhenAvailable         = $true
        }
        try {
            $TaskSettings = New-ScheduledTaskSettingsSet @settingsParams -Compatibility Win8
        } catch {
            # Fallback for older ScheduledTasks modules.
            $TaskSettings = New-ScheduledTaskSettingsSet @settingsParams
            Write-Warning "Could not set Compatibility=Win8 via PowerShell module. Leaving default compatibility."
        }

        # Requirement 3: "Run whether user is logged on or not"
        # Requirement 4: "Do not store password" (S4U logon type)
        $TaskPrincipal = New-ScheduledTaskPrincipal `
            -UserId $UserId `
            -RunLevel Highest `
            -LogonType S4U

        Register-ScheduledTask `
            -TaskName $TaskName `
            -Action $TaskAction `
            -Trigger $TaskTrigger `
            -Settings $TaskSettings `
            -Principal $TaskPrincipal `
            -Description "Automatically control ASUS laptop fan speeds based on CPU temperature." `
            -Force `
            -ErrorAction Stop | Out-Null

        # Tray/UI task (runs at user logon in interactive desktop session).
        # It stops the startup task first to avoid duplicate controller loops.
        $TrayAction = New-ScheduledTaskAction `
            -Execute "powershell.exe" `
            -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$TrayLauncher`" -PythonExe `"$PythonExe`" -MainScript `"$MainScript`" -CoreTaskName `"$TaskName`"" `
            -WorkingDirectory $ScriptDir

        $TrayTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

        $TraySettings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -StartWhenAvailable

        $TrayPrincipal = New-ScheduledTaskPrincipal `
            -UserId $UserId `
            -RunLevel Highest `
            -LogonType Interactive

        $trayRegistered = $true
        try {
            Register-ScheduledTask `
                -TaskName $TrayTaskName `
                -Action $TrayAction `
                -Trigger $TrayTrigger `
                -Settings $TraySettings `
                -Principal $TrayPrincipal `
                -Description "Launch ASUS Fan Control tray UI on user logon." `
                -Force `
                -ErrorAction Stop | Out-Null
        } catch {
            $trayRegistered = $false
            Write-Warning "Failed to register tray task '$TrayTaskName': $($_.Exception.Message)"
        }

        Write-Host "Task '$TaskName' registered." -ForegroundColor Green
        Write-Host "  Trigger: At system startup" -ForegroundColor Green
        Write-Host "  Mode: Run whether user is logged on or not (Do not store password / S4U)" -ForegroundColor Green
        Write-Host "  Purpose: Background thermal safety (no tray)" -ForegroundColor Green

        if ($trayRegistered) {
            Write-Host "Task '$TrayTaskName' registered." -ForegroundColor Green
            Write-Host "  Trigger: At user logon" -ForegroundColor Green
            Write-Host "  Mode: Interactive user session" -ForegroundColor Green
            Write-Host "  Purpose: Tray icon + visible console by default" -ForegroundColor Green
        } else {
            Write-Host "Task '$TrayTaskName' was NOT registered." -ForegroundColor Red
            Write-Host "  Fix and rerun: .\autostart.ps1 install" -ForegroundColor Red
        }
    }

    "uninstall" {
        $existingCore = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        $existingTray = Get-ScheduledTask -TaskName $TrayTaskName -ErrorAction SilentlyContinue

        if ($existingCore) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Write-Host "Task '$TaskName' removed." -ForegroundColor Yellow
        } else {
            Write-Host "Task '$TaskName' not found. Nothing to remove." -ForegroundColor Gray
        }

        if ($existingTray) {
            Unregister-ScheduledTask -TaskName $TrayTaskName -Confirm:$false
            Write-Host "Task '$TrayTaskName' removed." -ForegroundColor Yellow
        } else {
            Write-Host "Task '$TrayTaskName' not found. Nothing to remove." -ForegroundColor Gray
        }
    }
}
