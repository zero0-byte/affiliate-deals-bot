"""Narração dos vídeos: texto -> voz natural em português, e mixagem da voz com a batida.

Motores (VOZ_MOTOR no .env):
- edge: vozes neurais da Microsoft (as mesmas do Edge "Ler em voz alta"). Grátis, precisa de internet.
- elevenlabs: a mais natural de todas, paga (precisa de ELEVENLABS_API_KEY e ELEVENLABS_VOZ_ID).
- nenhum: vídeo só com a batida, como antes.

Se a voz falhar (sem internet, por exemplo), o vídeo sai só com a batida: nunca trava o robô.

Rode `python voz.py --amostras` (ou o ouvir-vozes.bat) para gerar uma amostra de cada voz.
"""
import asyncio
import logging
import re
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

import config

log = logging.getLogger(__name__)
SR = 44100

# palavras que a voz lê errado: escreve do jeito que se fala
PRONUNCIA = {
    r"\bRGB\b": "erre gê bê", r"\bFPS\b": "efe pê esse", r"\bSSD\b": "esse esse dê", r"\bPC\b": "pê cê",
    r"\bPOV\b": "pi ou vi", r"\bHz\b": "hertz", r"\bUSB\b": "u esse bê", r"\bnvme\b": "",
    r"\bkkk+\b": "", r"\s+x\s+": " versus ",
}

_UNID = ["zero", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez", "onze", "doze",
         "treze", "catorze", "quinze", "dezesseis", "dezessete", "dezoito", "dezenove"]
_DEZ = ["", "", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta", "oitenta", "noventa"]
_CEM = ["", "cento", "duzentos", "trezentos", "quatrocentos", "quinhentos", "seiscentos", "setecentos",
        "oitocentos", "novecentos"]


def por_extenso(n):
    """Número inteiro (até 999.999) por extenso, do jeito que se fala."""
    n = int(n)
    if n < 20:
        return _UNID[n]
    if n < 100:
        d, u = divmod(n, 10)
        return _DEZ[d] + (f" e {_UNID[u]}" if u else "")
    if n < 1000:
        if n == 100:
            return "cem"
        c, r = divmod(n, 100)
        return _CEM[c] + (f" e {por_extenso(r)}" if r else "")
    m, r = divmod(n, 1000)
    mil = "mil" if m == 1 else f"{por_extenso(m)} mil"
    if not r:
        return mil
    return f"{mil} e {por_extenso(r)}" if r < 100 or r % 100 == 0 else f"{mil} {por_extenso(r)}"


def reais_falado(valor):
    """1299.9 -> 'mil duzentos e noventa e nove e noventa'; 50 -> 'cinquenta reais'."""
    centavos = round(float(valor) * 100)
    inteiro, cent = divmod(centavos, 100)
    if not cent:
        return f"{por_extenso(inteiro)} {'real' if inteiro == 1 else 'reais'}"
    return f"{por_extenso(inteiro)} e {por_extenso(cent)}"


def preparar(texto):
    """Ajusta o texto para a voz: pronúncia, preços e porcentagens por extenso."""
    t = re.sub(r"R\$\s?([\d.]+)(?:,(\d{2}))?",
               lambda m: reais_falado(float(m.group(1).replace(".", "") + "." + (m.group(2) or "0"))), texto)
    t = re.sub(r"(\d+)\s?%", lambda m: f"{por_extenso(m.group(1))} por cento", t)
    for padrao, troca in PRONUNCIA.items():
        t = re.sub(padrao, troca, t, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", t).strip()


def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 — sem o pacote, tenta o ffmpeg do sistema
        return "ffmpeg"


def _ler_audio(caminho):
    """Qualquer áudio -> mono 44,1 kHz em float, com limpeza de voz (tira grave embolado, comprime leve)."""
    r = subprocess.run([_ffmpeg(), "-loglevel", "error", "-i", str(caminho), "-ac", "1", "-ar", str(SR),
                        "-af", "highpass=f=75,acompressor=threshold=-20dB:ratio=2.5:attack=8:release=150,"
                               "silenceremove=start_periods=1:start_threshold=-50dB,areverse,"
                               "silenceremove=start_periods=1:start_threshold=-50dB,areverse",
                        "-f", "f32le", "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.float32).copy()


async def _edge(texto, saida, velocidade, tom, voz=None):
    import edge_tts
    com = edge_tts.Communicate(texto, voz or config.VOZ, rate=f"{velocidade:+d}%", pitch=f"{tom:+d}Hz")
    await com.save(str(saida))


def _elevenlabs(texto, saida, velocidade):
    import httpx
    r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{config.ELEVENLABS_VOZ_ID}",
                   headers={"xi-api-key": config.ELEVENLABS_API_KEY},
                   json={"text": texto, "model_id": "eleven_multilingual_v2",
                         "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.35,
                                            "speed": max(0.8, min(1.2, 1 + velocidade / 100))}},
                   timeout=60)
    r.raise_for_status()
    Path(saida).write_bytes(r.content)


def sintetizar(texto, velocidade=None, voz=None):
    """Texto -> áudio (numpy float32, mono, 44,1 kHz). Levanta erro se não conseguir."""
    velocidade = config.VOZ_VELOCIDADE if velocidade is None else velocidade
    texto = preparar(texto)
    with tempfile.TemporaryDirectory() as tmp:
        arq = Path(tmp) / "voz.mp3"
        if config.VOZ_MOTOR == "elevenlabs" and config.ELEVENLABS_API_KEY and config.ELEVENLABS_VOZ_ID:
            _elevenlabs(texto, arq, velocidade)
        else:
            asyncio.run(_edge(texto, arq, velocidade, config.VOZ_TOM, voz))
        return _ler_audio(arq)


def ligada():
    return config.VOZ_MOTOR in ("edge", "elevenlabs")


def fala_que_cabe(texto, espaco):
    """Gera a fala; se passar do espaço, gera de novo um pouco mais rápida (até +30%), sem distorcer a voz."""
    a = sintetizar(texto)
    dur = len(a) / SR
    if dur > espaco:
        extra = min(30, int((dur / espaco - 1) * 100) + 4)
        a = sintetizar(texto, velocidade=config.VOZ_VELOCIDADE + extra)
    return a


def montar_trilha_de_voz(falas, duracao):
    """falas: lista de (início em s, áudio). Devolve um áudio só, do tamanho do vídeo."""
    pista = np.zeros(int(SR * duracao) + 1, np.float32)
    for ini, a in falas:
        i = int(ini * SR)
        j = min(len(pista), i + len(a))
        if j > i:
            pista[i:j] += a[: j - i]
    return pista


def salvar_wav(caminho, audio):
    with wave.open(str(caminho), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())


def filtro_mix(musica=None):
    """Filtro do ffmpeg: entrada 0 = música, entrada 1 = voz. A música abaixa sozinha quando a voz fala
    (ducking), e o volume final fica no padrão das redes (-14 LUFS)."""
    vol = (config.VOZ_MUSICA if musica is None else musica) / 100
    return (f"[0:a]aformat=sample_rates={SR}:channel_layouts=stereo,volume={vol:.2f}[m];"
            f"[1:a]aformat=sample_rates={SR}:channel_layouts=stereo,"
            "equalizer=f=3000:t=q:w=1:g=2,volume=1.0,asplit=2[v][sc];"
            "[m][sc]sidechaincompress=threshold=0.02:ratio=6:attack=15:release=400:makeup=1[duck];"
            "[duck][v]amix=inputs=2:duration=first:normalize=0,"
            "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]")


def mixar(musica_wav, voz_wav, saida_wav):
    subprocess.run([_ffmpeg(), "-loglevel", "error", "-y", "-i", str(musica_wav), "-i", str(voz_wav),
                    "-filter_complex", filtro_mix(), "-map", "[a]", str(saida_wav)], check=True)


# ------------------------------------------------------------------ amostras

AMOSTRA = ("Achei isso por trinta e sete e noventa! Mouse gamer com sessenta por cento de desconto. "
           "Corre que acaba rápido: segue o canal no WhatsApp, link na bio!")


VOZES_PADRAO = ["pt-BR-AntonioNeural", "pt-BR-FranciscaNeural", "pt-BR-ThalitaMultilingualNeural",
                "en-US-AndrewMultilingualNeural", "en-US-AvaMultilingualNeural", "en-US-BrianMultilingualNeural",
                "en-US-EmmaMultilingualNeural"]


async def _vozes_pt():
    import edge_tts
    try:
        vozes = await edge_tts.list_voices()
    except Exception:  # noqa: BLE001 — sem a lista, usa as conhecidas
        return VOZES_PADRAO
    pt = [v["ShortName"] for v in vozes if v["Locale"] == "pt-BR"]
    multi = [v["ShortName"] for v in vozes if "Multilingual" in v["ShortName"] and v["Locale"].startswith("en-US")]
    return pt + multi


def amostras(pasta):
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        raise SystemExit("Falta o pacote da voz. Rode no Prompt de Comando:  python -m pip install edge-tts")
    pasta = Path(pasta)
    pasta.mkdir(exist_ok=True)
    texto = preparar(AMOSTRA)
    feitos, erros = [], []
    for nome in asyncio.run(_vozes_pt()):
        for vel in (0, 10):
            arq = pasta / f"{nome}{'-rapida' if vel else ''}.mp3"
            try:
                asyncio.run(_edge(texto, arq, vel, 0, nome))
                feitos.append(arq.name)
                print("ok:", arq.name, flush=True)
            except Exception as e:  # noqa: BLE001
                erros.append(f"{nome}: {e}")
                print("falhou:", nome, e, flush=True)
    if not feitos:
        raise SystemExit("Nenhuma amostra foi gerada. Confira a internet e mande um print desta janela.\n"
                         + "\n".join(erros[:3]))
    # página simples pra escutar tudo num lugar só
    itens = "".join(f"<p><b>{Path(f).stem}</b><br><audio controls preload='none' src='{f}'></audio></p>"
                    for f in feitos)
    (pasta / "escutar.html").write_text(
        "<meta charset='utf-8'><title>Vozes</title><body style='font-family:sans-serif;max-width:640px;margin:24px'>"
        "<h2>Amostras de voz</h2><p>Gostou de uma? Coloque no .env: <code>VOZ=nome</code> "
        "(sem '-rapida'). Se preferiu a rápida, acrescente <code>VOZ_VELOCIDADE=10</code>.</p>" + itens,
        encoding="utf-8")
    print(f"\nPronto! {len(feitos)} amostras em {pasta}. Abrindo a página para escutar...")


if __name__ == "__main__":
    if "--amostras" in sys.argv:
        amostras(Path(__file__).resolve().parent / "amostras-voz")
    else:
        print(preparar(" ".join(sys.argv[1:]) or AMOSTRA))
