"""Publica na sua Página do Facebook pela API oficial (Graph API)."""
import httpx

import config

API = "https://graph.facebook.com/v21.0"


async def postar_foto(imagem_png, legenda):
    """Publica uma foto com legenda na Página. Retorna o id do post."""
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(
            f"{API}/{config.FB_PAGE_ID}/photos",
            data={"caption": legenda, "access_token": config.FB_PAGE_TOKEN},
            files={"source": ("oferta.png", imagem_png, "image/png")},
        )
    dados = r.json()
    if "error" in dados:
        raise RuntimeError(dados["error"].get("message", "erro do Facebook"))
    return dados.get("post_id") or dados.get("id")
