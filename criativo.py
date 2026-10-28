"""Gera as artes do Instagram (Story 9:16 e Feed 4:5) para cada oferta."""
import io
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

import config
from formatar import reais

PASTA_ARTE = Path(__file__).resolve().parent / "arte"
AMARELO = (255, 208, 0)
PRETO = (23, 23, 26)
BRANCO = (255, 255, 255)
CINZA = (92, 92, 102)
FUNDO_FOTO = (244, 244, 246)


# ------------------------------------------------------------------ fontes e logo

def _fonte(peso, tamanho):
    arquivo = {"black": "Inter-Black.otf", "bold": "Inter-Bold.otf", "semi": "Inter-SemiBold.otf"}[peso]
    try:
        return ImageFont.truetype(str(PASTA_ARTE / arquivo), tamanho)
    except OSError:
        return ImageFont.load_default(tamanho)


def _logo(tamanho):
    try:
        img = Image.open(PASTA_ARTE / "logo.png").convert("RGBA").resize((tamanho, tamanho), Image.LANCZOS)
    except OSError:
        return None
    mascara = Image.new("L", (tamanho, tamanho), 0)
    ImageDraw.Draw(mascara).ellipse([0, 0, tamanho - 1, tamanho - 1], fill=255)
    img.putalpha(mascara)
    return img


def _arroba():
    canal = config.CANAL.strip()
    return canal if canal.startswith("@") else ""


# ------------------------------------------------------------------ texto

def _quebrar(texto, fonte, largura, max_linhas):
    palavras = texto.split()
    linhas, atual = [], ""
    for p in palavras:
        teste = f"{atual} {p}".strip()
        if fonte.getlength(teste) <= largura:
            atual = teste
            continue
        if atual:
            linhas.append(atual)
        atual = p
        if len(linhas) == max_linhas:
            break
    if atual and len(linhas) < max_linhas:
        linhas.append(atual)
    usadas = " ".join(linhas).split()
    if len(usadas) < len(palavras):  # cortou: põe reticências na última linha
        ultima = linhas[-1]
        while ultima and fonte.getlength(ultima + "…") > largura:
            ultima = ultima.rsplit(" ", 1)[0] if " " in ultima else ultima[:-1]
        linhas[-1] = ultima + "…"
    return linhas


def _numero_br(texto):
    texto = texto.strip().replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def ler_oferta_do_texto(texto):
    """Tenta montar os dados da arte a partir de um post manual.

    Usa a primeira linha (sem link e sem preço) como título; com dois preços,
    o maior vira o 'De' e o menor o 'Por'.
    """
    achados = re.findall(r"R\$\s*([\d.]+(?:,\d{1,2})?)", texto)
    # Também aceita linhas como "De 199,90" e "Por 129,90" (sem R$)
    for linha in texto.splitlines():
        m = re.match(r"^\s*(?:de|por|era|agora)\b\s*:?\s*(?:R\$\s*)?([\d.]*\d(?:,\d{1,2})?)\s*$", linha, re.I)
        if m and "R$" not in linha:
            achados.append(m.group(1))
    precos = [v for v in (_numero_br(x) for x in achados) if v]
    if not precos:
        return None
    titulo = ""
    for linha in texto.splitlines():
        limpa = re.sub(r"https?://\S+", "", linha)
        limpa = re.sub(r"(?i)\b(com\s+)?\d{1,2}\s*%\s*(off|de desconto)", "", limpa)  # o % já vai no selo
        limpa = re.sub(r"\s{2,}", " ", limpa).strip(" -–|")
        if limpa and "R$" not in limpa and not re.match(r"^(de|por|era|agora)\b\s*:?\s*[\d.,]+\s*$", limpa, re.I):
            titulo = limpa
            break
    preco = min(precos)
    original = max(precos) if len(precos) > 1 and max(precos) > preco else None
    pct = re.search(r"(\d{1,2})\s*%\s*(?:off|de desconto)", texto, re.I)  # "60%" sozinho pode ser do produto
    desconto = int(pct.group(1)) if pct else (round((1 - preco / original) * 100) if original else 0)

    loja = ""
    dominios = {"amazon": "Amazon", "amzn": "Amazon", "mercadoli": "Mercado Livre", "meli.la": "Mercado Livre", "shopee": "Shopee",
                "shp.ee": "Shopee", "aliexpress": "AliExpress", "magazineluiza": "Magalu", "magalu": "Magalu",
                "kabum": "KaBuM!", "awin1.com": "KaBuM!"}
    for chave, nome in dominios.items():
        if chave in texto.lower():
            loja = nome
            break
    return {"titulo": titulo or "Oferta", "preco": preco, "preco_original": original,
            "desconto": desconto, "fonte": loja, "imagem": None}


# ------------------------------------------------------------------ desenho

def _foto_produto(imagem_bytes, largura, altura):
    caixa = Image.new("RGB", (largura, altura), FUNDO_FOTO)
    if not imagem_bytes:
        d = ImageDraw.Draw(caixa)
        d.text((largura // 2, altura // 2), "OFERTA", font=_fonte("black", 90), fill=(200, 200, 206), anchor="mm")
        return caixa
    try:
        foto = Image.open(io.BytesIO(imagem_bytes))
        foto = ImageOps.exif_transpose(foto).convert("RGBA")
    except Exception:  # noqa: BLE001 — imagem quebrada vira o espaço reservado
        return _foto_produto(None, largura, altura)
    fundo = Image.new("RGBA", foto.size, BRANCO + (255,))
    fundo.alpha_composite(foto)
    foto = fundo.convert("RGB")
    caixa = Image.new("RGB", (largura, altura), BRANCO)
    foto.thumbnail((largura - 40, altura - 40), Image.LANCZOS)
    caixa.paste(foto, ((largura - foto.width) // 2, (altura - foto.height) // 2))
    return caixa


def _arredondar(img, raio):
    mascara = Image.new("L", img.size, 0)
    ImageDraw.Draw(mascara).rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=raio, fill=255)
    saida = img.convert("RGBA")
    saida.putalpha(mascara)
    return saida


VERDE_WHATS = (37, 211, 102)


def _icone_whats(d, cx, cy, diametro, fundo, traco):
    """Bolinha com um balão de conversa (lembra o ícone do WhatsApp, sem usar a marca)."""
    d.ellipse([cx - diametro / 2, cy - diametro / 2, cx + diametro / 2, cy + diametro / 2], fill=fundo)
    r = diametro * 0.27
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=traco, width=max(2, int(diametro * 0.07)))
    d.polygon([(cx - r * 0.95, cy + r * 0.35), (cx - r * 1.25, cy + r * 1.25), (cx - r * 0.25, cy + r * 0.95)], fill=traco)


def _chamada_whats(img, y0, largura, alt_pilula, alt_extra, arroba, s):
    """WhatsApp em destaque: pílula verde 'SIGA NO WHATSAPP', o link grande e o Telegram pequeno embaixo."""
    d = ImageDraw.Draw(img)
    texto = "SIGA NO WHATSAPP"
    f_cta = _fonte("black", s(52))
    icone = s(64)
    larg = icone + s(22) + f_cta.getlength(texto) + s(120)
    x0 = (largura - larg) / 2
    d.rounded_rectangle([x0, y0, x0 + larg, y0 + alt_pilula], radius=alt_pilula // 2, fill=VERDE_WHATS)
    xi = x0 + s(60) + icone / 2
    _icone_whats(d, xi, y0 + alt_pilula / 2, icone, BRANCO, VERDE_WHATS)
    d.text((xi + icone / 2 + s(22), y0 + alt_pilula / 2), texto, font=f_cta, fill=BRANCO, anchor="lm")

    link = config.WHATS_ARTE
    tam = s(50)
    while tam > 18 and _fonte("black", tam).getlength(link) > largura - s(160):
        tam -= 2
    y_link = y0 + alt_pilula + s(48)
    d.text((largura / 2, y_link), link, font=_fonte("black", tam), fill=PRETO, anchor="mm")
    if arroba:
        d.text((largura / 2, y_link + s(58)), f"também no Telegram {arroba}", font=_fonte("semi", s(32)),
               fill=CINZA, anchor="mm")


def _desenhar(dados, imagem_bytes, largura, altura, topo, altura_foto, base_segura, escala=1.0, whats=True):
    s = lambda v: int(v * escala)  # noqa: E731
    img = Image.new("RGBA", (largura, altura), AMARELO + (255,))
    d = ImageDraw.Draw(img)
    margem = 80

    tam_logo = s(130)
    f_marca = _fonte("black", s(60))
    largura_marca = f_marca.getlength(config.MARCA.upper())
    card_x0, card_x1 = margem, largura - margem
    pad = s(40)
    interno = card_x1 - card_x0 - 2 * pad

    f_titulo = _fonte("bold", s(48))
    linhas = _quebrar(dados.get("titulo", ""), f_titulo, interno, 3)
    altura_linha = s(60)
    f_de = _fonte("semi", s(40))
    f_preco = _fonte("black", s(104))
    f_badge = _fonte("black", s(42))
    f_loja = _fonte("semi", s(34))

    original = dados.get("preco_original")
    tem_de = bool(original and original > dados["preco"])
    altura_card = (pad + altura_foto + s(32) + altura_linha * len(linhas) + s(16)
                   + (s(52) if tem_de else 0) + s(120) + (s(46) if dados.get("fonte") else 0) + pad)
    alt_cta = s(108)
    mostrar_whats = whats and bool(config.WHATS_CANAL)
    alt_whats = s(140) if mostrar_whats else 0
    total = tam_logo + s(50) + altura_card + s(44) + alt_cta + alt_whats
    livre = altura - base_segura - topo - total
    if livre < 0:  # título comprido: diminui a foto para caber tudo
        corte = min(-livre, altura_foto - s(300))
        altura_foto -= corte
        altura_card -= corte
        livre += corte
    inicio = topo + max(0, livre // 2)

    # Cabeçalho: logo + nome
    x0 = int((largura - (tam_logo + s(28) + largura_marca)) / 2)
    logo = _logo(tam_logo)
    if logo:
        d.ellipse([x0 - 6, inicio - 6, x0 + tam_logo + 5, inicio + tam_logo + 5], fill=PRETO)
        img.alpha_composite(logo, (x0, inicio))
    d.text((x0 + tam_logo + s(28), inicio + tam_logo // 2), config.MARCA.upper(), font=f_marca, fill=PRETO, anchor="lm")

    # Cartão branco com a oferta
    card_y0 = inicio + tam_logo + s(50)
    card_y1 = card_y0 + altura_card

    sombra = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sombra).rounded_rectangle([card_x0, card_y0 + 24, card_x1, card_y1 + 24], radius=44, fill=(23, 23, 26, 70))
    img.alpha_composite(sombra.filter(ImageFilter.GaussianBlur(28)))
    d.rounded_rectangle([card_x0, card_y0, card_x1, card_y1], radius=44, fill=BRANCO)

    y = card_y0 + pad
    foto = _arredondar(_foto_produto(imagem_bytes, interno, altura_foto), 28)
    img.alpha_composite(foto, (card_x0 + pad, y))
    y += altura_foto + s(32)

    for linha in linhas:
        d.text((card_x0 + pad, y), linha, font=f_titulo, fill=PRETO)
        y += altura_linha
    y += s(16)

    if tem_de:
        texto_de = f"De {reais(original)}"
        d.text((card_x0 + pad, y), texto_de, font=f_de, fill=CINZA)
        larg = f_de.getlength(texto_de)
        meio = y + s(26)
        d.line([(card_x0 + pad, meio), (card_x0 + pad + larg, meio)], fill=CINZA, width=max(3, s(4)))
        y += s(52)

    texto_preco = reais(dados["preco"])
    d.text((card_x0 + pad, y + s(60)), texto_preco, font=f_preco, fill=PRETO, anchor="lm")
    if dados.get("desconto"):
        badge = f"{int(dados['desconto'])}% OFF"
        bx = card_x0 + pad + f_preco.getlength(texto_preco) + s(28)
        bl = f_badge.getlength(badge) + s(44)
        if bx + bl <= card_x1 - pad:
            d.rounded_rectangle([bx, y + s(28), bx + bl, y + s(92)], radius=s(16), fill=PRETO)
            d.text((bx + bl / 2, y + s(60)), badge, font=f_badge, fill=AMARELO, anchor="mm")
    y += s(120)

    if dados.get("fonte"):
        d.text((card_x0 + pad, y), f"na {dados['fonte']}", font=f_loja, fill=CINZA)

    # Chamada no rodapé
    arroba = _arroba()
    cy0 = card_y1 + s(44)
    if mostrar_whats:
        _chamada_whats(img, cy0, largura, alt_cta, alt_whats, arroba, s)
    else:
        chamada = f"Link no canal  {arroba}" if arroba else "Link no canal do Telegram"
        f_cta = _fonte("black", s(48))
        larg_cta = f_cta.getlength(chamada) + s(110)
        cx0 = (largura - larg_cta) / 2
        d.rounded_rectangle([cx0, cy0, cx0 + larg_cta, cy0 + alt_cta], radius=alt_cta // 2, fill=PRETO)
        d.text((largura / 2, cy0 + alt_cta / 2), chamada, font=f_cta, fill=AMARELO, anchor="mm")

    saida = io.BytesIO()
    img.convert("RGB").save(saida, "PNG", optimize=True)
    return saida.getvalue()


EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")


def gerar(dados, imagem_bytes=None, whats=True):
    """Retorna [(nome_arquivo, bytes_png), ...] com Story e Feed.

    whats=False tira a linha do canal do WhatsApp (usado nas artes que vão pro próprio canal do WhatsApp).
    """
    dados = dict(dados)
    # A fonte não tem emoji: tira do título para não aparecer quadradinho.
    dados["titulo"] = re.sub(r"\s{2,}", " ", EMOJI.sub("", dados.get("titulo") or "")).strip(" -–|") or "Oferta"
    titulo = re.sub(r"[^\w]+", "-", dados.get("titulo", "oferta").lower()).strip("-")[:40] or "oferta"
    story = _desenhar(dados, imagem_bytes, 1080, 1920, topo=250, altura_foto=560, base_segura=320, whats=whats)
    feed = _desenhar(dados, imagem_bytes, 1080, 1350, topo=60, altura_foto=420, base_segura=60, escala=0.9, whats=whats)
    return [(f"story-{titulo}.png", story), (f"feed-{titulo}.png", feed)]
