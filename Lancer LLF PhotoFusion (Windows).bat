@echo off
setlocal EnableExtensions
title LLF PhotoFusion
cd /d "%~dp0"

rem ============================================================
rem  LLF PhotoFusion - lanceur Windows
rem  Logiciel gratuit, utilisable par tous - www.lalogitheque.fr
rem ============================================================

rem ---- Environnement deja pret ?
if not exist ".venv\Scripts\python.exe" goto prepare
".venv\Scripts\python.exe" -c "import numpy, cv2, PIL, tifffile, scipy" >nul 2>nul
if errorlevel 1 goto install_modules
goto run

:prepare
echo.
echo  Premier lancement : preparation de LLF PhotoFusion.
echo  Cela peut prendre quelques minutes (connexion Internet necessaire).
echo.
call :findpy
if defined PY goto make_venv

echo  Python n'est pas installe sur cet ordinateur.
echo  Installation automatique de Python 3.12 (gratuit, site officiel)...
echo.
where winget >nul 2>nul
if errorlevel 1 goto download_python
winget install -e --id Python.Python.3.12 --scope user --silent --accept-source-agreements --accept-package-agreements
call :findpy
if defined PY goto make_venv

:download_python
echo  Telechargement de Python depuis python.org...
set "PYINST=%TEMP%\python-3.12-installer.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile $env:PYINST"
if not exist "%PYINST%" goto no_python
echo  Installation de Python (environ 1 minute)...
"%PYINST%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0
del "%PYINST%" >nul 2>nul
call :findpy
if defined PY goto make_venv

:no_python
echo.
echo  ------------------------------------------------------------
echo  Impossible d'installer Python automatiquement.
echo  1. Allez sur https://www.python.org/downloads/
echo  2. Telechargez et lancez l'installation de Python
echo  3. IMPORTANT : cochez la case "Add python.exe to PATH"
echo  4. Relancez ce fichier.
echo  ------------------------------------------------------------
pause
exit /b 1

:make_venv
echo  Python utilise : %PY%
if exist ".venv" rmdir /s /q ".venv"
"%PY%" -m venv .venv
if not exist ".venv\Scripts\python.exe" (
  echo  Echec de creation de l'environnement Python.
  pause
  exit /b 1
)

:install_modules
echo.
echo  Installation des modules de traitement d'image...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo  Certains modules optionnels n'ont pas pu etre installes, nouvel essai sans eux...
  ".venv\Scripts\python.exe" -m pip install numpy opencv-python-headless Pillow tifffile scipy
)
".venv\Scripts\python.exe" -c "import numpy, cv2, PIL, tifffile, scipy" >nul 2>nul
if errorlevel 1 (
  echo.
  echo  L'installation des modules a echoue. Verifiez la connexion Internet
  echo  puis relancez ce fichier.
  pause
  exit /b 1
)
echo  Installation terminee.

:run
".venv\Scripts\python.exe" PhotoFusion.py
if errorlevel 1 pause
exit /b 0


rem ============================================================
rem  Recherche d'un VRAI Python (le raccourci "python" du
rem  Microsoft Store present par defaut sur Windows 11 est ignore)
rem ============================================================
:findpy
set "PY="
set "CAND="
for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "CAND=%%i"
call :testpy "%CAND%"
for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :testpy "%%d\python.exe"
for /d %%d in ("%ProgramFiles%\Python3*") do call :testpy "%%d\python.exe"
for /f "delims=" %%i in ('where python 2^>nul') do call :testpy "%%i"
exit /b 0

:testpy
if defined PY exit /b 0
if "%~1"=="" exit /b 0
if not exist "%~1" exit /b 0
"%~1" -c "import sys, venv; sys.exit(0 if sys.version_info >= (3, 9) and sys.maxsize > 2**32 else 1)" >nul 2>nul
if errorlevel 1 exit /b 0
set "PY=%~1"
exit /b 0
