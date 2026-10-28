@echo off
chcp 65001 >nul
REM Gera uma amostra de cada voz em portugues e abre uma pagina para escutar.
REM Funciona dentro da pasta robo-promocoes ou na pasta acima dela.
set "AQUI=%~dp0"
if exist "%AQUI%voz.py" goto achou_aqui
if exist "%AQUI%robo-promocoes\voz.py" goto achou_sub
echo Nao achei o voz.py. Coloque este arquivo dentro da pasta robo-promocoes (a do robo v3/v4).
pause
exit /b
:achou_aqui
cd /d "%AQUI%"
goto rodar
:achou_sub
cd /d "%AQUI%robo-promocoes"
:rodar
set "PY=python"
where python >nul 2>nul || set "PY=py"
echo Instalando o pacote da voz (so na primeira vez)...
%PY% -m pip install -q --upgrade edge-tts python-dotenv
echo Gerando as amostras (precisa de internet, leva uns 30 segundos)...
%PY% voz.py --amostras
if errorlevel 1 goto erro
start "" "%CD%\amostras-voz\escutar.html"
pause
exit /b
:erro
echo.
echo Deu erro. Tire um print desta janela e mande no chat.
pause
