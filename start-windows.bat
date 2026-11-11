@echo off
rem CAPCAST launcher (Windows). Runs the local server from this folder.
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 goto run_py
where python >nul 2>nul
if not errorlevel 1 goto run_python
echo.
echo Python 3 is required ^(3.11 or newer recommended^). Install it from https://www.python.org/downloads/
echo During installation, tick "Add Python to PATH".
pause
exit /b 9009

:run_py
py -3 -I server.py %*
goto done

:run_python
python -I server.py %*
goto done

:done
set "CAPCAST_EXIT=%errorlevel%"
if "%CAPCAST_EXIT%"=="9009" (
    echo.
    echo Python does not seem to be installed. Install Python 3.11 or newer from https://www.python.org/downloads/
    pause
    exit /b 9009
)
if not "%CAPCAST_EXIT%"=="0" (
    echo.
    echo CAPCAST stopped with an error ^(code %CAPCAST_EXIT%^). Read the message above.
)
pause
exit /b %CAPCAST_EXIT%
