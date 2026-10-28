"""Ponte entre o painel (painel.pyw) e o robô que está rodando.

O painel não fala com o Telegram: ele escreve um pedido na tabela painel_comandos do robo.db
e o robô, a cada 3 segundos, faz o pedido e anota a resposta. O robô também anota a situação
(pausado, fila, lojas...) na tabela painel_estado, que o painel mostra na tela.

No bot.py, dentro do main(), antes do run_polling:
    try:
        import painel_ponte  # opcional: botões do painel (painel.pyw)
        painel_ponte.instalar(app, globals())
    except ImportError:
        pass
"""
import asyncio
import json
import logging
import sqlite3
import time
from types import SimpleNamespace

import banco
import config

log = logging.getLogger("robo")

VERSAO = 1
_g = {}  # funções e variáveis do bot.py
_rodando = set()  # tarefas em andamento (para não serem coletadas antes de terminar)


def _conectar():
    con = sqlite3.connect(config.ARQUIVO_BANCO, timeout=10)
    con.execute(
        "CREATE TABLE IF NOT EXISTS painel_comandos ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, comando TEXT, arg TEXT, criado REAL,"
        " status TEXT DEFAULT 'novo', resposta TEXT, terminado REAL)"
    )
    con.execute("CREATE TABLE IF NOT EXISTS painel_estado (nome TEXT PRIMARY KEY, valor TEXT)")
    return con


def _responder(cid, status, resposta):
    with _conectar() as con:
        con.execute("UPDATE painel_comandos SET status=?, resposta=?, terminado=? WHERE id=?",
                    (status, resposta, time.time(), cid))


# ---------------------------------------------------------------- o que cada botão faz

async def _buscar(bot, arg):
    novas = await _g["buscar_ofertas"](SimpleNamespace(bot=bot))
    return f"{novas or 0} ofertas novas na fila. Total na fila: {banco.tamanho_fila()}."


async def _postar(bot, arg):
    if callable(_g.get("descartar_velhas")):  # versões com a "fila fresca"
        _g["descartar_velhas"]()
    item = banco.proximo_da_fila()
    while item and item["chave"] and item["dados"] and not banco.desconto_confiavel(item["dados"]):
        banco.remover_da_fila(item["id"])  # desconto suspeito (preço "De" inflado)
        item = banco.proximo_da_fila()
    if not item:
        return "A fila está vazia."
    try:
        await _g["enviar"](bot, config.CANAL, item["texto"], item["foto"])
    except Exception as e:  # noqa: BLE001 — o item continua na fila
        raise RuntimeError(f"Não consegui publicar no canal ({e}). O post continua na fila.") from e
    banco.marcar_postado(item["chave"] or f"manual:{item['id']}", item["dados"])
    banco.remover_da_fila(item["id"])
    await _g["mandar_criativos"](bot, item["dados"])
    titulo = (item["dados"] or {}).get("titulo") or "post manual"
    return f"Publicado no canal: {titulo[:70]}"


async def _tiktok(bot, arg):
    if not _g["tiktok"].ativo():
        return f"TikTok: {_g['_status_tiktok']()}"
    formato = _g["_formato_do_dia"]()
    if formato == "achados":
        await _g["mandar_video"](bot, avisar_se_vazio=True, para_tiktok=True)
    else:
        await _g["mandar_casual"](bot, formato, para_tiktok=True, avisar_se_vazio=True)
    return "Vídeo enviado pros rascunhos do TikTok. A legenda chegou no seu Telegram."


async def _video(bot, arg):
    await _g["mandar_video"](bot, avisar_se_vazio=True)
    return "Vídeo \"3 achados\" enviado no seu Telegram."


async def _casual(bot, arg):
    formatos = _g["video_casual"].FORMATOS
    if arg not in formatos:
        return "Formato desconhecido."
    await _g["mandar_casual"](bot, arg, avisar_se_vazio=True)
    return f"Vídeo \"{_g['video_casual'].NOMES[arg]}\" enviado no seu Telegram."


async def _whats(bot, arg):
    await _g["mandar_para_whats"](bot, avisar_se_vazio=True)
    return "Kit do WhatsApp enviado no seu Telegram."


async def _face(bot, arg):
    await _g["postar_facebook"](bot, avisar_resultado=True)
    return "Pronto. O resultado do Facebook chegou no seu Telegram."


async def _insta(bot, arg):
    await _g["postar_instagram"](bot, avisar_resultado=True)
    return "Pronto. O resultado do Instagram chegou no seu Telegram."


def _definir_pausa(valor):
    if callable(_g.get("definir_pausa")):  # versões que lembram o /pausar depois de religar
        _g["definir_pausa"](valor)
    else:
        _g["estado"]["pausado"] = valor


async def _pausar(bot, arg):
    _definir_pausa(True)
    return "Robô pausado: buscas, posts e vídeos automáticos parados."


async def _retomar(bot, arg):
    _definir_pausa(False)
    return "Robô retomado."


async def _relatorio(bot, arg):
    if not callable(_g.get("mandar_relatorio")):
        return "Esta versão do robô ainda não tem o relatório semanal."
    await _g["mandar_relatorio"](bot)
    return "Relatório enviado no seu Telegram."


async def _limpar(bot, arg):
    banco.limpar_fila()
    return "Fila esvaziada."


COMANDOS = {
    "buscar": _buscar, "postar": _postar, "tiktok": _tiktok, "video": _video, "casual": _casual,
    "whats": _whats, "face": _face, "insta": _insta, "pausar": _pausar, "retomar": _retomar, "limpar": _limpar,
    "relatorio": _relatorio,
}


# ---------------------------------------------------------------- rotina de 3 em 3 segundos

async def _executar(bot, cid, comando, arg):
    try:
        resposta = await COMANDOS[comando](bot, arg)
        _responder(cid, "ok", resposta)
        log.info("Painel: %s — %s", comando, resposta)
    except Exception as e:  # noqa: BLE001 — erro num botão não pode derrubar o robô
        log.exception("Painel: erro em %s", comando)
        _responder(cid, "erro", str(e) or e.__class__.__name__)


def _situacao():
    g = _g
    lojas = {
        "Mercado Livre": bool(config.ml_ativo()),
        "Shopee": bool(config.shopee_ativo() or config.SHOPEE_COOKIE),
        "AliExpress": bool(config.aliexpress_ativo()),
        "Amazon": bool(config.AMAZON_TAG),
        "KaBuM!": bool(config.AWIN_ID),
    }
    canais = {}
    for nome, fn in [("TikTok", "_status_tiktok"), ("Instagram", "_status_insta"), ("Facebook", "_status_face")]:
        try:
            canais[nome] = g[fn]()
        except Exception as e:  # noqa: BLE001
            canais[nome] = f"(erro: {e})"
    canais["WhatsApp"] = ", ".join(f"{h}h" for h in config.WHATS_HORARIOS) or "desligado"
    return {
        "versao": VERSAO,
        "visto": time.time(),
        "pausado": bool(g["estado"].get("pausado")),
        "no_horario": bool(g["no_horario"]()),
        "horario": f"{config.HORA_INICIO}h às {config.HORA_FIM}h",
        "intervalo_post": config.INTERVALO_POST_MIN,
        "intervalo_busca": config.INTERVALO_BUSCA_MIN,
        "canal": config.CANAL,
        "lojas": lojas,
        "canais": canais,
        "formatos": {f: g["video_casual"].NOMES[f] for f in g["video_casual"].FORMATOS},
    }


async def _rotina(context):
    bot = context.bot
    try:
        with _conectar() as con:
            con.execute("INSERT OR REPLACE INTO painel_estado (nome, valor) VALUES ('robo', ?)",
                        (json.dumps(_situacao(), ensure_ascii=False),))
            # Pedidos com mais de 2 minutos (o robô estava desligado) não são feitos.
            con.execute("UPDATE painel_comandos SET status='expirado', terminado=? "
                        "WHERE status='novo' AND criado < ?", (time.time(), time.time() - 120))
            pedidos = con.execute("SELECT id, comando, arg FROM painel_comandos WHERE status='novo' ORDER BY id").fetchall()
            for cid, _, _ in pedidos:
                con.execute("UPDATE painel_comandos SET status='rodando' WHERE id=?", (cid,))
            # Limpa o histórico de pedidos com mais de 3 dias.
            con.execute("DELETE FROM painel_comandos WHERE criado < ?", (time.time() - 3 * 86400,))
    except sqlite3.Error as e:
        log.warning("Painel: não consegui ler os pedidos (%s)", e)
        return
    for cid, comando, arg in pedidos:
        if comando not in COMANDOS:
            _responder(cid, "erro", f"Comando desconhecido: {comando}")
            continue
        tarefa = asyncio.create_task(_executar(bot, cid, comando, arg or ""))
        _rodando.add(tarefa)
        tarefa.add_done_callback(_rodando.discard)


def instalar(app, globais):
    """Liga a ponte. `globais` = globals() do bot.py."""
    _g.update(globais)
    with _conectar() as con:  # pedidos que ficaram "rodando" quando o robô caiu
        con.execute("UPDATE painel_comandos SET status='erro', resposta='O robô foi desligado antes de terminar.' "
                    "WHERE status='rodando'")
    app.job_queue.run_repeating(_rotina, interval=3, first=2)
    log.info("Painel: botões ligados.")
