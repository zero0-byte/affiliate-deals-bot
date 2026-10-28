"""Publica no Instagram (conta Profissional ligada à Página do Facebook) pela API oficial (Graph API).

Como a API do Instagram só aceita imagem por URL pública, o robô primeiro sobe a arte na sua Página do
Facebook como foto NÃO publicada (não aparece pra ninguém) e usa o endereço dela. Vídeos (Reels) vão
direto do PC, pelo envio "resumable" da própria API.
"""
import asyncio
import io

import httpx
from PIL import Image

import config

API = "https://graph.facebook.com/v21.0"
UPLOAD = "https://rupload.facebook.com/ig-api-upload/v21.0"
_cache = {}


class ErroInstagram(Exception):
    pass


def _token():
    return config.IG_TOKEN or config.FB_PAGE_TOKEN


def _checar(r):
    try:
        dados = r.json()
    except ValueError:
        raise ErroInstagram(f"resposta inesperada do Instagram (HTTP {r.status_code})")
    if isinstance(dados, dict) and "error" in dados:
        raise ErroInstagram(dados["error"].get("message", "erro do Instagram"))
    return dados


async def conta_id():
    """ID da conta do Instagram: IG_USER_ID do .env ou descoberto pela Página do Facebook."""
    if config.IG_USER_ID:
        return config.IG_USER_ID
    if "ig" in _cache:
        return _cache["ig"]
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{API}/{config.FB_PAGE_ID}",
                        params={"fields": "instagram_business_account", "access_token": _token()})
    conta = (_checar(r).get("instagram_business_account") or {}).get("id")
    if not conta:
        raise ErroInstagram("a Página do Facebook não tem uma conta do Instagram Profissional conectada "
                            "(ou o token não tem a permissão instagram_basic).")
    _cache["ig"] = conta
    return conta


def _jpg(png):
    """O Instagram só aceita JPEG."""
    img = Image.open(io.BytesIO(png)).convert("RGB")
    saida = io.BytesIO()
    img.save(saida, "JPEG", quality=92)
    return saida.getvalue()


async def _url_publica(imagem):
    """Sobe a imagem na Página como foto não publicada e devolve o endereço público dela."""
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{API}/{config.FB_PAGE_ID}/photos",
                         data={"published": "false", "access_token": config.FB_PAGE_TOKEN},
                         files={"source": ("oferta.jpg", _jpg(imagem), "image/jpeg")})
        foto = _checar(r)["id"]
        r = await c.get(f"{API}/{foto}", params={"fields": "images", "access_token": config.FB_PAGE_TOKEN})
    imagens = _checar(r).get("images") or []
    if not imagens:
        raise ErroInstagram("o Facebook não devolveu o endereço da imagem")
    return max(imagens, key=lambda i: i.get("width", 0))["source"]


async def _esperar(c, container, limite_s):
    """Espera o Instagram terminar de processar a mídia."""
    for _ in range(max(1, limite_s // 5)):
        r = await c.get(f"{API}/{container}", params={"fields": "status_code,status", "access_token": _token()})
        dados = _checar(r)
        situacao = dados.get("status_code")
        if situacao == "FINISHED":
            return
        if situacao in ("ERROR", "EXPIRED"):
            raise ErroInstagram(f"o Instagram não aceitou a mídia ({dados.get('status') or situacao})")
        await asyncio.sleep(5)
    raise ErroInstagram("o Instagram demorou demais para processar a mídia")


async def _publicar(c, ig, container, limite_s):
    await _esperar(c, container, limite_s)
    r = await c.post(f"{API}/{ig}/media_publish", data={"creation_id": container, "access_token": _token()})
    return _checar(r).get("id")


async def postar_foto(imagem_png, legenda):
    """Post no feed (use a arte 4:5). Retorna o id do post."""
    ig = await conta_id()
    url = await _url_publica(imagem_png)
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{API}/{ig}/media",
                         data={"image_url": url, "caption": legenda, "access_token": _token()})
        return await _publicar(c, ig, _checar(r)["id"], 60)


async def postar_story(imagem_png):
    """Story com a arte 9:16. (A API não permite colocar figurinha de link no Story.)"""
    ig = await conta_id()
    url = await _url_publica(imagem_png)
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{API}/{ig}/media",
                         data={"image_url": url, "media_type": "STORIES", "access_token": _token()})
        return await _publicar(c, ig, _checar(r)["id"], 60)


async def postar_reels(caminho_video, legenda):
    """Reels a partir de um arquivo .mp4 do PC (envio direto, sem precisar de URL pública)."""
    ig = await conta_id()
    with open(caminho_video, "rb") as arq:
        video = arq.read()
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(f"{API}/{ig}/media", data={
            "media_type": "REELS", "upload_type": "resumable", "caption": legenda,
            "share_to_feed": "true", "access_token": _token()})
        container = _checar(r)["id"]
        r = await c.post(f"{UPLOAD}/{container}", content=video, headers={
            "Authorization": f"OAuth {_token()}", "offset": "0", "file_size": str(len(video))})
        _checar(r)
        return await _publicar(c, ig, container, 300)
