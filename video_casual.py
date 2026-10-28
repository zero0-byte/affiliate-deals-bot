"""Vídeos descontraídos (cara de TikTok, não de propaganda) feitos sozinhos a partir de uma oferta.

- conversa(dados, imagem): print de conversa animado ("mostrei meu achado pro amigo / pra mãe").
- adivinha(dados, imagem): "adivinha o preço", com 3 opções, contagem e a revelação narrada.

Os dois devolvem o caminho do .mp4 (1080x1920, h264 + aac), igual ao video_achados.gerar.
"""
import logging
import math
import os
import random
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import config
import voz
from criativo import EMOJI, _foto_produto
from formatar import reais
from video_achados import (AMARELO, BRANCO, FPS, H, PRETO, W, _clamp, _colar, _ffmpeg, _fonte, _in_cubic,
                           _out_back, _out_cubic, _pilula, _pop, _prog, _texto)

log = logging.getLogger(__name__)
FUNDO = (18, 18, 20)
CINZA_BOLHA = (44, 44, 50)
VERDE = (37, 211, 102)
VERMELHO = (255, 80, 80)
SR = voz.SR


# ------------------------------------------------------------------ utilidades

def _nome_curto(titulo, palavras=3):
    t = EMOJI.sub("", titulo or "").strip(" -–|")
    for sep in (" - ", " – ", " | ", ",", "(", "["):
        t = t.split(sep)[0]
    return " ".join(t.split()[:palavras]) or "achado"


def _arredonda(v):
    """Preço "de cabeça": 187,90 -> 190; 1.349 -> 1.350."""
    passo = 10 if v < 1000 else 50
    return int(round(v / passo) * passo)


def _quebrar(txt, peso, tam, largura):
    f = _fonte(peso, tam)
    txt = re.sub(r"\s+", " ", EMOJI.sub("", txt)).strip().replace("R$ ", "R$\u00a0")
    linhas, atual = [], ""
    for p in txt.split(" "):
        t = f"{atual} {p}".strip()
        if f.getlength(t) <= largura or not atual:
            atual = t
        else:
            linhas.append(atual)
            atual = p
    return linhas + ([atual] if atual else [])


def _caixa(txt, tam=56, fundo=BRANCO, cor=PRETO, larg=860, peso="Bold"):
    """Texto no estilo legenda do TikTok: caixa arredondada."""
    linhas = _quebrar(txt, peso, tam, larg - 60)
    f = _fonte(peso, tam)
    alt = int(tam * 1.25)
    w = int(max(f.getlength(x) for x in linhas)) + 60
    h = alt * len(linhas) + 36
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=22, fill=fundo)
    for i, x in enumerate(linhas):
        d.text((w / 2, 18 + alt * i + alt / 2), x, font=f, fill=cor, anchor="mm")
    return img


def _marca(img, y=150):
    arroba = config.CANAL if (config.CANAL or "").startswith("@") else config.MARCA
    img.alpha_composite(_texto(arroba, "Bold", 34, (255, 255, 255, 150)), (60, y))


def _foto(imagem, lado, raio=36):
    foto = _foto_produto(imagem, lado, lado).convert("RGBA")
    m = Image.new("L", foto.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, lado - 1, lado - 1], radius=raio, fill=255)
    foto.putalpha(m)
    return foto


# ------------------------------------------------------------------ sons (gerados aqui, sem direitos autorais)

def _x(L):
    return np.arange(int(SR * L)) / SR


def _som_pop():
    x = _x(0.09)
    f = 900 + 900 * np.exp(-x * 60)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-x * 45) * 0.5


def _som_tick():
    x = _x(0.05)
    return np.sin(2 * np.pi * 1800 * x) * np.exp(-x * 120) * 0.45


def _som_ding():
    x = _x(0.9)
    return (np.sin(2 * np.pi * 1318 * x) + 0.6 * np.sin(2 * np.pi * 1976 * x)) * np.exp(-x * 5) * 0.35


def _som_impacto():
    x = _x(0.6)
    f = 45 + 160 * np.exp(-x * 25)
    return np.tanh(np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-x * 6) * 2) * 0.8


def _pista(duracao, eventos):
    """eventos: [(tempo, áudio)] -> um áudio só, do tamanho do vídeo."""
    return voz.montar_trilha_de_voz([(t, a.astype(np.float32)) for t, a in eventos], duracao)


def _render(duracao, quadro, efeitos, inicio_batida, trocas, saida=None):
    """Gera o mp4: quadros + batida baixinha + efeitos/voz por cima (a batida abaixa sozinha)."""
    if saida is None:
        fd, saida = tempfile.mkstemp(suffix=".mp4", prefix="casual-")
        os.close(fd)
    tmp = Path(tempfile.mkdtemp(prefix="casual-"))
    audio = None
    try:
        import trilha
        trilha.gerar_wav(str(tmp / "batida.wav"), duracao, inicio_batida=inicio_batida, transicoes=trocas)
        voz.salvar_wav(tmp / "efeitos.wav", _pista(duracao, efeitos))
        voz.mixar(tmp / "batida.wav", tmp / "efeitos.wav", tmp / "mix.wav")
        audio = tmp / "mix.wav"
    except Exception as e:  # noqa: BLE001 — sem áudio, o vídeo sai mudo (você coloca um som no TikTok)
        log.warning("Áudio do vídeo casual falhou: %s", e)
    cmd = [_ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    if audio:
        cmd += ["-i", str(audio), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-tune", "animation", "-crf", "17", "-profile:v", "high",
            "-pix_fmt", "yuv420p", "-r", str(FPS), "-g", str(FPS * 2), "-movflags", "+faststart", str(saida)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(int(duracao * FPS)):
            proc.stdin.write(quadro(i / FPS).convert("RGB").tobytes())
    finally:
        proc.stdin.close()
        proc.wait()
        for f in tmp.iterdir():
            f.unlink(missing_ok=True)
        tmp.rmdir()
    if proc.returncode != 0:
        raise RuntimeError("O ffmpeg não conseguiu gerar o vídeo.")
    return saida


# ------------------------------------------------------------------ 1) conversa

def _roteiro_conversa(d):
    preco = reais(d["preco"])
    orig = d.get("preco_original")
    chute = _arredonda(orig if orig and orig > d["preco"] else d["preco"] * 1.8)
    nome = _nome_curto(d.get("titulo")).lower()
    amigo = ("Juninho", f"quando eu mostro meu achado pro meu amigo:", [
        (True, "FOTO"), (True, "olha o que eu achei hoje"), (False, "quanto?"), (True, "chuta"),
        (False, f"uns {chute} conto?"), (True, preco), (False, "MENTIRA"), (False, "manda o link AGORA"),
        (True, "tá no canal, link na bio kkkk")])
    mae = ("Mãe", "minha mãe quando vê a caixa chegando:", [
        (False, "filho chegou mais uma caixa aqui"), (False, "comprou coisa de computador DE NOVO??"),
        (True, f"mãe foi promoção, um {nome}"), (False, "quanto foi isso"), (True, preco),
        (False, "e era quanto?"), (True, reais(orig) if orig and orig > d["preco"] else "bem mais caro"),
        (False, "..."), (False, "compra um pra mim também")])
    era = reais(orig) if orig and orig > d["preco"] else "bem mais caro"
    chefe = ("Chefe", "eu no trabalho fingindo que tô ocupado:", [
        (False, "tá fazendo o relatório?"), (True, "tô sim chefe"), (False, "e esse barulho de notificação?"),
        (True, f"é que baixou um {nome}"), (True, f"tá {preco}"), (False, "..."), (False, "manda o link"),
        (False, "e termina o relatório"), (True, "tá no canal, link na bio")])
    amor = ("Amor", "quando ela pergunta o que eu quero de presente:", [
        (False, "amor o que vc quer de presente?"), (True, "nada não"), (False, "fala sério"), (True, "FOTO"),
        (True, f"esse {nome}"), (False, "isso não é caro??"), (True, preco), (False, "SÓ ISSO?"),
        (False, "vou comprar 2 então kkkk")])
    familia = ("Família", "o grupo da família quando eu mando um achado:", [
        (True, "FOTO"), (True, f"achei esse {nome} por {preco}"), (False, "tio: isso é golpe"),
        (True, "não tio, é promoção"), (False, f"vó: era {era}?"), (True, "isso vó"),
        (False, "tia: me manda o link"), (False, "tio: me manda também"), (True, "link na bio pessoal")])
    entregador = ("Entregador", "o entregador que já me conhece:", [
        (False, "boa tarde, encomenda pra você de novo"), (False, "é a terceira essa semana"),
        (True, f"é que tá {preco} um {nome}"), (False, "sério??"), (False, "onde vc acha isso?"),
        (True, "num canal de promoções"), (False, "passa o nome aí"), (True, f"{config.MARCA}, link na bio"),
        (False, "agora eu vou entregar pra mim mesmo kkkk")])
    return random.choice([amigo, mae, chefe, amor, familia, entregador])


def _bolha(txt, minha):
    tam = 50
    linhas = _quebrar(txt, "SemiBold", tam, 620)
    f = _fonte("SemiBold", tam)
    alt = int(tam * 1.3)
    w = int(max(f.getlength(x) for x in linhas)) + 64
    h = alt * len(linhas) + 40
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=34, fill=AMARELO if minha else CINZA_BOLHA)
    for i, x in enumerate(linhas):
        d.text((32, 20 + alt * i + alt / 2), x, font=f, fill=PRETO if minha else BRANCO, anchor="lm")
    return img


def _bolha_foto(imagem, minha):
    foto = _foto(imagem, 440, 28)
    img = Image.new("RGBA", (foto.width + 16, foto.height + 16), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=34,
                                          fill=AMARELO if minha else CINZA_BOLHA)
    img.alpha_composite(foto, (8, 8))
    return img


def conversa(dados, imagem, saida=None):
    contato, topo, msgs = _roteiro_conversa(dados)
    imgs = [_bolha_foto(imagem, m) if t == "FOTO" else _bolha(t, m) for m, t in msgs]
    legenda = _caixa(topo, 50)
    cab = _texto(EMOJI.sub("", contato).strip(), "Bold", 52, BRANCO)
    sub = _texto("online", "SemiBold", 34, (140, 140, 150))
    # tempos: cada mensagem entra depois de um respiro; antes da revelação do preço, um suspense maior
    t, tempos = 0.5, []
    for k, (m, txt) in enumerate(msgs):
        tempos.append(t)
        t += 1.0 if txt != "FOTO" else 1.3
        if k + 1 < len(msgs) and msgs[k + 1][1] == reais(dados["preco"]):
            t += 0.7
    dur = t + 2.0
    efeitos = [(tk, _som_pop()) for tk in tempos]
    k_preco = next((k for k, (_, x) in enumerate(msgs) if x == reais(dados["preco"])), None)
    if k_preco is not None:
        efeitos.append((tempos[k_preco], _som_impacto()))

    def quadro(t):
        img = Image.new("RGBA", (W, H), FUNDO + (255,))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, W, 300], fill=(28, 28, 32))
        d.ellipse([60, 180, 160, 280], fill=(90, 90, 100))
        d.text((110, 230), contato[0].upper(), font=_fonte("Black", 56), fill=BRANCO, anchor="mm")
        img.alpha_composite(cab, (190, 175))
        img.alpha_composite(sub, (190, 240))
        visiveis = [k for k in range(len(msgs)) if t >= tempos[k]]
        y = 1560
        for k in reversed(visiveis):  # as mais novas embaixo, rolando pra cima
            im = imgs[k]
            p = _prog(t, tempos[k], 0.25)
            y -= im.height + 22
            if y < 520:
                break
            x = W - 70 - im.width if msgs[k][0] else 70
            tremor = 10 * (1 - _prog(t, tempos[k], 0.4)) * math.sin(t * 90) if k == k_preco else 0
            _colar(img, im, x + im.width / 2 + tremor, y + im.height / 2 + 30 * (1 - _out_cubic(p)),
                   escala=0.9 + 0.1 * _out_back(p), alfa=_clamp(p * 3))
        prox = len(visiveis)
        if prox < len(msgs) and t > tempos[prox] - 0.55:  # "digitando..."
            minha = msgs[prox][0]
            bx = W - 70 - 150 if minha else 70
            d.rounded_rectangle([bx, 1590, bx + 150, 1670], radius=34, fill=AMARELO if minha else CINZA_BOLHA)
            for j in range(3):
                fase = math.sin((t * 8) - j * 0.8)
                d.ellipse([bx + 30 + j * 36, 1620 - 6 * fase, bx + 50 + j * 36, 1640 - 6 * fase],
                          fill=PRETO if minha else BRANCO)
        # legenda de gancho por cima (já no 1º quadro)
        _colar(img, legenda, W / 2, 400, escala=1.04 - 0.04 * _out_cubic(_prog(t, 0, 0.3)))
        return img

    return _render(dur, quadro, efeitos, 0.5, [tempos[k_preco]] if k_preco is not None else [], saida)


# ------------------------------------------------------------------ 2) adivinha o preço

def _contagem(n):
    img = Image.new("RGBA", (280, 280), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 0, 279, 279], fill=PRETO + (230,))
    d.text((140, 150), str(n), font=_fonte("Black", 190), fill=AMARELO, anchor="mm")
    return img


def adivinha(dados, imagem, saida=None):
    preco = dados["preco"]
    orig = dados.get("preco_original")
    alto = orig if orig and orig > preco * 1.15 else preco * 1.7
    isca = lambda v: (_arredonda(v) if v >= 100 else round(v)) - 0.1  # noqa: E731 — preço "de loja" (x,90)
    opcoes = [preco, isca(alto), isca((preco + alto) / 2)]
    if len({round(v, 2) for v in opcoes}) < 3:
        opcoes = [preco, isca(preco * 1.8), isca(preco * 1.4)]
    random.shuffle(opcoes)
    certa = opcoes.index(preco)
    letras = "ABC"
    nome = _nome_curto(dados.get("titulo"), 4)

    titulo1 = _texto("ADIVINHA", "Black", 120, AMARELO)
    titulo2 = _texto("O PREÇO", "Black", 120, BRANCO)
    foto = _foto(imagem, 620)
    nome_img = _caixa(nome, 48, larg=900)
    pilulas = [_pilula(f"{letras[k]})  {reais(v)}", 62, BRANCO, PRETO, 50, 26) for k, v in enumerate(opcoes)]
    pil_certa = _pilula(f"{letras[certa]})  {reais(preco)}", 62, VERDE, PRETO, 50, 26)
    desc = int(dados.get("desconto") or 0)
    selo = _pilula(f"{desc}% OFF", 56, AMARELO, PRETO, 36, 20) if desc else None
    cta = _caixa("comenta quanto você chutou! link no canal (bio)", 50, fundo=AMARELO)

    # falas (se a voz falhar, o vídeo sai só com sons e batida)
    falas = {}
    if voz.ligada():
        try:
            falas["pergunta"] = voz.sintetizar(f"Adivinha quanto tá custando esse {nome}?")
            falas["revela"] = voz.sintetizar(f"{voz.reais_falado(preco)}! Quanto você chutou? Comenta aí!")
        except Exception as e:  # noqa: BLE001
            log.warning("Narração do adivinha falhou: %s", e)
    seg = {k: len(a) / SR for k, a in falas.items()}
    t_opcoes = max(2.0, 0.2 + seg.get("pergunta", 0) + 0.2)
    t_conta = t_opcoes + 1.6
    t_revela = t_conta + 3.0
    t_cta = t_revela + max(1.8, seg.get("revela", 0) + 0.2)
    dur = t_cta + 2.4

    efeitos = [(0.0, _som_impacto())] + [(t_opcoes + 0.25 * k, _som_pop()) for k in range(3)]
    efeitos += [(t_conta + k, _som_tick()) for k in range(3)] + [(t_revela, _som_ding()), (t_revela, _som_impacto())]
    if "pergunta" in falas:
        efeitos.append((0.2, falas["pergunta"]))
    if "revela" in falas:
        efeitos.append((t_revela + 0.15, falas["revela"]))

    def quadro(t):
        img = Image.new("RGBA", (W, H), FUNDO + (255,))
        soco = 1.12 - 0.12 * _out_cubic(_prog(t, 0, 0.3))
        _colar(img, titulo1, W / 2, 300, escala=soco)
        _colar(img, titulo2, W / 2, 425, escala=soco)
        zoom = 1 + 0.04 * _prog(t, 0, dur)
        _colar(img, foto, W / 2, 830, escala=zoom * (0.85 + 0.15 * _out_back(_prog(t, 0.0, 0.4))))
        _colar(img, nome_img, W / 2, 1180, alfa=_prog(t, 0.3, 0.3))
        if t < t_revela:
            for k, p in enumerate(pilulas):
                _pop(img, p, W / 2, 1330 + k * 125, t, t_opcoes + 0.25 * k, 0.35, de=0.7)
            if t >= t_conta:
                n = 3 - int(t - t_conta)
                pp = (t - t_conta) % 1
                _colar(img, _contagem(n), W / 2, 830, escala=1.3 - 0.3 * _out_cubic(min(1, pp * 3)),
                       alfa=1 - _in_cubic(pp))
        else:
            tl = t - t_revela
            for k, p in enumerate(pilulas):
                if k == certa:
                    sx = 12 * (1 - _prog(tl, 0, 0.4)) * math.sin(t * 90)
                    _colar(img, pil_certa, W / 2 + sx, 1330 + k * 125, escala=1 + 0.08 * (1 - _prog(tl, 0, 0.3)))
                else:
                    _colar(img, p, W / 2, 1330 + k * 125, alfa=0.25)
                    ImageDraw.Draw(img).line([(W / 2 - p.width / 2 + 30, 1330 + k * 125),
                                              (W / 2 + p.width / 2 - 30, 1330 + k * 125)], fill=VERMELHO, width=8)
            if selo:
                ps = _prog(tl, 0.3, 0.4)
                if ps > 0:
                    _colar(img, selo, 860, 560, escala=0.3 + 0.7 * _out_back(ps, 2.4), alfa=_clamp(ps * 3),
                           rot=12 + (1 - _out_cubic(ps)) * 50)
            if t >= t_cta:
                _pop(img, cta, W / 2, 1740, t, t_cta, 0.4, de=0.8)
        _marca(img, 150)
        return img

    return _render(dur, quadro, efeitos, t_opcoes, [t_revela], saida)


# ------------------------------------------------------------------ 3) POV (meme)

_POVS = [
    ("POV: você achou {nome} por {preco}", "e ainda tava {desc}% OFF"),
    ("POV: seu amigo pagou {orig} nesse {nome}", "e você pagou {preco}"),
    ("POV: você esperou a promoção certa", "{nome} por {preco}"),
    ("POV: o salário caiu e o {nome} tá {preco}", "a decisão já foi tomada"),
]


def pov(dados, imagem, saida=None):
    preco = dados["preco"]
    orig = dados.get("preco_original") or 0
    desc = int(dados.get("desconto") or 0)
    nome = _nome_curto(dados.get("titulo")).lower()
    opcoes = [p for p in _POVS if ("{orig}" not in p[0] or orig > preco) and ("{desc}" not in p[1] or desc)]
    l1, l2 = random.choice(opcoes)
    campos = {"nome": nome, "preco": reais(preco), "orig": reais(orig), "desc": desc}
    topo = _caixa(l1.format(**campos), 62, larg=900)
    reacao = _caixa(l2.format(**campos), 58, fundo=AMARELO)
    foto = _foto(imagem, 720)
    cta = _caixa("link no canal (bio)", 46)
    falas = {}
    if voz.ligada():
        try:
            falas["a"] = voz.sintetizar(l1.format(**{**campos, "preco": voz.reais_falado(preco),
                                                     "orig": voz.reais_falado(orig or preco)}).replace("POV:", "Pi ou vi:"))
        except Exception as e:  # noqa: BLE001
            log.warning("Narração do POV falhou: %s", e)
    t_reacao = max(2.6, 0.2 + len(falas.get("a", [])) / SR + 0.3)
    dur = t_reacao + 4.2
    efeitos = [(0.0, _som_impacto()), (t_reacao, _som_impacto()), (t_reacao + 0.1, _som_ding())]
    if "a" in falas:
        efeitos.append((0.2, falas["a"]))

    def quadro(t):
        img = Image.new("RGBA", (W, H), FUNDO + (255,))
        # zoom lento + "tremida" na virada
        z = 1 + 0.08 * _prog(t, 0, dur)
        tr = 16 * (1 - _prog(t, t_reacao, 0.4)) * math.sin(t * 95) if t >= t_reacao else 0
        _colar(img, foto, W / 2 + tr, 980, escala=z)
        _colar(img, topo, W / 2, 440, escala=1.04 - 0.04 * _out_cubic(_prog(t, 0, 0.3)))
        if t >= t_reacao:
            _pop(img, reacao, W / 2, 1460, t, t_reacao, 0.35, de=0.6)
            _pop(img, cta, W / 2, 1600, t, t_reacao + 1.2, 0.35, de=0.8)
        _marca(img)
        return img

    return _render(dur, quadro, efeitos, t_reacao, [t_reacao], saida)


# ------------------------------------------------------------------ 4) não compre antes de ver

def nao_compre(dados, imagem, saida=None):
    preco = dados["preco"]
    orig = dados.get("preco_original")
    nome = _nome_curto(dados.get("titulo"), 2).upper()
    l1 = _texto("NÃO COMPRE", "Black", 130, VERMELHO)
    l2 = _caixa(f"{nome} ANTES DE VER ISSO", 64, fundo=BRANCO, larg=960, peso="Black")
    foto = _foto(imagem, 640)
    era = _texto(f"na loja: {reais(orig)}", "Bold", 64, (180, 180, 186)) if orig and orig > preco else None
    agora = _pilula(f"ACHEI POR {reais(preco)}", 72, AMARELO, PRETO, 50, 28)
    cta = _caixa("todo dia tem achado assim no canal (link na bio)", 46)
    falas = {}
    if voz.ligada():
        try:
            falas["a"] = voz.sintetizar(f"Não compre {nome.lower()} antes de ver isso.")
            falas["b"] = voz.sintetizar(f"Achei por {voz.reais_falado(preco)}. O link tá no canal!")
        except Exception as e:  # noqa: BLE001
            log.warning("Narração do não compre falhou: %s", e)
    t_foto = max(2.2, 0.2 + len(falas.get("a", [])) / SR + 0.2)
    t_preco = t_foto + (1.6 if era else 0.8)
    dur = t_preco + max(3.2, len(falas.get("b", [])) / SR + 1.0)
    efeitos = [(0.0, _som_impacto()), (t_foto, _som_pop()), (t_preco, _som_impacto()), (t_preco, _som_ding())]
    if "a" in falas:
        efeitos.append((0.2, falas["a"]))
    if "b" in falas:
        efeitos.append((t_preco + 0.1, falas["b"]))

    def quadro(t):
        img = Image.new("RGBA", (W, H), FUNDO + (255,))
        tr = 14 * (1 - _prog(t, 0, 0.5)) * math.sin(t * 90)
        _colar(img, l1, W / 2 + tr, 330, escala=1.15 - 0.15 * _out_cubic(_prog(t, 0, 0.3)))
        _colar(img, l2, W / 2, 540)
        if t >= t_foto:
            _pop(img, foto, W / 2, 960, t, t_foto, 0.4, de=0.7)
        if era and t >= t_foto + 0.6:
            _colar(img, era, W / 2, 1330, alfa=_prog(t, t_foto + 0.6, 0.3))
            p = _out_cubic(_prog(t, t_foto + 1.1, 0.3))
            if p > 0:
                ImageDraw.Draw(img).line([(W / 2 - era.width / 2, 1332), (W / 2 - era.width / 2 + era.width * p, 1332)],
                                         fill=VERMELHO, width=9)
        if t >= t_preco:
            sx = 12 * (1 - _prog(t, t_preco, 0.4)) * math.sin(t * 90)
            _colar(img, agora, W / 2 + sx, 1460, escala=1.5 - 0.5 * _out_cubic(_prog(t, t_preco, 0.3)))
            _pop(img, cta, W / 2, 1620, t, t_preco + 1.0, 0.35, de=0.8)
        _marca(img)
        return img

    return _render(dur, quadro, efeitos, t_foto, [t_preco], saida)


# ------------------------------------------------------------------ 5) duelo: qual você levaria?

def duelo(ofertas, saida=None):
    """ofertas: [(dados, imagem), (dados, imagem)]. "Qual você levaria? comenta 1 ou 2"."""
    (d1, i1), (d2, i2) = ofertas[:2]
    titulo = _texto("QUAL VOCÊ LEVARIA?", "Black", 92, AMARELO)
    fotos = [_foto(i1, 430, 30), _foto(i2, 430, 30)]
    nomes = [_caixa(_nome_curto(d.get("titulo"), 3), 40, larg=480) for d in (d1, d2)]
    precos = [_pilula(reais(d["preco"]), 54, AMARELO, PRETO, 34, 18) for d in (d1, d2)]
    nums = [_pilula(n, 64, BRANCO, PRETO, 34, 14) for n in ("1", "2")]
    vs = Image.new("RGBA", (180, 180), (0, 0, 0, 0))
    ImageDraw.Draw(vs).ellipse([0, 0, 179, 179], fill=AMARELO)
    ImageDraw.Draw(vs).text((90, 94), "VS", font=_fonte("Black", 80), fill=PRETO, anchor="mm")
    cta = _caixa("comenta 1 ou 2! os dois estão no canal (link na bio)", 46, fundo=AMARELO)
    falas = {}
    if voz.ligada():
        try:
            falas["a"] = voz.sintetizar("Qual você levaria? Um ou dois? Comenta aí!")
        except Exception as e:  # noqa: BLE001
            log.warning("Narração do duelo falhou: %s", e)
    dur = 9.0
    efeitos = [(0.0, _som_impacto()), (0.6, _som_pop()), (1.2, _som_pop()), (2.0, _som_impacto()),
               (3.0, _som_ding()), (4.2, _som_ding())]
    if "a" in falas:
        efeitos.append((0.2, falas["a"]))

    def quadro(t):
        img = Image.new("RGBA", (W, H), FUNDO + (255,))
        _colar(img, titulo, W / 2, 330, escala=1.08 - 0.08 * _out_cubic(_prog(t, 0, 0.3)))
        for k, x in enumerate((290, 790)):
            ini = 0.6 + 0.6 * k
            if t >= ini:
                bal = 1 + 0.03 * math.sin((t - ini) * 2 * math.pi * 0.8 + k * math.pi)
                _pop(img, fotos[k], x, 800, t, ini, 0.4, de=0.6)
                if t > ini + 0.4:
                    _colar(img, fotos[k], x, 800, escala=bal)
                _colar(img, nums[k], x, 530, alfa=_prog(t, ini, 0.3))
                _colar(img, nomes[k], x, 1105, alfa=_prog(t, ini + 0.2, 0.3))
            if t >= 3.0 + 1.2 * k:
                _pop(img, precos[k], x, 1230, t, 3.0 + 1.2 * k, 0.35, de=0.5)
        if t >= 2.0:
            _colar(img, vs, W / 2, 800, escala=1.6 - 0.6 * _out_back(_prog(t, 2.0, 0.4)), alfa=_prog(t, 2.0, 0.2))
        if t >= 5.0:
            _pop(img, cta, W / 2, 1450, t, 5.0, 0.4, de=0.8)
        _marca(img)
        return img

    return _render(dur, quadro, efeitos, 2.0, [3.0, 4.2], saida)


# ------------------------------------------------------------------ 6) loja cara x achado

def comparacao(dados, imagem, saida=None):
    preco = dados["preco"]
    orig = dados.get("preco_original")
    if not orig or orig <= preco:
        return adivinha(dados, imagem, saida)  # sem preço "de", não dá pra comparar
    nome = _nome_curto(dados.get("titulo"), 3)
    foto = _foto(imagem, 420, 28)
    topo = _caixa(f"{nome}: onde você compraria?", 54, larg=960)
    lado_a = _texto("PREÇO NORMAL", "Black", 54, (200, 200, 205))
    lado_b = _texto("NO CANAL", "Black", 54, AMARELO)
    pa = _texto(reais(orig), "Black", 92, VERMELHO)
    pb = _texto(reais(preco), "Black", 92, VERDE)
    economia = _pilula(f"ECONOMIA DE {reais(orig - preco)}", 60, AMARELO, PRETO, 44, 24)
    cta = _caixa("link no canal (bio)", 46)
    falas = {}
    if voz.ligada():
        try:
            falas["a"] = voz.sintetizar(f"Preço normal: {voz.reais_falado(orig)}.")
            falas["b"] = voz.sintetizar(f"No {config.MARCA}: {voz.reais_falado(preco)}!")
        except Exception as e:  # noqa: BLE001
            log.warning("Narração da comparação falhou: %s", e)
    t_a = 1.0
    t_b = t_a + max(2.0, len(falas.get("a", [])) / SR + 0.4)
    t_eco = t_b + max(1.8, len(falas.get("b", [])) / SR + 0.3)
    dur = t_eco + 3.0
    efeitos = [(0.0, _som_pop()), (t_a, _som_impacto()), (t_b, _som_impacto()), (t_eco, _som_ding())]
    if "a" in falas:
        efeitos.append((t_a + 0.1, falas["a"]))
    if "b" in falas:
        efeitos.append((t_b + 0.1, falas["b"]))

    def quadro(t):
        img = Image.new("RGBA", (W, H), FUNDO + (255,))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 620, W / 2 - 4, 1330], fill=(40, 26, 28))
        d.rectangle([W / 2 + 4, 620, W, 1330], fill=(24, 40, 30))
        _colar(img, topo, W / 2, 330)
        _colar(img, foto, W / 2, 620, escala=0.6 + 0.4 * _out_back(_prog(t, 0, 0.4)))
        if t >= t_a:
            _pop(img, lado_a, W / 4, 930, t, t_a, 0.3)
            _pop(img, pa, W / 4, 1060, t, t_a + 0.2, 0.35, de=0.5)
        if t >= t_b:
            _pop(img, lado_b, 3 * W / 4, 930, t, t_b, 0.3)
            sx = 10 * (1 - _prog(t, t_b + 0.2, 0.4)) * math.sin(t * 90)
            _pop(img, pb, 3 * W / 4 + sx, 1060, t, t_b + 0.2, 0.35, de=0.5)
        if t >= t_eco:
            _pop(img, economia, W / 2, 1450, t, t_eco, 0.4, de=0.5)
            _pop(img, cta, W / 2, 1590, t, t_eco + 0.8, 0.35, de=0.8)
        _marca(img)
        return img

    return _render(dur, quadro, efeitos, t_a, [t_b, t_eco], saida)


# ------------------------------------------------------------------ rodízio

FORMATOS = ["adivinha", "conversa", "pov", "nao_compre", "duelo", "comparacao"]
NOMES = {"adivinha": "adivinha o preço", "conversa": "conversa", "pov": "POV", "nao_compre": "não compre antes de ver",
         "duelo": "qual você levaria", "comparacao": f"preço normal x {config.MARCA}"}


def gerar(formato, ofertas, saida=None):
    """ofertas: [(dados, imagem), ...]; o duelo usa 2, os outros usam a primeira."""
    if formato == "duelo":
        if len(ofertas) >= 2:
            return duelo(ofertas, saida)
        formato = "adivinha"
    dados, imagem = ofertas[0]
    return {"adivinha": adivinha, "conversa": conversa, "pov": pov, "nao_compre": nao_compre,
            "comparacao": comparacao}.get(formato, adivinha)(dados, imagem, saida)
