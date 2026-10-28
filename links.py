"""Troca links comuns pelos seus links de afiliado."""
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

import config
from fontes import aliexpress, shopee

PADRAO_URL = re.compile(r"https?://[^\s<>\"']+")
PADRAO_ASIN = re.compile(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})", re.I)


def _dominio(url):
    return urlparse(url).netloc.lower()


async def _expandir(url):
    """Segue o redirecionamento de links curtos (amzn.to etc.)."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as c:
            r = await c.head(url)
            return str(r.url)
    except httpx.HTTPError:
        return url


ML_GERADOR = "https://www.mercadolivre.com.br/affiliate-program/api/v2/affiliates/createLink"


class CookieMLExpirado(Exception):
    pass


async def link_mercadolivre(url):
    """Gera o link de afiliado (meli.la/...) pelo mesmo gerador do portal, usando a sua sessão."""
    headers = {
        "content-type": "application/json",
        "accept": "application/json",
        "origin": "https://www.mercadolivre.com.br",
        "referer": "https://www.mercadolivre.com.br/afiliados/linkbuilder",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "cookie": config.ML_COOKIE,
    }
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as c:
        r = await c.post(ML_GERADOR, headers=headers, json={"urls": [url], "tag": config.ML_TAG})
    if r.status_code in (301, 302, 401, 403):
        raise CookieMLExpirado()
    r.raise_for_status()
    try:
        dados = r.json()
        item = dados["urls"][0]
    except (ValueError, KeyError, IndexError):
        raise RuntimeError("resposta inesperada do Mercado Livre")
    link = item.get("short_url") or item.get("long_url")
    if not link:
        raise RuntimeError(item.get("error") or "o Mercado Livre não devolveu o link")
    return link


SHOPEE_GERADOR = "https://affiliate.shopee.com.br/api/v3/gql?q=batchCustomLink"
SHOPEE_QUERY = """
    query batchGetCustomLink($linkParams: [CustomLinkParam!], $sourceCaller: SourceCaller){
      batchCustomLink(linkParams: $linkParams, sourceCaller: $sourceCaller){
        shortLink
        longLink
        failCode
      }
    }
    """


class CookieShopeeExpirado(Exception):
    pass


def _csrf_do_cookie(cookie):
    m = re.search(r"(?:^|;\s*)csrftoken=([^;]+)", cookie or "")
    return m.group(1) if m else None


async def link_shopee_portal(url):
    """Gera o link de afiliado (s.shopee.com.br/...) pelo gerador "Link personalizado" do portal, com a sua sessão."""
    headers = {
        "content-type": "application/json",
        "accept": "application/json",
        "origin": "https://affiliate.shopee.com.br",
        "referer": "https://affiliate.shopee.com.br/offer/custom_link",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "cookie": config.SHOPEE_COOKIE,
    }
    csrf = _csrf_do_cookie(config.SHOPEE_COOKIE)
    if csrf:
        headers["x-csrftoken"] = csrf
    corpo = {
        "operationName": "batchGetCustomLink",
        "query": SHOPEE_QUERY,
        "variables": {"linkParams": [{"originalLink": url, "advancedLinkParams": {}}],
                      "sourceCaller": "CUSTOM_LINK_CALLER"},
    }
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as c:
        r = await c.post(SHOPEE_GERADOR, headers=headers, json=corpo)
    if r.status_code in (301, 302, 401, 403):
        raise CookieShopeeExpirado()
    r.raise_for_status()
    try:
        dados = r.json()
    except ValueError:
        raise RuntimeError("resposta inesperada da Shopee")
    erros = dados.get("errors") or []
    if erros:
        texto = str(erros).lower()
        if any(p in texto for p in ("login", "auth", "session", "unauthor", "permission")):
            raise CookieShopeeExpirado()
        raise RuntimeError(erros[0].get("message") if isinstance(erros[0], dict) else str(erros[0]))
    itens = ((dados.get("data") or {}).get("batchCustomLink")) or []
    if not itens:
        raise CookieShopeeExpirado()  # sem dados normalmente é sessão inválida
    item = itens[0]
    link = item.get("shortLink") or item.get("longLink")
    if not link:
        raise RuntimeError(f"a Shopee recusou o link (código {item.get('failCode')})")
    return link


AVISO_COOKIE_SHOPEE = ("🔑 O cookie da Shopee expirou ou está errado. Entre no portal de afiliados da Shopee, "
                       "copie um cookie novo para SHOPEE_COOKIE no .env e reinicie o robô.")


def link_kabum(url):
    """Deep link da Awin para a KaBuM!: leva direto para o produto, com o seu ID de afiliado."""
    from urllib.parse import quote
    limpo = url.split("#")[0]
    return (f"https://www.awin1.com/cread.php?awinmid={config.KABUM_AWIN_MID}"
            f"&awinaffid={config.AWIN_ID}&clickref=dealsbot&ued={quote(limpo, safe='')}")


def link_amazon(url, tag):
    """Coloca sua tag na URL da Amazon (e encurta para /dp/ASIN quando dá)."""
    partes = urlparse(url)
    asin = PADRAO_ASIN.search(partes.path)
    if asin:
        return f"https://{partes.netloc}/dp/{asin.group(1).upper()}?tag={tag}"
    query = dict(parse_qsl(partes.query))
    query["tag"] = tag
    return urlunparse(partes._replace(query=urlencode(query)))


async def converter(url):
    """Retorna (link_novo, aviso). aviso é None quando deu tudo certo."""
    dominio = _dominio(url)

    if "amzn.to" in dominio or "a.co" == dominio:
        url = await _expandir(url)
        dominio = _dominio(url)

    if "awin1.com" in dominio or "tidd.ly" in dominio:
        return url, None  # já é link de afiliado da Awin

    if "kabum.com.br" in dominio:
        if not config.AWIN_ID:
            return url, "AWIN_ID não configurado: link da KaBuM! ficou sem seu código de afiliado."
        return link_kabum(url), None

    if "amazon." in dominio:
        if not config.AMAZON_TAG:
            return url, "AMAZON_TAG não configurada: link da Amazon ficou sem sua tag."
        return link_amazon(url, config.AMAZON_TAG), None

    if "shopee." in dominio or "shp.ee" in dominio:
        if "s.shopee" in dominio:
            return url, None  # já é link curto de afiliado
        if config.shopee_ativo():  # API oficial (quando liberarem)
            try:
                return await shopee.gerar_link(url), None
            except Exception as e:  # noqa: BLE001
                return url, f"Não consegui converter o link da Shopee ({e})."
        if config.SHOPEE_COOKIE:  # gerador do portal, com a sua sessão
            try:
                return await link_shopee_portal(url), None
            except CookieShopeeExpirado:
                return url, AVISO_COOKIE_SHOPEE
            except Exception as e:  # noqa: BLE001
                return url, f"Não consegui converter o link da Shopee ({e})."
        return url, "Shopee não configurada: link ficou sem seu código de afiliado (coloque SHOPEE_COOKIE no .env)."

    if "aliexpress." in dominio:
        if "s.click.aliexpress" in dominio:
            return url, None  # já é link de afiliado
        if not config.aliexpress_ativo():
            return url, "AliExpress não configurado: link ficou sem seu código de afiliado."
        try:
            return await aliexpress.gerar_link(url), None
        except Exception as e:  # noqa: BLE001
            return url, f"Não consegui converter o link do AliExpress ({e})."

    if dominio.endswith("meli.la"):
        return url, None  # link curto de afiliado do Mercado Livre

    if "mercadoli" in dominio or "mercadolibre" in dominio:
        caminho = urlparse(url).path
        if caminho.startswith("/sec/") or caminho.startswith("/social/"):
            return url, None  # link de afiliado do Mercado Livre (/sec/ ou /social/)
        if not config.ml_ativo():
            return url, (
                "Mercado Livre não configurado (ML_COOKIE/ML_TAG): o link ficou sem seu código de afiliado."
            )
        try:
            return await link_mercadolivre(url), None
        except CookieMLExpirado:
            return url, (
                "🔑 O cookie do Mercado Livre expirou. Entre no portal de afiliados, copie um cookie novo "
                "para ML_COOKIE no .env e reinicie o robô. Esse link ficou SEM seu código de afiliado."
            )
        except Exception as e:  # noqa: BLE001
            return url, f"Não consegui converter o link do Mercado Livre ({e}). Ele ficou sem seu código."

    return url, None


async def converter_texto(texto):
    """Converte todos os links de um texto. Retorna (texto_novo, avisos)."""
    avisos = []
    novo = texto
    for url in dict.fromkeys(PADRAO_URL.findall(texto)):
        convertido, aviso = await converter(url)
        if aviso:
            avisos.append(aviso)
        novo = novo.replace(url, convertido)
    return novo, avisos


async def link_canal(dados, canal):
    """Link da oferta marcado com o canal (Sub ID), para saber de onde vêm as vendas.

    Por enquanto só a Shopee (API oficial) aceita Sub ID. Nas outras lojas, ou se der erro,
    devolve o link normal da oferta. O link gerado fica guardado em dados["links"][canal].
    """
    link = dados.get("link")
    url = dados.get("url_produto")
    if not (url and dados.get("fonte") == "Shopee" and config.shopee_ativo()):
        return link
    guardados = dados.setdefault("links", {})
    if guardados.get(canal):
        return guardados[canal]
    try:
        guardados[canal] = await shopee.gerar_link(url, canal)
        return guardados[canal]
    except Exception:  # noqa: BLE001 — sem o Sub ID, vai o link normal (a venda conta do mesmo jeito)
        return link


def url_shopee(texto):
    """Primeiro link comum da Shopee no texto (não o curto de afiliado)."""
    for url in PADRAO_URL.findall(texto or ""):
        dominio = _dominio(url)
        if "shopee." in dominio and "s.shopee" not in dominio:
            return url
    return None
