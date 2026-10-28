@echo off
REM Conecta o robo a sua conta do TikTok (uma vez so). Siga o GUIA-TIKTOK.md antes.
cd /d "%~dp0robo-promocoes"
python -m pip install -q -r requirements.txt
python -m fontes.tiktok
pause
