"""KaBuM!: lê título, preço e foto da página do produto (tentativa; se o site bloquear, você manda foto + preço)."""
import json
import re

import httpx
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
PADRAO_ID = re.compile(r"kabum\.com\.br/produto/(\d+)", re.I)


def _numero(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _produto_jsonld(sopa):
    for bloco in sopa.select('script[type="application/ld+json"]'):
        try:
            dado = json.loads(bloco.string or "")
        except ValueError:
            continue
        for item in dado if isinstance(dado, list) else [dado]:
            if isinstance(item, dict) and item.get("@type") == "Product":
                return item
    return None


def ler_produto(html):
    sopa = BeautifulSoup(html, "html.parser")
    prod = _produto_jsonld(sopa) or {}
    titulo = prod.get("name")
    if not titulo:
        og = sopa.select_one('meta[property="og:title"]')
        titulo = og.get("content") if og else None
    imagem = prod.get("image")
    if isinstance(imagem, list):
        imagem = imagem[0] if imagem else None
    if not imagem:
        og = sopa.select_one('meta[property="og:image"]')
        imagem = og.get("content") if og else None
    ofertas = prod.get("offers") or {}
    if isinstance(ofertas, list):
        ofertas = ofertas[0] if ofertas else {}
    preco = _numero(ofertas.get("price") or ofertas.get("lowPrice"))
    # preço "de" (antigo) costuma vir no JSON da página
    original = None
    m = re.search(r'"(?:oldPrice|priceOld|price_old|oldPriceWithDiscount)"\s*:\s*"?([\d.]+)', html)
    if m:
        original = _numero(m.group(1))
    if not (titulo and preco):
        return None
    desconto = round((1 - preco / original) * 100) if original and original > preco else 0
    return {"titulo": titulo.strip(), "preco": preco, "preco_original": original if desconto else None,
            "desconto": desconto, "imagem": imagem, "fonte": "KaBuM!"}


async def dados_produto(url):
    headers = {"user-agent": UA, "accept-language": "pt-BR,pt;q=0.9"}
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=headers) as c:
        r = await c.get(url)
    r.raise_for_status()
    return ler_produto(r.text)
