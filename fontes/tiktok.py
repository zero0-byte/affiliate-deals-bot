"""Manda o vídeo diário pro TikTok pela API oficial (Content Posting API). Dois modos (TIKTOK_MODO no .env):

- rascunho (padrão): o vídeo chega como notificação no app; você escolhe um som, cola a legenda e publica.
- privado: o robô posta direto, JÁ COM TÍTULO E HASHTAGS, como "Somente eu" (regra do TikTok para apps não
  aprovados). Você abre o vídeo no app › ⋯ › Configurações de privacidade › Todos. Atenção: o TikTok só aceita
  isso se a CONTA estiver como privada.
- manual: o robô não fala com o TikTok. Manda o vídeo e a legenda no seu Telegram, você posta pelo app (+ ›
  Carregar). Não precisa conectar a conta. As ofertas usadas também não se repetem.

Conectar a conta (uma vez só): clique em conectar-tiktok.bat. Ele abre o navegador, você autoriza, e o robô
guarda a chave em tiktok-token.json (na mesma pasta do .env). A chave se renova sozinha por 1 ano.
"""
import asyncio
import hashlib
import http.server
import json
import secrets
import time
import urllib.parse
import webbrowser
from pathlib import Path

import httpx

import config

API = "https://open.tiktokapis.com/v2"
AUTORIZAR = "https://www.tiktok.com/v2/auth/authorize/"
PORTA = 3455
REDIRECT = f"http://localhost:{PORTA}/callback/"  # cadastre exatamente este endereço no app do TikTok
ARQ_TOKEN = config.PASTA_DADOS / "tiktok-token.json"
MB = 1024 * 1024


class ErroTikTok(Exception):
    pass


def ativo():
    if config.TIKTOK_MODO == "manual":
        return config.TIKTOK
    return config.TIKTOK and bool(config.TIKTOK_CLIENT_KEY and config.TIKTOK_CLIENT_SECRET) and ARQ_TOKEN.is_file()


# ------------------------------------------------------------------ chave de acesso

def _salvar(dados):
    agora = time.time()
    ARQ_TOKEN.write_text(json.dumps({
        "access_token": dados["access_token"],
        "refresh_token": dados["refresh_token"],
        "expira": agora + int(dados.get("expires_in", 86400)) - 300,
        "refresh_expira": agora + int(dados.get("refresh_expires_in", 31536000)),
        "open_id": dados.get("open_id"),
        "scope": dados.get("scope", ""),
    }, indent=2), encoding="utf-8")


def _pedir_token(campos):
    r = httpx.post(f"{API}/oauth/token/", timeout=30, data={
        "client_key": config.TIKTOK_CLIENT_KEY, "client_secret": config.TIKTOK_CLIENT_SECRET, **campos})
    dados = r.json()
    if "access_token" not in dados:
        raise ErroTikTok(dados.get("error_description") or dados.get("error") or f"HTTP {r.status_code}")
    _salvar(dados)
    return dados["access_token"]


def _token():
    """Chave válida; renova sozinha quando vence (a cada 24h)."""
    if not ARQ_TOKEN.is_file():
        raise ErroTikTok("conta do TikTok não conectada: clique em conectar-tiktok.bat")
    t = json.loads(ARQ_TOKEN.read_text(encoding="utf-8"))
    if time.time() < t["expira"]:
        return t["access_token"]
    if time.time() > t["refresh_expira"]:
        raise ErroTikTok("a conexão com o TikTok venceu (1 ano): clique em conectar-tiktok.bat de novo")
    return _pedir_token({"grant_type": "refresh_token", "refresh_token": t["refresh_token"]})


def _escopos():
    return "user.info.basic,video.upload" + (",video.publish" if config.TIKTOK_MODO == "privado" else "")


def conectar():
    """Abre o navegador para você autorizar o robô na sua conta do TikTok."""
    if not (config.TIKTOK_CLIENT_KEY and config.TIKTOK_CLIENT_SECRET):
        raise SystemExit("Preencha TIKTOK_CLIENT_KEY e TIKTOK_CLIENT_SECRET no .env (veja o GUIA-TIKTOK.md).")
    verificador = secrets.token_urlsafe(64)[:96]
    estado = secrets.token_urlsafe(16)
    url = AUTORIZAR + "?" + urllib.parse.urlencode({
        "client_key": config.TIKTOK_CLIENT_KEY, "scope": _escopos(), "response_type": "code",
        "redirect_uri": REDIRECT, "state": estado,
        "code_challenge": hashlib.sha256(verificador.encode()).hexdigest(), "code_challenge_method": "S256"})
    recebido = {}

    class Retorno(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            recebido.update({k: v[0] for k, v in q.items()})
            ok = "code" in recebido and recebido.get("state") == estado
            msg = "Pronto! Pode fechar esta aba e voltar pro robô." if ok else "Não deu certo. Tente de novo."
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"<h2 style='font-family:sans-serif'>{msg}</h2>".encode())

        def log_message(self, *a):
            pass

    with http.server.HTTPServer(("127.0.0.1", PORTA), Retorno) as srv:
        print("Abrindo o navegador... entre na conta do TikTok do canal e toque em Autorizar.")
        webbrowser.open(url)
        while "code" not in recebido and "error" not in recebido:
            srv.handle_request()
    if recebido.get("state") != estado or "code" not in recebido:
        raise SystemExit(f"Autorização cancelada ou inválida: {recebido.get('error_description') or recebido}")
    _pedir_token({"grant_type": "authorization_code", "code": recebido["code"], "redirect_uri": REDIRECT,
                  "code_verifier": verificador})
    print(f"\nConta conectada! A chave ficou guardada em {ARQ_TOKEN}.\n"
          "Agora coloque TIKTOK=1 no .env e ligue o robô de novo.")


# ------------------------------------------------------------------ envio

def _partes(tamanho):
    """Regras do TikTok: partes de 5 a 64 MB; a última leva a sobra. Abaixo de 5 MB vai tudo de uma vez."""
    if tamanho <= 64 * MB:
        return tamanho, 1
    parte = 10 * MB
    return parte, tamanho // parte


def _checar(r):
    try:
        dados = r.json()
    except ValueError:
        raise ErroTikTok(f"resposta inesperada do TikTok (HTTP {r.status_code})")
    erro = dados.get("error") or {}
    if erro.get("code") not in (None, "ok"):
        dicas = {
            "scope_not_authorized": "a conexão não tem permissão pra isso: ligue o Direct Post no Sandbox, "
                                    "clique em Apply changes e rode o conectar-tiktok.bat de novo",
            "spam_risk_too_many_pending_share": "tem 5 rascunhos esperando no app: publique ou apague alguns",
            "spam_risk_too_many_posts": "o TikTok bloqueou mais posts hoje (limite diário)",
            "access_token_invalid": "a conexão expirou: rode o conectar-tiktok.bat de novo",
            "unaudited_client_can_only_post_to_private_accounts":
                "o TikTok só deixa postar direto em conta privada. Coloque TIKTOK_MODO=manual no .env",
        }
        codigo = erro.get("code")
        raise ErroTikTok(dicas.get(codigo) or f"{codigo}: {erro.get('message')}")
    return dados.get("data") or {}


def _cabecalho(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}


async def _subir(c, token, endpoint, caminho, extra=None):
    """Inicia o envio no endpoint e sobe o arquivo em partes. Devolve o publish_id."""
    tamanho = Path(caminho).stat().st_size
    parte, total = _partes(tamanho)
    r = await c.post(f"{API}{endpoint}", headers=_cabecalho(token), json={
        **(extra or {}),
        "source_info": {"source": "FILE_UPLOAD", "video_size": tamanho, "chunk_size": parte,
                        "total_chunk_count": total}})
    dados = _checar(r)
    with open(caminho, "rb") as arq:
        for k in range(total):
            ini = k * parte
            fim = tamanho - 1 if k == total - 1 else ini + parte - 1
            pedaco = arq.read(fim - ini + 1)
            up = await c.put(dados["upload_url"], content=pedaco, headers={
                "Content-Type": "video/mp4", "Content-Length": str(len(pedaco)),
                "Content-Range": f"bytes {ini}-{fim}/{tamanho}"})
            if up.status_code not in (200, 201, 206):
                raise ErroTikTok(f"falha ao enviar o vídeo (HTTP {up.status_code})")
    return dados["publish_id"]


async def enviar_rascunho(caminho):
    """Sobe o vídeo para a caixa de rascunhos do TikTok. Devolve o publish_id."""
    token = await asyncio.to_thread(_token)
    async with httpx.AsyncClient(timeout=120) as c:
        return await _subir(c, token, "/post/publish/inbox/video/init/", caminho)


async def postar_privado(caminho, legenda):
    """Posta direto com título e hashtags, como "Somente eu". Devolve o publish_id."""
    token = await asyncio.to_thread(_token)
    async with httpx.AsyncClient(timeout=120) as c:
        info = _checar(await c.post(f"{API}/post/publish/creator_info/query/", headers=_cabecalho(token)))
        if "SELF_ONLY" not in (info.get("privacy_level_options") or ["SELF_ONLY"]):
            raise ErroTikTok("o TikTok não ofereceu a opção 'Somente eu' para esta conta")
        post = {"title": legenda[:2200], "privacy_level": "SELF_ONLY",
                "disable_comment": bool(info.get("comment_disabled")),
                "disable_duet": bool(info.get("duet_disabled")), "disable_stitch": bool(info.get("stitch_disabled")),
                "video_cover_timestamp_ms": 1000, "brand_content_toggle": False, "brand_organic_toggle": False}
        return await _subir(c, token, "/post/publish/video/init/", caminho, {"post_info": post})


async def enviar(caminho, legenda):
    """Manda conforme o TIKTOK_MODO. Devolve o aviso pro seu Telegram."""
    if config.TIKTOK_MODO == "manual":
        return ("📲 Pra postar: salve o vídeo acima, abra o TikTok › + › Carregar, escolha um som em alta e "
                "cole a legenda que veio junto.")
    if config.TIKTOK_MODO == "privado":
        await postar_privado(caminho, legenda)
        return ("✅ Vídeo postado no TikTok com título e hashtags, como \"Somente eu\". Pra ficar público: abra o "
                "vídeo no app › ⋯ › Configurações de privacidade › Todos.")
    situacao_final = await _conferir(await enviar_rascunho(caminho))
    if situacao_final == "SEND_TO_USER_INBOX":
        return ("✅ O TikTok confirmou: o vídeo chegou na conta. No app, abra a Caixa de entrada › "
                "Notificações do sistema, toque no aviso do vídeo, cole a legenda acima e publique.")
    if situacao_final == "PUBLISH_COMPLETE":
        return "✅ O TikTok confirmou: vídeo publicado."
    return ("⏳ Vídeo enviado; o TikTok ainda está processando. Em alguns minutos deve chegar a notificação "
            "no app (Caixa de entrada › Notificações do sistema).")


async def _conferir(publish_id, espera=90):
    """Pergunta ao TikTok o que aconteceu com o envio, até ele dizer que chegou ou que falhou."""
    fim = time.time() + espera
    status = "PROCESSING_UPLOAD"
    while time.time() < fim:
        await asyncio.sleep(5)
        try:
            dados = await situacao(publish_id)
        except ErroTikTok:
            continue
        status = dados.get("status") or status
        if status == "FAILED":
            raise ErroTikTok(f"o TikTok recusou o vídeo ({dados.get('fail_reason') or 'sem motivo informado'})")
        if status in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"):
            return status
    return status


async def situacao(publish_id):
    """Status do envio (PROCESSING_UPLOAD, SEND_TO_USER_INBOX, FAILED...)."""
    token = await asyncio.to_thread(_token)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{API}/post/publish/status/fetch/", json={"publish_id": publish_id},
                         headers={"Authorization": f"Bearer {token}",
                                  "Content-Type": "application/json; charset=UTF-8"})
    return _checar(r)


if __name__ == "__main__":
    conectar()
