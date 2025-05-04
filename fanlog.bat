@echo off
setlocal enabledelayedexpansion
set LOGFILE=%USERPROFILE%\Apps\AsusFanControlEnhanced\fan_control.log
powershell -command "& {while ($true) {Get-Content '%LOGFILE%' -Tail 10; Start-Sleep -Seconds 2; Clear-Host}}"