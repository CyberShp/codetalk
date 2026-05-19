@echo off
REM package-vendor.bat — Run on a machine WITH internet to prepare offline vendor bundles.
REM Populates deployer\vendor\ with GitNexus and tiktoken cache for intranet deployment.

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

REM ---- tiktoken cache ----
echo.
echo [2/3] Packaging tiktoken cache...

set "TK_CACHE=%LOCALAPPDATA%\tiktoken_v1"
if not exist "!TK_CACHE!" (
    echo tiktoken cache not found at !TK_CACHE!
    echo Downloading all tiktoken encodings...
    python -c "import tiktoken; [tiktoken.get_encoding(n) for n in tiktoken.list_encoding_names()]" 2>nul
)

if exist "!TK_CACHE!" (
    if exist "vendor\tiktoken_cache" rmdir /s /q "vendor\tiktoken_cache"
    xcopy "!TK_CACHE!" "vendor\tiktoken_cache\" /E /I /Q /Y >nul
    echo tiktoken cache packaged: vendor\tiktoken_cache\
) else (
    echo WARNING: tiktoken cache not available. DeepWiki may fail on intranet.
)

REM ---- Zoekt binaries ----
echo.
echo [3/3] Packaging Zoekt binaries...

where go >nul 2>&1
if errorlevel 1 (
    echo WARNING: Go not found in PATH. Skipping Zoekt packaging.
    echo         Install Go from https://go.dev/dl/ then re-run to include Zoekt.
    goto :zoekt_done
)

for /f "tokens=3" %%v in ('go version') do echo Found Go: %%v

REM Windows amd64 — native compile; GOBIN accepted by go install for same-platform targets
echo Compiling Zoekt for Windows/amd64...
if exist "vendor\zoekt\win32" rmdir /s /q "vendor\zoekt\win32"
mkdir "vendor\zoekt\win32"
set "GOBIN=%CD%\vendor\zoekt\win32"
set "GOOS=windows"
set "GOARCH=amd64"
set "CGO_ENABLED=0"
go install github.com/sourcegraph/zoekt/cmd/zoekt-webserver@latest
go install github.com/sourcegraph/zoekt/cmd/zoekt-index@latest
if exist "vendor\zoekt\win32\zoekt-webserver.exe" if exist "vendor\zoekt\win32\zoekt-index.exe" (
    echo Zoekt Windows binaries packaged: vendor\zoekt\win32\
) else (
    echo WARNING: Zoekt Windows build incomplete. Check go output above.
)

REM Linux amd64 ^(cross-compile^) — isolated temp GOPATH prevents stale-artifact false positives;
REM exit codes are captured before copy so build failures are not silently ignored.
echo Compiling Zoekt for Linux/amd64...
if exist "vendor\zoekt\linux" rmdir /s /q "vendor\zoekt\linux"
mkdir "vendor\zoekt\linux"
set "ZOEKT_GOPATH=%TEMP%\zoekt-cross-%RANDOM%"
mkdir "!ZOEKT_GOPATH!"
set "GOPATH=!ZOEKT_GOPATH!"
set "GOBIN="
set "GOOS=linux"
set "ZOEKT_LINUX_FAIL=0"
go install github.com/sourcegraph/zoekt/cmd/zoekt-webserver@latest
if errorlevel 1 set "ZOEKT_LINUX_FAIL=1"
go install github.com/sourcegraph/zoekt/cmd/zoekt-index@latest
if errorlevel 1 set "ZOEKT_LINUX_FAIL=1"
if "!ZOEKT_LINUX_FAIL!"=="0" (
    copy "!ZOEKT_GOPATH!\bin\linux_amd64\zoekt-webserver" "vendor\zoekt\linux\zoekt-webserver" >nul
    copy "!ZOEKT_GOPATH!\bin\linux_amd64\zoekt-index" "vendor\zoekt\linux\zoekt-index" >nul 2>&1
)
rmdir /s /q "!ZOEKT_GOPATH!"
set "GOPATH="
if exist "vendor\zoekt\linux\zoekt-webserver" if exist "vendor\zoekt\linux\zoekt-index" (
    echo Zoekt Linux binaries packaged: vendor\zoekt\linux\
) else (
    echo WARNING: Zoekt Linux cross-compile incomplete. Check go output above.
)

REM Restore Go env vars
set "GOBIN="
set "GOOS="
set "GOARCH="
set "CGO_ENABLED="

:zoekt_done

echo.
echo ============================================
echo  Vendor packaging complete!
echo  Include deployer\vendor\ in your zip distribution.
echo  Zoekt binaries: vendor\zoekt\win32\ ^(Windows^) and vendor\zoekt\linux\ ^(Linux^)
echo ============================================
pause
