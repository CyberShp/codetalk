@echo off
cd /d "%~dp0"

python start.py
if %ERRORLEVEL% neq 0 (
    python3 start.py
    if %ERRORLEVEL% neq 0 (
        echo.
        echo Error: Could not launch the deployer. Make sure Python 3.10+ is installed and on your PATH.
        pause
        exit /b 1
    )
)
