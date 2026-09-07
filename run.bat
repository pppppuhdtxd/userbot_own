@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Userbot Control Menu

rem ────────────────────────────────────────────────────────────────
rem  Interactive launcher menu for the userbot project.
rem  - Option 1  -> python main.py         (run the bot)
rem  - Option 2  -> python add_account.py  (account management)
rem  - Option 3  -> exit
rem  While a task runs, press Ctrl+C to stop it and return here
rem  (answer "N" to the "Terminate batch job (Y/N)?" prompt).
rem ────────────────────────────────────────────────────────────────

rem Always work from the folder this .bat lives in, so relative
rem imports and config paths resolve correctly no matter how it's launched.
cd /d "%~dp0"

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
echo                  USERBOT  -  CONTROL  MENU
echo  ==============================================================
echo.
echo    [1] Run the bot             ^(main.py^)
echo    [2] Manage accounts         ^(add_account.py^)
echo    [3] Exit
echo.
echo  --------------------------------------------------------------
echo   Tip: while a task is running, press Ctrl+C to stop it and
echo   return to this menu - answer "N" to the "Terminate batch
echo   job?" prompt.
echo  --------------------------------------------------------------
echo.
choice /C 123 /N /M "  Your choice [1-3]: "

if errorlevel 3 goto :exit
if errorlevel 2 goto :accounts
if errorlevel 1 goto :bot
goto :menu

:bot
echo.
echo  ── Starting the bot ^(main.py^) ... press Ctrl+C to stop ──
echo.
python main.py
goto :after_run

:accounts
echo.
echo  ── Starting account manager ^(add_account.py^) ... press Ctrl+C to stop ──
echo.
python add_account.py
goto :after_run

:after_run
echo.
echo  Task finished.
pause
goto :menu

:exit
endlocal
exit /b 0

