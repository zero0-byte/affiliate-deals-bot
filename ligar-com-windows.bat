@echo off
REM Clique duas vezes UMA VEZ: o robo passa a ligar sozinho quando o Windows iniciar (minimizado).
REM Para desfazer: aperte Windows+R, digite shell:startup, Enter, e apague "robo-promocoes.bat".
set "PASTA_INICIAR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "ROBO=%~dp0ligar-robo.bat"
if not exist "%ROBO%" (
  echo Nao achei o ligar-robo.bat nesta pasta. Coloque este arquivo junto dele.
  pause
  exit /b
)
(
  echo @echo off
  echo start "Robo de Promocoes" /min cmd /c "%ROBO%"
) > "%PASTA_INICIAR%\robo-promocoes.bat"
echo Pronto! O robo vai ligar sozinho quando o Windows iniciar.
echo Dica: em Configuracoes ^> Energia, deixe o PC sem "suspender" para o robo nao parar.
pause
