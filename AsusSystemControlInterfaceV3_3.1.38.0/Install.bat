@echo off
chcp 437 > NUL
pushd "%~dp0"

setlocal enableextensions
set asussci_logpath=%ProgramData%\ASUS\ASUS System Control Interface\log
echo Install ASUS System Control Interface  V3 driver
echo Please wait......
echo.
echo Create folder: "%asussci_logpath%"
mkdir "%asussci_logpath%"
echo logpath="%asussci_logpath%"
echo.
call :InstallSub > "%asussci_logpath%\setup_asci.log" 2>&1
echo.
echo Exit installation script!
endlocal
exit /b


:InstallSub
call :OutputLogWithTime ASCI V3 Driver installer
echo.
call :OutputLogWithTime Start installation procedure
echo.

:: ==========================================================
:: Disable service recovery configuration
:: ==========================================================
call :ScRecoveryDisableService ASUSSystemAnalysis
call :ScRecoveryDisableService ASUSSystemDiagnosis
call :ScRecoveryDisableService ASUSOptimization
call :ScRecoveryDisableService ASUSSwitch
call :ScRecoveryDisableService ASUSSoftwareManager
call :ScRecoveryDisableService AsusAppService

:: ==========================================================
:: Stop service before install ASCI driver
:: ==========================================================
call :ScStopService ASUSSystemAnalysis
call :ScStopService ASUSSystemDiagnosis
call :ScStopService ASUSOptimization
call :ScStopService ASUSSwitch
call :ScStopService AsusAppService

:: ==========================================================
:: Try to kill the process that can't stop
:: ==========================================================
call :OutputLogWithTime ASCI V3 [asussci2.inf] Information:
findstr /l "DriverVer" "%~dp0\asussci2.inf"
echo.
call :OutputASUSDeviceInformation
echo.
timeout /t 10 /nobreak
call :OutputLogWithTime Check all services have been stop...
taskkill /F /IM AsusOptimization.exe
taskkill /F /IM AsusSwitch.exe
taskkill /F /IM AsusSystemAnalysis.exe
taskkill /F /IM AsusSystemDiagnosis.exe
taskkill /F /IM AsusAppService.exe
timeout /t 1 /nobreak

:: ==========================================================
:: Intall ASCI V3 driver
:: ==========================================================
echo ----------------------------------------------------------
call :OutputLogWithTime Start to run pnputil tool...
echo.
pnputil -i -a .\asussci2.inf
set pnputilExitCode=%errorlevel%

call :OutputLogWithTime Fill the pnputil tool result to registry...

reg add "HKLM\SOFTWARE\ASUS\ASUS System Control Interface" /v Result /t REG_SZ /d %pnputilExitCode% /f /reg:64
call :OutputLogWithTime pnputil Exit Code=%pnputilExitCode%
echo ----------------------------------------------------------
echo.
:: ==========================================================
:: Enable service recovery configuration
:: ==========================================================
timeout /t 1 /nobreak
echo.
call :OutputLogWithTime Update service configuration
call :ScRecoveryEnableService ASUSSystemAnalysis
call :ScRecoveryEnableService ASUSSystemDiagnosis
call :ScRecoveryEnableService ASUSOptimization
call :ScRecoveryEnableService ASUSSwitch
call :ScRecoveryEnableService ASUSSoftwareManager
call :ScRecoveryEnableService AsusAppService

echo.
:: ==========================================================
:: Make sure that all services have been started
:: ==========================================================
call :OutputLogWithTime Check all service has been started...
call :ScStartService ASUSOptimization
call :ScStartService ASUSSwitch
call :ScStartService ASUSSystemDiagnosis
call :ScStartService ASUSSystemAnalysis
call :ScStartService ASUSSoftwareManager
call :ScStartService AsusAppService

echo.
call :GetUnixTime UNIX_TIME
echo Current UNIX Timestamp=%UNIX_TIME%

call :OutputLogWithTime Backup setup_asci.log
echo logpath="%asussci_logpath%"
mkdir "%asussci_logpath%"
copy "%asussci_logpath%\setup_asci.log" "%asussci_logpath%\setup_asci_%UNIX_TIME%.log"
echo.

call :OutputLogWithTime Backup setupapi.dev.log
copy %windir%\INF\setupapi.dev.log "%asussci_logpath%\setupapi.dev.log"
echo.

call :OutputLogWithTime Update timestatmp in registry...
reg add "HKLM\SOFTWARE\ASUS\ASUS System Control Interface" /v LastSelfUpdateFinishedTime /t REG_SZ /d %UNIX_TIME% /f /reg:64

call :OutputLogWithTime ASCI driver installation Finished!

:: call :OutputLogWithTime Try to get current ASCI driver version...
:: timeout /t 10 /nobreak
:: call :OutputASUSDeviceInformation
:: call :OutputLogWithTime Dump device information finisned!

echo.
goto :EOF

:: ==========================================================
::  Helper method
:: ==========================================================

:ScConfigService
setlocal enableextensions
echo ----------------------------------------------------------
sc config "%1" start=%2
echo Stop config %1 ExitCode=%errorlevel%
echo ----------------------------------------------------------
endlocal & goto :EOF

:ScRecoveryDisableService
setlocal enableextensions
echo ----------------------------------------------------------
sc failure "%1" reset=0 actions=''
echo disable recovery %1 ExitCode=%errorlevel%
echo ----------------------------------------------------------
endlocal & goto :EOF

:ScRecoveryEnableService
setlocal enableextensions
echo ----------------------------------------------------------
sc failure "%1" reset=0 actions=restart/0/restart/0/restart/0
echo enable recovery %1 ExitCode=%errorlevel%
echo ----------------------------------------------------------
endlocal & goto :EOF

:ScStopService
setlocal enableextensions
echo ----------------------------------------------------------
sc stop %1
echo Stop service %1 ExitCode=%errorlevel%
echo ----------------------------------------------------------
endlocal & goto :EOF

:ScStartService
setlocal enableextensions
echo ----------------------------------------------------------
sc start %1
echo Start service %1 ExitCode=%errorlevel%
echo ----------------------------------------------------------
endlocal & goto :EOF

:GetUnixTime
for /f %%i in ('powershell "[DateTimeOffset]::Now.ToUnixTimeSeconds()"') do (
	set ut=%%i
)
endlocal & set "%1=%ut%" & goto :EOF

:OutputLogWithTime
setlocal enableextensions
for /F "tokens=2" %%i in ('date /t') do set mydate=%%i
set mytime=%time%
echo [%mydate%:%mytime%] %*
endlocal & goto :EOF

:OutputAsusDeviceInformation
setlocal enableextensions
echo Current Device Information:
powershell.exe -command "Get-WmiObject Win32_PnPSignedDriver | where-object {$_.DeviceID -like '*ASUS*'} | Select DeviceName, DeviceId, DriverVersion, DriverDate"
endlocal & goto :EOF
