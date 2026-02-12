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
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
$UserId = "$env:USERDOMAIN\$env:USERNAME"

if (-not $PythonExe) {
    Write-Error "Python not found in PATH. Please install Python 3 first."
    exit 1
}

switch ($Action) {
    "install" {
        $MainScript = Join-Path $ScriptDir "main.py"
        if (-not (Test-Path $MainScript)) {
            Write-Error "main.py not found at $MainScript"
            exit 1
        }

        # Remove existing task if present
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

        $TaskAction = New-ScheduledTaskAction `
            -Execute $PythonExe `
            -Argument "`"$MainScript`" --no-console" `
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
            -Force

        Write-Host "Task '$TaskName' registered." -ForegroundColor Green
        Write-Host "Trigger: At system startup" -ForegroundColor Green
        Write-Host "Logon mode: Run whether user is logged on or not (Do not store password / S4U)" -ForegroundColor Green
        Write-Host "Compatibility: Windows 10/11" -ForegroundColor Green
    }

    "uninstall" {
        $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($existing) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Write-Host "Task '$TaskName' removed." -ForegroundColor Yellow
        } else {
            Write-Host "Task '$TaskName' not found. Nothing to remove." -ForegroundColor Gray
        }
    }
}
