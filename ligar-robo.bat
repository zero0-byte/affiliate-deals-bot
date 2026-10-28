@echo off
REM Clique duas vezes para ligar o robo. Funciona dentro da pasta robo-promocoes ou na pasta de cima.
REM Se o robo cair (erro, internet, travou), ele religa sozinho em 30 segundos.
REM Para desligar de vez: feche esta janela.
title Robo de Promocoes
if exist "%~dp0bot.py" (cd /d "%~dp0") else (cd /d "%~dp0robo-promocoes")
python -m pip install -q -r requirements.txt
:ligar
echo.
echo [%date% %time%] Ligando o robo...
python bot.py
echo.
echo [%date% %time%] O robo parou. Religando em 30 segundos (feche a janela para desligar de vez)...
timeout /t 30 /nobreak >nul
goto ligar
