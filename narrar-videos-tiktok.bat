@echo off
REM Coloca narracao nos videos prontos do TikTok (pasta 04-tiktok). Saem em 04-tiktok\narrados.
cd /d "%~dp0robo-promocoes"
python -m pip install -q -r requirements.txt
python narrar_videos.py
pause
