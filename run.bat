@echo off
setlocal EnableExtensions
chcp 65001 >nul

rem ────────────────────────────────────────────────────────────────
rem  Interactive launcher menu for the userbot project.
rem  - Option 1  -> python main.py         (run the bot)
rem  - Option 2  -> python add_account.py  (account management)
rem  - Option 3  -> connection settings sub-menu (show / change
rem                 mode / test); every action returns to that
rem                 sub-menu, [4] Back returns to the main menu
rem  - Option 4  -> exit
rem  While a task runs, press Ctrl+C to stop it and return to the
rem  menu you launched it from (answer "N" to the "Terminate batch
rem  job (Y/N)?" prompt).
rem ────────────────────────────────────────────────────────────────

rem Always work from the folder this .bat lives in, so relative
rem imports and config paths resolve correctly no matter how it's launched.
cd /d "%~dp0"

rem Prevent __pycache__ directories from being created during execution
set PYTHONDONTWRITEBYTECODE=1

rem Prevent __pycache__ directories from being created during execution
set PYTHONDONTWRITEBYTECODE=1

rem Current version, read from the VERSION file — every future
rem release updates this display automatically, nothing to edit here.
set "APP_VERSION=unknown"
if exist VERSION set /p APP_VERSION=<VERSION
title Userbot Control Menu v%APP_VERSION%

rem Sanity check: make sure Python is available before showing the menu.
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found in PATH. Install Python 3.11+ first.
    pause
    exit /b 1
)

:menu
cls
echo  ==============================================================
echo           USERBOT  -  CONTROL  MENU  (v%APP_VERSION%)
echo  ==============================================================
echo.
echo    [1] Run the bot             (main.py)
echo    [2] Manage accounts         (add_account.py)
echo    [3] Connection settings      (show / change mode / test)
echo    [4] Exit
echo.
echo  --------------------------------------------------------------
echo   Tip: while a task is running, press Ctrl+C to stop it and
echo   return to the menu you launched it from - answer "N" to the
echo   "Terminate batch job?" prompt.
echo  --------------------------------------------------------------
echo.
choice /C 1234 /N /M "  Your choice [1-4]: "

if errorlevel 4 goto :exit
if errorlevel 3 goto :settings
if errorlevel 2 goto :accounts
if errorlevel 1 goto :bot
goto :menu

:bot
echo.
echo  ── Starting the bot (main.py) ... press Ctrl+C to stop ──
echo.
python main.py
goto :after_run

:accounts
echo.
echo  ── Starting account manager (add_account.py) ... press Ctrl+C to stop ──
echo.
python add_account.py
goto :after_run

rem ── Connection settings ─────────────────────────────────────────
rem  Self-contained sub-menu: every action comes back HERE, so
rem  several settings can be checked/changed in a row. [4] Back is
rem  the only way out to the main menu.

:settings
echo.
echo  ==============================================================
echo                     CONNECTION SETTINGS
echo  ==============================================================
echo.
echo    [1] Show current settings
echo    [2] Change connection mode  (direct / SOCKS5 / Cloudflare)
echo    [3] Test connection
echo    [4] Back to main menu
echo.
choice /C 1234 /N /M "  Your choice [1-4]: "

if errorlevel 4 goto :menu
if errorlevel 3 goto :settings_test
if errorlevel 2 goto :settings_mode
goto :settings_show

:settings_show
python settings.py show
goto :settings

rem  The mode choices live one level deeper, selected from their
rem  own menu — same layout as the interactive settings.py menu.

:settings_mode
echo.
echo  --------------------------------------------------------------
echo   Choose the connection mode:
echo  --------------------------------------------------------------
echo.
echo    [1] Direct connection (no proxy)
echo    [2] SOCKS5 proxy (will prompt for details)
echo    [3] Cloudflare - without Worker (official endpoints)
echo    [4] Cloudflare - with Worker (will prompt for domain)
echo    [5] Back to Connection settings
echo.
choice /C 12345 /N /M "  Your choice [1-5]: "

if errorlevel 5 goto :settings
if errorlevel 4 goto :settings_cf_worker
if errorlevel 3 goto :settings_cf_noworker
if errorlevel 2 goto :settings_socks5
goto :settings_direct

:settings_direct
python settings.py set-direct
goto :settings

:settings_socks5
python settings.py set-socks5
goto :settings

:settings_cf_noworker
python settings.py set-cf-no-worker
goto :settings

:settings_cf_worker
python settings.py set-cf-worker
goto :settings

:settings_test
python settings.py test
goto :settings

:after_run
echo.
echo  Task finished.
pause
goto :menu

:exit
endlocal
exit /b 0
