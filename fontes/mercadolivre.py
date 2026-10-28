"""Mercado Livre: lê a página de ofertas e as páginas de produto usando a sua sessão (ML_COOKIE).

Sem a sessão, o ML devolve uma página de verificação em vez do conteúdo,
então tudo aqui depende do mesmo cookie usado para gerar os links de afiliado.
"""
import json
import logging
import re

import httpx
from bs4 import BeautifulSoup

import config
from modelos import Oferta

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
PADRAO_ID = re.compile(r"(MLBU?-?\d{6,})", re.I)  # aceita MLB123456 e MLB-123456


def _id(achado):
    """'mlb-123456' -> 'MLB123456'"""
    return achado.group(1).upper().replace("-", "")


def _link_produto(codigo):
    """'MLB123456' -> https://produto.mercadolivre.com.br/MLB-123456"""
    return f"https://produto.mercadolivre.com.br/{codigo[:3]}-{codigo[3:]}" if codigo.startswith("MLB") \
        and codigo[3:].isdigit() else f"https://www.mercadolivre.com.br/p/{codigo}"
log = logging.getLogger("robo")
PADRAO_ARIA = re.compile(r"(\d[\d.]*)\s*rea(?:is|les)(?:\s*c(?:om|on)\s*(\d{1,2})\s*centavos)?", re.I)


class SessaoMLInvalida(Exception):
    """O ML devolveu a página de verificação: o cookie expirou ou está errado."""


# Cabeçalhos iguais aos do Chrome no Windows (o ML barra pedidos que não parecem navegador)
CABECALHOS = {
    "user-agent": UA,
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "upgrade-insecure-requests": "1",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
}


def _barrado(html):
    """True se o ML mostrou a página "Segurança"/verificação em vez do conteúdo."""
    if len(html) < 20000:
        return True
    titulo = re.search(r"<title[^>]*>(.*?)</title>", html[:20000], re.I | re.S)
    titulo = titulo.group(1).lower() if titulo else ""
    return any(p in titulo for p in ("segurança", "seguranca", "verifica", "captcha", "login", "entrar"))


async def _baixar_como_chrome(url, cookie):
    """Usa o curl_cffi, que se apresenta igual ao Chrome de verdade. None se ele não estiver instalado."""
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None
    cabecalhos = dict(CABECALHOS)
    cabecalhos.pop("user-agent")  # o curl_cffi já manda o do Chrome que ele imita
    if cookie:
        cabecalhos["cookie"] = cookie
    async with AsyncSession(impersonate="chrome", timeout=25) as s:
        r = await s.get(url, headers=cabecalhos, allow_redirects=True)
    r.raise_for_status()
    return r.text


async def _baixar_httpx(url, cookie):
    cabecalhos = dict(CABECALHOS, cookie=cookie) if cookie else dict(CABECALHOS)
    async with httpx.AsyncClient(timeout=25, follow_redirects=True, headers=cabecalhos) as c:
        r = await c.get(url)
    r.raise_for_status()
    return r.text


async def _baixar(url):
    """Baixa a página do ML. Tenta como Chrome SEM o seu cookie primeiro (a página de ofertas é pública, assim
    a leitura não fica ligada à sua conta); só se precisar usa o cookie e, por último, o jeito antigo."""
    tentativas = [(_baixar_como_chrome, ""), (_baixar_como_chrome, config.ML_COOKIE), (_baixar_httpx, config.ML_COOKIE)]
    for funcao, cookie in tentativas:
        try:
            html = await funcao(url, cookie)
        except Exception as e:  # noqa: BLE001
            log.info("Mercado Livre: %s%s falhou (%s)", funcao.__name__, " sem cookie" if not cookie else "", e)
            continue
        if html is None:
            continue
        if not _barrado(html):
            return html
        log.info("Mercado Livre: página \"Segurança\" (%s%s)", funcao.__name__, " sem cookie" if not cookie else "")
    raise SessaoMLInvalida()


def _valor_aria(texto):
    """'71 reais com 65 centavos' / 'Antes: 1499 reais' -> 71.65 / 1499.0"""
    if not texto:
        return None
    m = PADRAO_ARIA.search(texto)
    if not m:
        return None
    inteiro = float(m.group(1).replace(".", ""))
    return inteiro + (int(m.group(2)) / 100 if m.group(2) else 0)


def _imagem_grande(url):
    if not url:
        return None
    url = url.split("?")[0]
    codigo = re.search(r"D_(?:N?Q)_NP_(?:2X_)?(\d+-ML[A-Z]?\d+_\d+)", url)
    if codigo:  # monta a versão grande em JPG (o Telegram e as artes lidam melhor com JPG)
        return f"https://http2.mlstatic.com/D_NQ_NP_{codigo.group(1)}-O.jpg"
    url = url.replace("D_Q_NP_2X_", "D_NQ_NP_").replace("D_Q_NP_", "D_NQ_NP_")
    return re.sub(r"\.webp$", ".jpg", url)


def _limpar_link(href):
    return href.split("?")[0].split("#")[0] if href else None


def _url_completa(url):
    if not url:
        return None
    url = url.replace("\\u002F", "/")
    if url.startswith("//"):
        return "https:" + url
    if not url.startswith("http"):
        return "https://" + url
    return url


def _ofertas_do_json(html):
    """Plano B: o ML também manda os cards como JSON ("polycard") dentro da página. Lê de lá."""
    decodificador = json.JSONDecoder()
    ofertas, vistos = [], set()
    for m in re.finditer(r'\{\s*"metadata"\s*:\s*\{', html):
        try:
            card, _ = decodificador.raw_decode(html, m.start())
        except ValueError:
            continue
        meta = card.get("metadata") or {}
        ident = PADRAO_ID.search(str(meta.get("id") or meta.get("url") or ""))
        if not ident or _id(ident) in vistos:
            continue
        titulo, preco, original = None, None, None
        for comp in card.get("components") or []:
            if comp.get("type") == "title":
                titulo = (comp.get("title") or {}).get("text")
            elif comp.get("type") == "price":
                p = comp.get("price") or {}
                preco = (p.get("current_price") or {}).get("value")
                original = (p.get("previous_price") or {}).get("value")
        if not (titulo and preco):
            continue
        fotos = (card.get("pictures") or {}).get("pictures") or []
        imagem = f"https://http2.mlstatic.com/D_NQ_NP_{fotos[0]['id']}-O.jpg" if fotos and fotos[0].get("id") else None
        link = _url_completa(meta.get("url")) or _link_produto(_id(ident))
        try:
            preco, original = float(preco), float(original) if original else None
        except (TypeError, ValueError):
            continue
        desconto = round((1 - preco / original) * 100) if original and original > preco else 0
        vistos.add(_id(ident))
        ofertas.append(Oferta(fonte="Mercado Livre", id=_id(ident), titulo=titulo, preco=preco,
                              preco_original=original if desconto else None, desconto=desconto, imagem=imagem,
                              link=_limpar_link(link)))
    return ofertas


def _ofertas_dos_links(sopa):
    """Plano C: qualquer link de produto com preço perto (layout novo que o robô ainda não conhece)."""
    ofertas, vistos = [], set()
    for a in sopa.select("a[href]"):
        link = _limpar_link(a.get("href"))
        ident = PADRAO_ID.search(link or "")
        titulo = a.get_text(" ", strip=True)
        if not ident or _id(ident) in vistos or len(titulo) < 15:
            continue
        caixa = a
        for _ in range(6):  # sobe até achar o card com o preço
            caixa = caixa.parent
            if caixa is None or caixa.select_one("[aria-label*='reais'], [aria-label*='Reais']"):
                break
        if caixa is None:
            continue
        rotulos = [x.get("aria-label") or "" for x in caixa.select("[aria-label*='reais'], [aria-label*='Reais']")]
        antes = [_valor_aria(r) for r in rotulos if "antes" in r.lower()]
        agora = [_valor_aria(r) for r in rotulos if "antes" not in r.lower() and _valor_aria(r)]
        if not agora:
            continue
        preco, original = agora[0], (antes[0] if antes else None)
        desconto = round((1 - preco / original) * 100) if original and original > preco else 0
        img = caixa.select_one("img")
        vistos.add(_id(ident))
        ofertas.append(Oferta(fonte="Mercado Livre", id=_id(ident), titulo=titulo, preco=preco,
                              preco_original=original if desconto else None, desconto=desconto,
                              imagem=_imagem_grande(img and (img.get("data-src") or img.get("src"))), link=link))
    return ofertas


def ler_ofertas(html):
    """Extrai as ofertas da página de ofertas do ML (cards; se o layout mudar, JSON ou links da página)."""
    sopa = BeautifulSoup(html, "html.parser")
    ofertas = _ler_cards(sopa)
    if not ofertas:
        ofertas = _ofertas_do_json(html)
    if not ofertas:
        ofertas = _ofertas_dos_links(sopa)
    if not ofertas:
        titulo = sopa.title.get_text(" ", strip=True)[:80] if sopa.title else "sem título"
        log.warning("Mercado Livre: a página veio SEM ofertas (%d letras, título: \"%s\", cards: %d, "
                    "links de produto: %d). Pode ser verificação/login do ML ou layout novo.",
                    len(html), titulo, len(sopa.select(".poly-card")),
                    len({_id(m) for m in PADRAO_ID.finditer(html)}))
    return ofertas


def _ler_cards(sopa):
    ofertas = []
    for card in sopa.select(".poly-card"):
        a = card.select_one("a.poly-component__title") or card.select_one("a[href]")
        if not a:
            continue
        link = _limpar_link(a.get("href"))
        ident = PADRAO_ID.search(link or "")
        if not ident:  # link de rastreio (click1.mercadolivre...): o código do produto está em outro lugar do card
            ident = PADRAO_ID.search(str(card))
            link = _link_produto(_id(ident)) if ident else None
        atual = card.select_one(".poly-price__current .andes-money-amount")
        anterior = card.select_one(".andes-money-amount--previous")
        preco = _valor_aria(atual.get("aria-label") if atual else None)
        if not (link and ident and preco):
            continue
        original = _valor_aria(anterior.get("aria-label") if anterior else None)
        desconto = round((1 - preco / original) * 100) if original and original > preco else 0
        img = card.select_one("img")
        imagem = _imagem_grande(img and (img.get("data-src") or img.get("src")))
        ofertas.append(Oferta(
            fonte="Mercado Livre", id=_id(ident), titulo=a.get_text(" ", strip=True),
            preco=preco, preco_original=original, desconto=desconto, imagem=imagem, link=link,
        ))
    return ofertas


def ler_produto(html):
    """Extrai título, preço, preço antigo e foto da página de um produto."""
    sopa = BeautifulSoup(html, "html.parser")
    h1 = sopa.select_one("h1")
    titulo = h1.get_text(" ", strip=True) if h1 else None
    if not titulo:
        og = sopa.select_one('meta[property="og:title"]')
        titulo = og.get("content", "").split(" - R$")[0] if og else None
    preco = None
    meta_preco = sopa.select_one('meta[itemprop="price"]')
    if meta_preco and meta_preco.get("content"):
        try:
            preco = float(meta_preco["content"])
        except ValueError:
            preco = None
    if preco is None:
        atual = sopa.select_one(".ui-pdp-price__second-line .andes-money-amount")
        preco = _valor_aria(atual.get("aria-label") if atual else None)
    anterior = sopa.select_one(".ui-pdp-price__original-value") or sopa.select_one(".andes-money-amount--previous")
    original = _valor_aria(anterior.get("aria-label") if anterior else None)
    og_img = sopa.select_one('meta[property="og:image"]')
    imagem = _imagem_grande(og_img.get("content") if og_img else None)
    if not (titulo and preco):
        return None
    desconto = round((1 - preco / original) * 100) if original and original > preco else 0
    return {"titulo": titulo, "preco": preco, "preco_original": original if desconto else None,
            "desconto": desconto, "imagem": imagem, "fonte": "Mercado Livre"}


async def buscar_ofertas():
    """Lê todas as páginas de ofertas (ML_OFERTAS_URL). Se uma falhar, segue com as outras."""
    ofertas, vistas, erro, alguma_ok = [], set(), None, False
    for url in config.ML_OFERTAS_URLS:
        try:
            pagina = ler_ofertas(await _baixar(url))
            alguma_ok = True
        except Exception as e:  # noqa: BLE001 — uma página com problema não derruba as outras
            erro = e
            continue
        for o in pagina:
            if o.chave not in vistas:
                vistas.add(o.chave)
                ofertas.append(o)
    if not alguma_ok and erro:
        raise erro  # nenhuma página abriu (ex.: cookie vencido)
    return ofertas


async def dados_produto(url):
    return ler_produto(await _baixar(url))
