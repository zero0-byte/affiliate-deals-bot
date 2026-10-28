@echo off
REM Clique duas vezes UMA VEZ: cria o atalho "Painel de Ofertas" na Area de Trabalho, com o logo.
REM Depois e so abrir o painel pelo atalho (pode fixar na barra de tarefas tambem).
cd /d "%~dp0"
if not exist "%~dp0painel.pyw" (
  echo Coloque este arquivo dentro da pasta robo-promocoes, junto do painel.pyw.
  pause
  exit /b
)
set "PYW="
for /f "delims=" %%i in ('python -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>nul') do set "PYW=%%i"
if not exist "%PYW%" for /f "delims=" %%i in ('py -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>nul') do set "PYW=%%i"
if not exist "%PYW%" (
  echo Nao achei o Python neste PC. Ele e o mesmo que roda o robo; confira se o robo liga pelo ligar-robo.bat.
  pause
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Painel de Ofertas.lnk'); $s.TargetPath='%PYW%'; $s.Arguments='\"%~dp0painel.pyw\"'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0arte\painel.ico'; $s.Description='Painel do robo de promocoes'; $s.Save()"
if errorlevel 1 (
  echo Nao consegui criar o atalho. Voce ainda pode abrir clicando duas vezes no painel.pyw.
) else (
  echo Pronto! O atalho "Painel de Ofertas" esta na sua Area de Trabalho.
)
pause
