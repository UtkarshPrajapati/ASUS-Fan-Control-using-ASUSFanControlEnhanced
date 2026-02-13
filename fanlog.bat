@echo off
setlocal enabledelayedexpansion
set "BASE_DIR=%~dp0"
set "LOGFILE=%BASE_DIR%runtime\logs\fan_control.log"
if not exist "%LOGFILE%" (
    type nul > "%LOGFILE%"
)
powershell -command "& {while ($true) {Get-Content '%LOGFILE%' -Tail 10; Start-Sleep -Seconds 2; Clear-Host}}"