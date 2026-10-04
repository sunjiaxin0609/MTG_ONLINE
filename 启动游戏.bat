@echo off
setlocal
cd /d "%~dp0"

set "PY="

REM --- 1) known install locations -------------------------------------------
for %%P in (
  "%LOCALAPPDATA%\Python\bin\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
  "C:\Python314\python.exe"
  "C:\Python313\python.exe"
) do (
  if not defined PY (
    if exist %%P (
      %%P -c "import tkinter" >nul 2>&1
      if not errorlevel 1 set "PY=%%P"
    )
  )
)

REM --- 2) whatever is on PATH ------------------------------------------------
if not defined PY (
  where python >nul 2>&1
  if not errorlevel 1 (
    python -c "import tkinter" >nul 2>&1
    if not errorlevel 1 set "PY=python"
  )
)
if not defined PY (
  where python3 >nul 2>&1
  if not errorlevel 1 (
    python3 -c "import tkinter" >nul 2>&1
    if not errorlevel 1 set "PY=python3"
  )
)
if not defined PY (
  where py >nul 2>&1
  if not errorlevel 1 (
    py -3 -c "import tkinter" >nul 2>&1
    if not errorlevel 1 set "PY=py -3"
  )
)

REM --- 3) launch -------------------------------------------------------------
if not defined PY (
  echo.
  echo [ERROR] No Python with tkinter found on this machine.
  echo MTG needs Python 3.10+ built with Tk support.
  echo Reinstall Python from python.org and tick "tcl/tk and IDLE".
  echo.
  pause
  exit /b 1
)

echo Using: %PY%
%PY% main.py

if errorlevel 1 (
  echo.
  echo [ERROR] The game exited with an error. See message above.
  pause
)
endlocal
