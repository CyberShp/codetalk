@echo off
REM package-vendor.bat — Run on a machine WITH internet to prepare offline vendor bundles.
REM Populates deployer\vendor\ with GitNexus, Python wheels, and tiktoken cache for intranet deployment.

setlocal enabledelayedexpansion
cd /d "%~dp0\.."

echo ============================================
echo  CodeTalk Vendor Packager
echo ============================================
echo.

REM ---- GitNexus ----
echo [1/3] Packaging GitNexus...

set "GN_SRC="
for /f "tokens=*" %%i in ('npm root -g 2^>nul') do set "GN_SRC=%%i\gitnexus"

if not exist "!GN_SRC!\package.json" (
    echo GitNexus not found in npm global. Installing now...
    npm install -g gitnexus
    for /f "tokens=*" %%i in ('npm root -g 2^>nul') do set "GN_SRC=%%i\gitnexus"
)

if not exist "!GN_SRC!\package.json" (
    echo ERROR: Could not find or install GitNexus. Aborting.
    pause
    exit /b 1
)

echo Found GitNexus at: !GN_SRC!

if exist "vendor\gitnexus" (
    echo Removing old vendor\gitnexus...
    rmdir /s /q "vendor\gitnexus"
)

echo Copying GitNexus package (this may take a minute)...
xcopy "!GN_SRC!" "vendor\gitnexus\" /E /I /Q /Y >nul
echo GitNexus packaged: vendor\gitnexus\

REM ---- Python wheels ----
echo.
echo [2/3] Packaging Python wheels...

if exist "vendor\wheels" rmdir /s /q "vendor\wheels"
mkdir "vendor\wheels" >nul

python -m pip download -r requirements.txt -d vendor\wheels
if errorlevel 1 (
    echo ERROR: failed to download deployer Python wheels.
    pause
    exit /b 1
)

if exist "..\backend\requirements.txt" (
    python -m pip download -r ..\backend\requirements.txt -d vendor\wheels
    if errorlevel 1 (
        echo ERROR: failed to download backend Python wheels.
        pause
        exit /b 1
    )
)

dir "vendor\wheels\*.whl" /b /a:-d 2>nul >nul
if errorlevel 1 (
    echo ERROR: vendor\wheels has no .whl files after download.
    pause
    exit /b 1
)

echo Python wheels packaged: vendor\wheels\

REM ---- tiktoken cache ----
echo.
echo [3/3] Packaging tiktoken cache...

set "TK_CACHE=%LOCALAPPDATA%\tiktoken_v1"
if not exist "!TK_CACHE!" (
    echo tiktoken cache not found at !TK_CACHE!
    echo Downloading all tiktoken encodings...
    python -c "import tiktoken; [tiktoken.get_encoding(n) for n in tiktoken.list_encoding_names()]"
    if errorlevel 1 (
        echo ERROR: tiktoken download failed. Check Python installation and network connectivity.
        echo        Install tiktoken manually: pip install tiktoken
        echo        Then re-run this script on a machine with internet access.
        pause
        exit /b 1
    )
)

if exist "!TK_CACHE!" (
    if exist "vendor\tiktoken_cache" rmdir /s /q "vendor\tiktoken_cache"
    xcopy "!TK_CACHE!" "vendor\tiktoken_cache\" /E /I /Q /Y >nul
    REM Verify the copy actually produced BPE files — an empty cache is useless.
    dir "vendor\tiktoken_cache\*" /b /a:-d 2>nul >nul
    if errorlevel 1 (
        echo ERROR: vendor\tiktoken_cache is empty after copy. Source may have no BPE files.
        echo        Delete %LOCALAPPDATA%\tiktoken_v1 and re-run to force a fresh download.
        pause
        exit /b 1
    )
    echo tiktoken cache packaged: vendor\tiktoken_cache\
) else (
    echo ERROR: tiktoken cache not available at !TK_CACHE!. Cannot create vendor bundle.
    echo        Ensure Python ^(with tiktoken installed^) and internet access, then re-run.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Vendor packaging complete!
echo  Include deployer\vendor\ in your zip distribution.
echo ============================================
pause
