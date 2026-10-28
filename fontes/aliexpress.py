"""AliExpress Portals (afiliados) — API aberta com assinatura HMAC-SHA256."""
import hashlib
import hmac
import time

import httpx

import config
from modelos import Oferta

URL = "https://api-sg.aliexpress.com/sync"


def assinar(parametros, segredo):
    texto = "".join(f"{k}{parametros[k]}" for k in sorted(parametros))
    return hmac.new(segredo.encode(), texto.encode(), hashlib.sha256).hexdigest().upper()


async def _chamar(metodo, extras):
    params = {
        "app_key": config.ALI_APP_KEY,
        "method": metodo,
        "sign_method": "sha256",
        "timestamp": str(int(time.time() * 1000)),
        **{k: str(v) for k, v in extras.items()},
    }
    params["sign"] = assinar(params, config.ALI_APP_SECRET)
    async with httpx.AsyncClient(timeout=20) as cliente:
        r = await cliente.post(URL, data=params)
    r.raise_for_status()
    dados = r.json()
    if "error_response" in dados:
        raise RuntimeError(f"AliExpress respondeu com erro: {dados['error_response']}")
    chave = metodo.replace(".", "_") + "_response"
    resp = dados[chave]["resp_result"]
    if str(resp.get("resp_code")) != "200":
        raise RuntimeError(f"AliExpress: {resp.get('resp_msg')}")
    return resp.get("result") or {}


def _num(valor):
    try:
        return float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return None


async def buscar(palavra, quantidade=20):
    resultado = await _chamar(
        "aliexpress.affiliate.hotproduct.query",
        {
            "keywords": palavra,
            "page_size": quantidade,
            "target_currency": "BRL",
            "target_language": "PT",
            "ship_to_country": "BR",
            "tracking_id": config.ALI_TRACKING_ID,
        },
    )
    produtos = (resultado.get("products") or {}).get("product") or []
    ofertas = []
    for p in produtos:
        preco = _num(p.get("target_sale_price"))
        link = p.get("promotion_link")
        if not preco or not link:
            continue
        desconto = int(_num(str(p.get("discount", "0")).rstrip("%")) or 0)
        ofertas.append(
            Oferta(
                fonte="AliExpress",
                id=str(p.get("product_id")),
                titulo=(p.get("product_title") or "").strip(),
                preco=preco,
                preco_original=_num(p.get("target_original_price")),
                desconto=desconto,
                imagem=p.get("product_main_image_url"),
                link=link,
                vendas=p.get("lastest_volume"),
            )
        )
    return ofertas


async def gerar_link(url_produto):
    resultado = await _chamar(
        "aliexpress.affiliate.link.generate",
        {
            "promotion_link_type": 0,
            "source_values": url_produto,
            "tracking_id": config.ALI_TRACKING_ID,
        },
    )
    links = (resultado.get("promotion_links") or {}).get("promotion_link") or []
    if not links:
        raise RuntimeError("AliExpress não gerou link para esse produto.")
    return links[0]["promotion_link"]
