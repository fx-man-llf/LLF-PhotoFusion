@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\LLF PhotoFusion.lnk');" ^
  "$s.TargetPath='%~dp0Lancer LLF PhotoFusion (Windows).bat';" ^
  "$s.WorkingDirectory='%~dp0';" ^
  "$s.IconLocation='%~dp0photofusion\web\icone.ico';" ^
  "$s.WindowStyle=7;" ^
  "$s.Description='LLF PhotoFusion - logiciel gratuit - www.lalogitheque.fr';" ^
  "$s.Save()"
if errorlevel 1 ( echo Impossible de creer le raccourci. ) else ( echo Raccourci "LLF PhotoFusion" cree sur le Bureau. )
pause
