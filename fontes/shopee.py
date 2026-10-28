"""Shopee Affiliate Open API (Brasil) — GraphQL com assinatura SHA256."""
import hashlib
import json
import time

import httpx

import config
from modelos import Oferta

URL = "https://open-api.affiliate.shopee.com.br/graphql"

# sortType: 1=relevância, 2=mais vendidos, 3=maior preço, 4=menor preço, 5=maior comissão
ORDEM_MAIS_VENDIDOS = 2


def assinar(app_id, segredo, payload, timestamp):
    return hashlib.sha256(f"{app_id}{timestamp}{payload}{segredo}".encode()).hexdigest()


async def _chamar(query):
    payload = json.dumps({"query": query}, separators=(",", ":"), ensure_ascii=False)
    ts = int(time.time())
    assinatura = assinar(config.SHOPEE_APP_ID, config.SHOPEE_SECRET, payload, ts)
    headers = {
        "Authorization": f"SHA256 Credential={config.SHOPEE_APP_ID}, Timestamp={ts}, Signature={assinatura}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=20) as cliente:
        r = await cliente.post(URL, content=payload.encode(), headers=headers)
    r.raise_for_status()
    dados = r.json()
    if dados.get("errors"):
        raise RuntimeError(f"Shopee respondeu com erro: {dados['errors']}")
    return dados["data"]


def _num(valor):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


async def buscar(palavra, quantidade=20, ordem=ORDEM_MAIS_VENDIDOS):
    query = (
        "{ productOfferV2(keyword: %s, sortType: %d, page: 1, limit: %d) {"
        " nodes { itemId productName priceMin priceDiscountRate imageUrl offerLink productLink sales }"
        " } }" % (json.dumps(palavra, ensure_ascii=False), ordem, quantidade)
    )
    dados = await _chamar(query)
    ofertas = []
    for n in dados["productOfferV2"]["nodes"]:
        preco = _num(n.get("priceMin"))
        if not preco or not n.get("offerLink"):
            continue
        desconto = int(_num(n.get("priceDiscountRate")) or 0)
        original = round(preco / (1 - desconto / 100), 2) if 0 < desconto < 100 else None
        ofertas.append(
            Oferta(
                fonte="Shopee",
                id=str(n["itemId"]),
                titulo=n.get("productName", "").strip(),
                preco=preco,
                preco_original=original,
                desconto=desconto,
                imagem=n.get("imageUrl"),
                link=n["offerLink"],
                vendas=n.get("sales"),
                url_produto=n.get("productLink"),
            )
        )
    return ofertas


async def gerar_link(url_produto, sub_id="telegram"):
    """Transforma um link comum da Shopee no seu link curto de afiliado.

    sub_id marca de qual canal veio o clique (telegram, whats, face, insta...). No painel de afiliados
    da Shopee, o relatório de conversões mostra o Sub ID de cada venda.
    """
    query = (
        '{ generateShortLink(input: {originUrl: %s, subIds: [%s]}) { shortLink } }'
        % (json.dumps(url_produto), json.dumps(sub_id))
    )
    # generateShortLink é uma mutation
    dados = await _chamar("mutation " + query)
    return dados["generateShortLink"]["shortLink"]
