@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHONDONTWRITEBYTECODE=1"
set "APP_PY=.venv\Scripts\python.exe"

if not exist "%APP_PY%" (
  where py >nul 2>nul
  if errorlevel 1 goto :use_python
  py -3 -m venv .venv
  if errorlevel 1 goto :setup_error
)
goto :install_dependencies

:use_python
where python >nul 2>nul
if errorlevel 1 goto :no_python
python -m venv .venv
if errorlevel 1 goto :setup_error

:install_dependencies
"%APP_PY%" -c "import bs4, dotenv, requests, yaml" >nul 2>nul
if errorlevel 1 (
  "%APP_PY%" -m pip install -r requirements.txt
  if errorlevel 1 goto :setup_error
)

set "PYTHONPATH=%CD%\src"
"%APP_PY%" -B -m knowledge_pipeline --workspace ".knowledge-workspace" serve --source-root "." --open-browser
exit /b %errorlevel%

:no_python
echo Python 3.11 or newer is required.
pause
exit /b 1

:setup_error
echo Setup failed. Run: .venv\Scripts\python.exe -m pip install -r requirements.txt
pause
exit /b 1
