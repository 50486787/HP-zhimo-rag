@echo off
cd /d "%~dp0"

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting admin privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo Installing HTTPS certificate...
certutil -addstore Root "%~dp0rootCA.pem"

if %errorlevel% equ 0 (
    echo.
    echo SUCCESS - HTTPS cert installed.
    echo Browser will no longer show security warnings.
) else (
    echo.
    echo FAILED - Please screenshot this and contact admin.
)

pause
