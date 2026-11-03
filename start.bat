@echo off
rem AI Jukebox launcher for Windows. Double-click this file, or run start.bat in a terminal.
rem It creates .env on the first run (asking for the class key), installs what is needed,
rem starts the app and opens http://localhost:8000. Stop the app with Ctrl+C.
setlocal EnableDelayedExpansion
cd /d "%~dp0"
echo == AI Jukebox launcher (%cd%)

rem 1. Settings: .env holds the key (never uploaded to GitHub).
if not exist .env (
  copy .env.example .env >nul
  echo Created .env from .env.example.
)
set NEEDKEY=0
findstr /r /c:"^CLASS_API_KEY=replace-me" /c:"^CLASS_API_KEY= *$" .env >nul
if !errorlevel! equ 0 set NEEDKEY=1
if !NEEDKEY! equ 1 if exist class-endpoints.txt (
  findstr /i /r /c:"^ *API Key *:" /c:"^ *CLASS_API_KEY *=" class-endpoints.txt >nul
  if !errorlevel! equ 0 (
    echo Using the API key from class-endpoints.txt.
    set NEEDKEY=0
  )
)
if !NEEDKEY! equ 1 (
  set /p KEY=Paste the class API key, then press Enter: 
  if "!KEY!"=="" (
    echo No key entered. Open .env, set CLASS_API_KEY=..., then run this again.
    pause
    exit /b 1
  )
  powershell -NoProfile -Command "$k = $env:KEY; (Get-Content .env) | ForEach-Object { if ($_ -match '^CLASS_API_KEY=') { 'CLASS_API_KEY=' + $k } else { $_ } } | Set-Content .env"
  echo Saved the key in .env.
)
if not exist class-endpoints.txt (
  findstr /c:"example.com" .env >nul
  if !errorlevel! equ 0 (
    echo NOTE: there is no class-endpoints.txt here and the URLs in .env are still placeholders.
    echo       Copy class-endpoints.txt into %cd%, or fill in the four *_BASE_URL lines in .env.
  )
)

rem 2. Install and start.
set OPEN_BROWSER=1
where uv >nul 2>nul
if !errorlevel! equ 0 (
  echo Installing dependencies with uv ^(first run takes a minute^)...
  uv run --no-dev ai-jukebox
  goto :end
)
where py >nul 2>nul
if !errorlevel! equ 0 (set PY=py -3) else (set PY=python)
!PY! -c "import sys; sys.exit(sys.version_info < (3, 12))" 2>nul
if !errorlevel! neq 0 (
  echo Python 3.12 or newer is needed ^(or install uv: https://docs.astral.sh/uv/^).
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe (
  echo Creating a virtual environment in .venv ...
  !PY! -m venv .venv
)
echo Installing dependencies with pip ^(first run takes a minute^)...
.venv\Scripts\python -m pip install -q --disable-pip-version-check -r requirements.txt
.venv\Scripts\python -m jukebox
:end
pause
