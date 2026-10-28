"""Vídeo vertical "N achados de hoje" (Reels/Stories, 1080x1920) a partir das ofertas postadas."""
import logging
import math
import os
import re
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import config
from criativo import EMOJI, _arroba, _foto_produto, _quebrar
import voz
from formatar import reais

log = logging.getLogger(__name__)

W, H, FPS = 1080, 1920, 30
AMARELO = (255, 208, 0)
PRETO = (23, 23, 26)
BRANCO = (255, 255, 255)
CINZA = (92, 92, 102)
PASTA_ARTE = Path(__file__).resolve().parent / "arte"

POR_OFERTA, FINAL = 4.0, 3.3


# ------------------------------------------------------------------ utilidades

def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 — sem o pacote, tenta o ffmpeg do sistema
        return "ffmpeg"


@lru_cache(None)
def _fonte(peso, tam):
    try:
        return ImageFont.truetype(str(PASTA_ARTE / f"Inter-{peso}.otf"), tam)
    except OSError:
        return ImageFont.load_default(tam)


def _clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def _prog(t, ini, dur):
    return _clamp((t - ini) / dur)


def _out_cubic(x):
    return 1 - (1 - x) ** 3


def _in_cubic(x):
    return x ** 3


def _out_back(x, s=1.9):
    x -= 1
    return 1 + (s + 1) * x ** 3 + s * x ** 2


@lru_cache(None)
def _texto(txt, peso, tam, cor):
    f = _fonte(peso, tam)
    l, t, r, b = f.getbbox(txt)
    img = Image.new("RGBA", (r - l + 20, b - t + 20), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((10 - l, 10 - t), txt, font=f, fill=cor)
    return img


@lru_cache(None)
def _pilula(txt, tam, fundo, cor, px=56, py=28):
    f = _fonte("Black", tam)
    l, t, r, b = f.getbbox(txt)
    w, h = r - l + 2 * px, b - t + 2 * py
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 2, fill=fundo)
    d.text((px - l, py - t), txt, font=f, fill=cor)
    return img


@lru_cache(None)
def _logo(tam):
    try:
        lg = Image.open(PASTA_ARTE / "logo.png").convert("RGBA").resize((tam, tam), Image.LANCZOS)
    except OSError:
        return _pilula("AB", tam // 3, PRETO, AMARELO)
    m = Image.new("L", (tam, tam), 0)
    ImageDraw.Draw(m).ellipse([0, 0, tam - 1, tam - 1], fill=255)
    lg.putalpha(m)
    borda = Image.new("RGBA", (tam + 24, tam + 24), (0, 0, 0, 0))
    ImageDraw.Draw(borda).ellipse([0, 0, tam + 23, tam + 23], fill=PRETO)
    borda.alpha_composite(lg, (12, 12))
    return borda


def _colar(base, img, cx, cy, escala=1.0, alfa=1.0, rot=0.0):
    if alfa <= 0 or escala <= 0.01:
        return
    if escala != 1.0:
        img = img.resize((max(1, int(img.width * escala)), max(1, int(img.height * escala))), Image.BILINEAR)
    if rot:
        img = img.rotate(rot, resample=Image.BICUBIC, expand=True)
    if alfa < 1.0:
        img = img.copy()
        img.putalpha(img.getchannel("A").point(lambda a: int(a * alfa)))
    base.alpha_composite(img, (int(cx - img.width / 2), int(cy - img.height / 2)))


def _pop(base, img, cx, cy, t, ini, dur=0.45, de=0.4):
    p = _prog(t, ini, dur)
    if p > 0:
        _colar(base, img, cx, cy, escala=de + (1 - de) * _out_back(p), alfa=_clamp(p * 2.5))


def _wipe(base, t, ini, dur, cor):
    p = _prog(t, ini, dur)
    if p > 0:
        r = _out_cubic(p) * math.hypot(W, H) / 2 * 1.05
        ImageDraw.Draw(base).ellipse([W / 2 - r, H / 2 - r, W / 2 + r, H / 2 + r], fill=cor)


# ------------------------------------------------------------------ cartão da oferta

class _Cartao:
    """Pré-desenha as partes fixas de uma oferta; o movimento é aplicado por quadro."""

    LARG = W - 160
    PAD = 40
    FOTO_H = 640

    def __init__(self, dados, imagem_bytes, numero, total):
        self.d = dados
        self.dur = POR_OFERTA  # aumenta sozinho se a narração for mais longa
        self.rotulo = _pilula(f"ACHADO {numero}/{total}", 46, PRETO, AMARELO, 40, 22)
        interno = self.LARG - 2 * self.PAD
        foto = _foto_produto(imagem_bytes, interno, self.FOTO_H).convert("RGBA")
        m = Image.new("L", foto.size, 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, foto.width - 1, foto.height - 1], radius=28, fill=255)
        foto.putalpha(m)
        self.foto = foto
        f_tit = _fonte("Bold", 50)
        titulo = EMOJI.sub("", dados.get("titulo") or "Oferta").strip(" -–|") or "Oferta"
        self.linhas = _quebrar(titulo, f_tit, interno, 2)
        self.f_tit = f_tit
        orig = dados.get("preco_original")
        self.de = f"De {reais(orig)}" if orig and orig > dados["preco"] else None
        self.preco = reais(dados["preco"])
        self.desc = int(dados.get("desconto") or 0)
        self.loja = dados.get("fonte") or ""
        self.altura = (self.PAD + self.FOTO_H + 30 + 62 * len(self.linhas) + 16
                       + (56 if self.de else 0) + 124 + (44 if self.loja else 0) + self.PAD)

    def desenhar(self, base, t):
        # entrada (sobe) e saída (vai pra esquerda)
        entra = _out_cubic(_prog(t, 0.0, 0.45))
        sai = _in_cubic(_prog(t, self.dur - 0.3, 0.3))
        dx = -1200 * sai
        y0 = 440 + (1 - entra) * 1400
        x0 = 80 + dx

        _pop(base, self.rotulo, W / 2 + dx, 330, t, 0.05)

        camada = Image.new("RGBA", (self.LARG, self.altura), (0, 0, 0, 0))
        d = ImageDraw.Draw(camada)
        d.rounded_rectangle([0, 0, self.LARG - 1, self.altura - 1], radius=44, fill=BRANCO)

        # foto com zoom lento
        z = 1 + 0.06 * _prog(t, 0.0, self.dur)
        fw, fh = int(self.foto.width * z), int(self.foto.height * z)
        foto = self.foto.resize((fw, fh), Image.BILINEAR).crop(
            ((fw - self.foto.width) // 2, (fh - self.foto.height) // 2,
             (fw - self.foto.width) // 2 + self.foto.width, (fh - self.foto.height) // 2 + self.foto.height))
        camada.alpha_composite(foto, (self.PAD, self.PAD))

        y = self.PAD + self.FOTO_H + 30
        a_tit = _clamp(_prog(t, 0.35, 0.3))
        for linha in self.linhas:
            d.text((self.PAD, y), linha, font=self.f_tit, fill=PRETO + (int(255 * a_tit),))
            y += 62
        y += 16

        if self.de:
            f_de = _fonte("SemiBold", 42)
            a = int(255 * _prog(t, 0.55, 0.25))
            d.text((self.PAD, y), self.de, font=f_de, fill=CINZA + (a,))
            p = _out_cubic(_prog(t, 0.85, 0.3))
            if p > 0:
                larg = f_de.getlength(self.de)
                d.line([(self.PAD - 6, y + 27), (self.PAD - 6 + (larg + 12) * p, y + 27)], fill=PRETO, width=7)
            y += 56

        base.alpha_composite(camada, (int(x0), int(y0)))

        # preço "batendo" e selo de desconto (desenhados por cima, com escala)
        yp = y0 + y + 62
        pp = _prog(t, 1.15, 0.35)
        if pp > 0:
            img_preco = _texto(self.preco, "Black", 112, PRETO)
            esc = 1.5 - 0.5 * _out_cubic(pp)
            cx = x0 + self.PAD + img_preco.width / 2
            _colar(base, img_preco, cx, yp, escala=esc, alfa=_clamp(pp * 3))
            if self.desc:
                selo = _pilula(f"{self.desc}% OFF", 44, PRETO, AMARELO, 26, 16)
                sx = x0 + self.PAD + img_preco.width + 24 + selo.width / 2
                if sx + selo.width / 2 <= x0 + self.LARG - self.PAD:
                    pb = _prog(t, 1.5, 0.4)
                    if pb > 0:
                        _colar(base, selo, sx, yp, escala=0.3 + 0.7 * _out_back(pb, 2.6), alfa=_clamp(pb * 3),
                               rot=(1 - _out_cubic(pb)) * 25)
        if self.loja:
            a = _prog(t, 1.7, 0.3)
            if a > 0:
                loja = _texto(f"na {self.loja}", "SemiBold", 38, CINZA)
                _colar(base, loja, x0 + self.PAD + loja.width / 2 - 10, y0 + y + 124 + 22, alfa=a)


# ------------------------------------------------------------------ gancho (primeiros segundos)

@lru_cache(None)
def _caixa_preco(txt):
    """Preço grande em caixa preta, levemente inclinada."""
    f = _fonte("Black", 150)
    l, t, r, b = f.getbbox(txt)
    w, h = r - l + 100, b - t + 64
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=36, fill=PRETO)
    d.text((50 - l, 32 - t), txt, font=f, fill=AMARELO)
    return img.rotate(-3, resample=Image.BICUBIC, expand=True)


@lru_cache(None)
def _selo_redondo(txt):
    tam = 250
    img = Image.new("RGBA", (tam, tam), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 0, tam - 1, tam - 1], fill=PRETO)
    d.ellipse([10, 10, tam - 11, tam - 11], fill=AMARELO)
    d.text((tam / 2, tam / 2 - 18), txt, font=_fonte("Black", 76), fill=PRETO, anchor="mm")
    d.text((tam / 2, tam / 2 + 46), "OFF", font=_fonte("Black", 44), fill=PRETO, anchor="mm")
    return img


class _Gancho:
    """Abertura: a melhor oferta já no primeiro quadro, com o preço batendo na tela."""

    DUR = 3.3
    FOTO = 660

    def __init__(self, dados, imagem_bytes):
        foto = _foto_produto(imagem_bytes, self.FOTO, self.FOTO).convert("RGBA")
        cartao = Image.new("RGBA", (self.FOTO + 40, self.FOTO + 40), (0, 0, 0, 0))
        ImageDraw.Draw(cartao).rounded_rectangle([0, 0, cartao.width - 1, cartao.height - 1], radius=48, fill=BRANCO)
        m = Image.new("L", foto.size, 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, foto.width - 1, foto.height - 1], radius=32, fill=255)
        foto.putalpha(m)
        cartao.alpha_composite(foto, (20, 20))
        self.cartao = cartao
        self.dur = self.DUR
        self.titulo = _texto("ACHEI ISSO POR", "Black", 96, PRETO)
        self.preco = _caixa_preco(reais(dados["preco"]))
        orig = dados.get("preco_original")
        self.era = _texto(f"era {reais(orig)}", "Bold", 58, PRETO) if orig and orig > dados["preco"] else None
        desc = int(dados.get("desconto") or 0)
        self.selo = _selo_redondo(f"-{desc}%") if desc else None
        self.mais = _pilula("E TEM MAIS NO CANAL", 52, PRETO, AMARELO, 44, 24)

    def desenhar(self, t):
        img = Image.new("RGBA", (W, H), AMARELO + (255,))
        # tremida da tela quando o preço bate
        tremor = (1 - _prog(t, 0.22, 0.35)) * (t >= 0.22)
        sx = 14 * tremor * math.sin(t * 95)
        sy = 10 * tremor * math.cos(t * 83)
        sai = _in_cubic(_prog(t, self.dur - 0.3, 0.3)) * -1200

        # já aparece no 1º quadro (sem tela vazia): só um leve "soco" de zoom
        soco = 1.08 - 0.08 * _out_cubic(_prog(t, 0.0, 0.25))
        _colar(img, self.titulo, W / 2 + sx + sai, 330 + sy, escala=soco)
        zoom = (1.0 + 0.05 * _prog(t, 0.0, self.dur)) * soco
        _colar(img, self.cartao, W / 2 + sx + sai, 790 + sy, escala=zoom)

        if self.selo:
            ps = _prog(t, 0.45, 0.4)
            if ps > 0:
                _colar(img, self.selo, 870 + sx + sai, 520 + sy, escala=0.2 + 0.8 * _out_back(ps, 2.4),
                       alfa=_clamp(ps * 3), rot=12 + (1 - _out_cubic(ps)) * 60)
        if self.era:
            pe = _prog(t, 0.55, 0.25)
            if pe > 0:
                _colar(img, self.era, W / 2 + sx + sai, 1212 + sy, alfa=pe)
                pr = _out_cubic(_prog(t, 0.75, 0.25))
                if pr > 0:
                    larg = self.era.width - 20
                    x0 = W / 2 - larg / 2 + sx + sai
                    ImageDraw.Draw(img).line([(x0 - 8, 1215 + sy), (x0 - 8 + (larg + 16) * pr, 1215 + sy)],
                                             fill=PRETO, width=8)
        pp = _prog(t, -0.06, 0.3)  # já aparece no 1º quadro, batendo
        if pp > 0:
            _colar(img, self.preco, W / 2 + sx + sai, 1385 + sy, escala=1.7 - 0.7 * _out_cubic(pp), alfa=_clamp(pp * 4))
        _pop(img, self.mais, W / 2 + sai, 1565, t, 1.9, de=0.6)
        return img


def _final(t):
    img = Image.new("RGBA", (W, H), AMARELO + (255,))
    p = _prog(t, 0.05, 0.6)
    if p > 0:
        _colar(img, _logo(320), W / 2, 640, escala=0.2 + 0.8 * _out_back(p, 1.4), alfa=_clamp(p * 3),
               rot=(1 - _out_cubic(p)) * -40)
    _pop(img, _texto("TODAS AS OFERTAS", "Black", 92, PRETO), W / 2, 950, t, 0.4, de=0.7)
    _pop(img, _texto("NO CANAL", "Black", 92, PRETO), W / 2, 1055, t, 0.55, de=0.7)
    arroba = "SIGA NO WHATSAPP" if config.WHATS_CANAL else (_arroba() or config.MARCA)
    pb = _prog(t, 0.8, 0.45)
    if pb > 0:
        pulso = 1 + 0.035 * math.sin((t - 1.25) * 2 * math.pi * 1.4) if t > 1.25 else 1
        _colar(img, _pilula(arroba, 84, PRETO, AMARELO, 70, 34), W / 2, 1215,
               escala=(0.5 + 0.5 * _out_back(pb)) * pulso, alfa=_clamp(pb * 3))
    onde = f"{config.WHATS_ARTE} · grátis" if config.WHATS_CANAL else "Grátis no Telegram"
    _pop(img, _texto(onde, "SemiBold", 52, PRETO), W / 2, 1360, t, 1.1, de=0.85)
    pa = _prog(t, 1.4, 0.3)
    if pa > 0:
        y = 1470 + 18 * math.sin((t - 1.4) * 2 * math.pi * 1.6)
        d = ImageDraw.Draw(img)
        a = int(255 * _clamp(pa * 2))
        d.line([(W / 2, y - 40), (W / 2, y + 30)], fill=PRETO + (a,), width=14)
        d.line([(W / 2 - 34, y), (W / 2, y + 36), (W / 2 + 34, y)], fill=PRETO + (a,), width=14, joint="curve")
    return img


# ------------------------------------------------------------------ narração

VOZ_GANCHO, VOZ_CARTAO, VOZ_FINAL = 0.15, 0.4, 0.3  # quando a fala começa em cada cena


def _nome_curto(titulo):
    """'Mouse Gamer Logitech G203 Lightsync RGB, 8000 DPI...' -> 'Mouse Gamer Logitech G203'."""
    t = EMOJI.sub("", titulo or "").strip(" -–|")
    t = re.split(r"\s[-–|/]\s|[,(\[]", t)[0]
    return " ".join(t.split()[:4])


def _roteiro(gancho, outras):
    d = gancho
    falas = [f"Achei isso por {voz.reais_falado(d['preco'])}!"
             + (f" {voz.por_extenso(int(d['desconto'])).capitalize()} por cento de desconto." if d.get("desconto") else "")]
    for d in outras:  # o "de" já aparece riscado na tela; falar só o "por" deixa o vídeo mais ágil
        falas.append(f"{_nome_curto(d.get('titulo'))}, por {voz.reais_falado(d['preco'])}.")
    falas.append("Essas e outras ofertas, todo dia, no canal do WhatsApp. Link na bio!" if config.WHATS_CANAL
                 else "Essas e outras ofertas, todo dia, no nosso canal. Link na bio!")
    return falas


def _narrar(gancho, cartoes, ofertas):
    """Gera as falas e alonga as cenas se precisar. Devolve [(início, áudio)] ou None se a voz falhar."""
    if not voz.ligada():
        return None
    try:
        textos = _roteiro(ofertas[0][0], [d for d, _ in ofertas[1:]])
        audios = [voz.sintetizar(tx) for tx in textos]
    except Exception as e:  # noqa: BLE001 — sem internet, por exemplo: o vídeo sai só com a batida
        log.warning("Narração falhou, o vídeo sai só com a batida: %s", e)
        return None
    seg = [len(a) / voz.SR for a in audios]
    gancho.dur = max(_Gancho.DUR, VOZ_GANCHO + seg[0] + 0.45)
    for c, s in zip(cartoes, seg[1:-1]):
        c.dur = max(POR_OFERTA, VOZ_CARTAO + s + 0.45)
    t, falas = 0.0, [(VOZ_GANCHO, audios[0])]
    t += gancho.dur
    for c, a in zip(cartoes, audios[1:-1]):
        falas.append((t + VOZ_CARTAO, a))
        t += c.dur
    falas.append((t + VOZ_FINAL, audios[-1]))
    return falas, max(FINAL, VOZ_FINAL + seg[-1] + 0.7)


# ------------------------------------------------------------------ principal

def gerar(ofertas, saida=None):
    """ofertas: lista de (dados, imagem_bytes). Retorna o caminho do .mp4 gerado."""
    ofertas = ofertas[:3]
    if not ofertas:
        raise ValueError("Nenhuma oferta para o vídeo.")
    # a de maior desconto vira o gancho de abertura; as outras vêm depois
    ofertas = sorted(ofertas, key=lambda o: int(o[0].get("desconto") or 0), reverse=True)
    n = len(ofertas)
    gancho = _Gancho(*ofertas[0])
    cartoes = [_Cartao(d, img, i + 2, n) for i, (d, img) in enumerate(ofertas[1:])]
    narracao = _narrar(gancho, cartoes, ofertas)
    final = narracao[1] if narracao else FINAL
    abertura = gancho.dur
    inicios = [abertura]
    for c in cartoes:
        inicios.append(inicios[-1] + c.dur)
    fim_cartoes = inicios[-1]
    duracao = fim_cartoes + final

    if saida is None:
        fd, saida = tempfile.mkstemp(suffix=".mp4", prefix="achados-")
        os.close(fd)
    # trilha de funk própria (sem direitos autorais), sincronizada com as trocas de cena
    audio = None
    try:
        import trilha
        fd, audio = tempfile.mkstemp(suffix=".wav", prefix="trilha-")
        os.close(fd)
        trilha.gerar_wav(audio, duracao, inicio_batida=abertura, transicoes=inicios[1:])
        if narracao:
            # voz por cima, com a batida abaixando sozinha quando alguém fala
            fd, fala = tempfile.mkstemp(suffix=".wav", prefix="voz-")
            os.close(fd)
            fd, mix = tempfile.mkstemp(suffix=".wav", prefix="mix-")
            os.close(fd)
            try:
                voz.salvar_wav(fala, voz.montar_trilha_de_voz(narracao[0], duracao))
                voz.mixar(audio, fala, mix)
                Path(audio).unlink(missing_ok=True)
                audio = mix
            except Exception as e:  # noqa: BLE001
                log.warning("Não consegui mixar a voz, vai só a batida: %s", e)
                Path(mix).unlink(missing_ok=True)
            finally:
                Path(fala).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001 — sem numpy ou com erro, o vídeo sai sem música
        audio = None
    cmd = [_ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    if audio:
        cmd += ["-i", audio, "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest"]
    # qualidade alta: o TikTok/Instagram recomprimem, então mandamos com folga (texto e bordas ficam nítidos)
    cmd += ["-c:v", "libx264", "-preset", "medium", "-tune", "animation", "-crf", "17", "-profile:v", "high",
            "-pix_fmt", "yuv420p", "-r", str(FPS), "-g", str(FPS * 2), "-movflags", "+faststart", saida]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(int(duracao * FPS)):
            t = i / FPS
            if t < abertura:
                quadro = gancho.desenhar(t)
            elif t < fim_cartoes:
                k = max(i for i in range(len(cartoes)) if inicios[i] <= t)
                quadro = Image.new("RGBA", (W, H), AMARELO + (255,))
                cartoes[k].desenhar(quadro, t - inicios[k])
            else:
                quadro = _final(t - fim_cartoes)
            proc.stdin.write(quadro.convert("RGB").tobytes())
    finally:
        proc.stdin.close()
        proc.wait()
        if audio:
            Path(audio).unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError("O ffmpeg não conseguiu gerar o vídeo.")
    return saida
