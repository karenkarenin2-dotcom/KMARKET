@echo off
rem ---------------------------------------------------------------------
rem  KMARKET launcher.
rem
rem  THIS FILE MUST STAY PURE ASCII WITH CRLF LINE ENDINGS.
rem  cmd.exe parses a .bat using the console code page (cp866 on a Russian
rem  Windows), so UTF-8 text is read as garbage; and "chcp 65001" inside
rem  the file shifts byte offsets mid-parse, which breaks if/else blocks
rem  and leaks their echo lines out as commands. All human-facing text
rem  lives in Python (kmarket/launcher.py), which writes to the console
rem  through the Unicode API and does not care about code pages.
rem
rem  Check before editing (must print 0):
rem    ([IO.File]::ReadAllBytes("KMARKET.bat") ^| ? { $_ -gt 127 }).Count
rem
rem  This file sits in launch/, so we step one level up to the project root.
rem ---------------------------------------------------------------------
title KMARKET
cd /d "%~dp0.."

python -m kmarket %*
set EXITCODE=%ERRORLEVEL%

if not "%EXITCODE%"=="0" (
    echo.
    echo   Exit code %EXITCODE%.
    echo.
    pause
)
exit /b %EXITCODE%
