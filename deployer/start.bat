@echo off
cd /d "%~dp0"

for %%P in ("py -3.12" "py -3.11" "py -3.10" "python" "python3") do (
    %%~P start.py
    if not errorlevel 1 exit /b 0
)

echo.
echo Error: Could not launch the deployer. Make sure Python 3.10+ is installed and on your PATH.
pause
exit /b 1
