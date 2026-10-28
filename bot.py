"""Robô de promoções para Telegram.

- Busca ofertas na Shopee e no AliExpress (quando configurados) e coloca na fila.
- Publica uma oferta da fila no canal a cada INTERVALO_POST_MIN minutos, dentro do horário.
- Você (admin) pode mandar um link ou uma foto com legenda no privado do robô:
  ele troca os links pelos seus links de afiliado e pergunta se publica agora ou põe na fila.
- A cada oferta publicada, manda no seu privado a arte pronta pro Instagram (Story e Feed).
- Todo dia (VIDEO_HORA) e com /video, monta o vídeo "3 achados de hoje" com as melhores ofertas postadas.
- Nos horários de WHATS_HORARIOS (e com /whats), separa a melhor oferta e te manda a arte + o texto
  no formato do WhatsApp, pronto pra copiar e colar no canal.
- Nos horários de IG_HORARIOS (e com /insta), posta a melhor oferta no Instagram (feed + Story) e o
  vídeo diário vai pro Reels.
- Guarda o histórico de preços (selo "menor preço em X dias") e avisa na hora quando aparece uma oferta forte.
"""
# Usa os certificados do Windows/sistema (resolve "CERTIFICATE_VERIFY_FAILED" em redes com firewall).
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import asyncio
import logging
import time as _t
import uuid
from dataclasses import asdict
from datetime import datetime, time as hora_do_dia
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from html import escape

from telegram.constants import ParseMode
import httpx
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import banco
import config
import criativo
import formatar
import links
import video_achados
import video_casual
import voz
from fontes import aliexpress, facebook, instagram, kabum, mercadolivre, shopee, tiktok
from modelos import Oferta

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
# O agendador escreve cada tarefa em hora UTC (3h a mais) e enche o log: só mostra avisos e erros
logging.getLogger("apscheduler").setLevel(logging.WARNING)
log = logging.getLogger("robo")
ARQUIVO_LOG = config.PASTA_DADOS / "robo.log"


class _EsconderSegredos(logging.Filter):
    """Tira token e cookies do texto antes de gravar no arquivo de log (que você pode mandar pra alguém)."""
    SEGREDOS = [v for v in (config.TELEGRAM_TOKEN, config.ML_COOKIE, config.SHOPEE_COOKIE, config.SHOPEE_SECRET,
                            config.FB_PAGE_TOKEN, config.IG_TOKEN, config.TIKTOK_CLIENT_SECRET,
                            config.ALI_APP_SECRET, config.ELEVENLABS_API_KEY) if v and len(v) > 6]

    def filter(self, record):
        texto = record.getMessage()
        for segredo in self.SEGREDOS:
            texto = texto.replace(segredo, "***")
        record.msg, record.args = texto, None
        return True


def _log_em_arquivo():
    """Além da janela preta, grava o log em robo.log (junto do .env). O /log manda esse arquivo."""
    from logging.handlers import RotatingFileHandler
    try:
        arquivo = RotatingFileHandler(ARQUIVO_LOG, maxBytes=2_000_000, backupCount=1, encoding="utf-8")
    except OSError as e:
        log.warning("Não consegui criar o robo.log (%s)", e)
        return
    arquivo.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    arquivo.addFilter(_EsconderSegredos())
    logging.getLogger().addHandler(arquivo)

FUSO = ZoneInfo(config.FUSO)
estado = {"pausado": False, "indice_palavra": 0, "rodada": 0, "aviso_cookie": 0, "fb_dia": "", "fb_qtd": 0}

AVISO_COOKIE = ("🔑 O Mercado Livre mostrou a página \"Segurança\" em vez das ofertas. Se o cookie for antigo, "
                "copie um novo para ML_COOKIE no .env e reinicie o robô. Se o cookie for novo, é o ML barrando "
                "o robô: mande o log para quem te ajuda.")


# ---------------------------------------------------------------- utilidades

def eh_admin(update: Update):
    return config.ADMIN_ID and update.effective_user and update.effective_user.id == config.ADMIN_ID


def no_horario():
    hora = datetime.now(FUSO).hour
    return config.HORA_INICIO <= hora < config.HORA_FIM


def passa_no_filtro(o, parecidos=None):
    """parecidos: palavras dos títulos já na fila/postados (banco.titulos_recentes); evita o mesmo produto 2x."""
    if o.desconto < config.DESCONTO_MINIMO:
        return False
    if config.DESCONTO_MAXIMO and o.desconto > config.DESCONTO_MAXIMO:  # preço "De" inflado
        log.info("Descartada (desconto suspeito de %s%%): %s", o.desconto, o.titulo[:60])
        return False
    if config.PRECO_MAXIMO and o.preco > config.PRECO_MAXIMO:
        return False
    if banco.ja_visto(o.chave):
        return False
    if parecidos is not None:
        palavras = banco.palavras_do_titulo(o.titulo)
        if any(banco.parecido(palavras, p) for p in parecidos):
            log.info("Descartada (produto parecido já na fila ou postado): %s", o.titulo[:60])
            return False
    return True


def _anotar_titulo(parecidos, o):
    """Depois de pôr na fila, guarda o título pra não entrar outro anúncio do mesmo produto na mesma busca."""
    if parecidos is not None:
        parecidos.append(banco.palavras_do_titulo(o.titulo))


async def enviar(bot, destino, texto, foto=None):
    """Envia foto com legenda; se não der, manda só o texto."""
    if foto and len(texto) <= formatar.LIMITE_LEGENDA:
        try:
            await bot.send_photo(destino, foto, caption=texto, parse_mode=ParseMode.HTML)
            return
        except Exception as e:  # noqa: BLE001 — foto quebrada não pode travar o robô
            log.warning("Falha ao enviar foto (%s); enviando só texto.", e)
    await bot.send_message(destino, texto, parse_mode=ParseMode.HTML)


async def avisar_admin(bot, texto):
    if config.ADMIN_ID:
        try:
            await bot.send_message(config.ADMIN_ID, texto)
        except Exception:  # noqa: BLE001
            log.exception("Não consegui avisar o admin")


async def _baixar_imagem(bot, origem):
    """Baixa a foto do produto: URL da loja ou foto enviada no Telegram ("tg:<id>")."""
    if not origem:
        return None
    try:
        if origem.startswith("tg:"):
            arquivo = await bot.get_file(origem[3:])
            return bytes(await arquivo.download_as_bytearray())
        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as c:
            r = await c.get(origem)
            r.raise_for_status()
            return r.content
    except Exception as e:  # noqa: BLE001 — sem foto, a arte sai com espaço reservado
        log.warning("Não consegui baixar a foto do produto (%s)", e)
        return None


async def _com_retentativa(enviar_fn, tentativas=3):
    """Tenta enviar de novo se der timeout/erro de rede (rede lenta)."""
    from telegram.error import NetworkError, TimedOut
    for n in range(tentativas):
        try:
            return await enviar_fn()
        except (TimedOut, NetworkError):
            if n == tentativas - 1:
                raise
            await asyncio.sleep(3 * (n + 1))


def _inicio_do_dia():
    agora = datetime.now(FUSO)
    return agora.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


async def postar_facebook(bot, avisar_resultado=False):
    """Nos horários do Facebook: pega a MELHOR oferta das últimas 12h que ainda não foi pra página e posta.

    Se nenhuma oferta for boa o bastante (desconto < FB_DESCONTO_MINIMO), pula esse horário.
    """
    if not config.facebook_ativo():
        if avisar_resultado:
            await avisar_admin(bot, "A página do Facebook não está configurada (FB_PAGE_ID e FB_PAGE_TOKEN no .env).")
        return
    feitos = banco.postados_face_hoje(_inicio_do_dia())
    if feitos >= max(config.FB_POSTS_DIA, len(config.FB_HORARIOS)) and not avisar_resultado:
        return
    escolhida = banco.melhor_para_face(horas=12, desconto_minimo=config.FB_DESCONTO_MINIMO)
    if not escolhida:
        log.info("Facebook: nenhuma oferta boa o bastante agora; pulando este horário.")
        if avisar_resultado:
            await avisar_admin(bot, f"Nenhuma oferta das últimas 12h com {config.FB_DESCONTO_MINIMO}% OFF ou mais "
                                    "que ainda não foi pro Facebook.")
        return
    chave, dados = escolhida
    try:
        imagem = await _baixar_imagem(bot, dados.get("imagem"))
        arte = (await asyncio.to_thread(criativo.gerar, dados, imagem))[1][1]  # versão feed 4:5
        link = await links.link_canal(dados, "face")
        await facebook.postar_foto(arte, formatar.facebook(dados, link))
        banco.marcar_face(chave)
        log.info("Postado no Facebook: %s (%s%% OFF) — %d hoje", dados.get("titulo"), dados.get("desconto"), feitos + 1)
        if avisar_resultado:
            await avisar_admin(bot, f"✅ Postado na página: {dados.get('titulo')} ({dados.get('desconto') or 0}% OFF)")
    except Exception as e:  # noqa: BLE001
        log.error("Erro ao postar no Facebook: %s", e)
        await avisar_admin(bot, f"⚠️ Não consegui postar na página do Facebook: {e}")


async def postar_instagram(bot, avisar_resultado=False):
    """Nos horários do Instagram: posta a MELHOR oferta das últimas 12h no feed (e no Story)."""
    if not config.insta_ativo():
        if avisar_resultado:
            await avisar_admin(bot, "O Instagram não está ligado (INSTA=1 e o token da Página no .env).")
        return
    feitos = banco.postados_face_hoje(_inicio_do_dia(), coluna="insta")
    if feitos >= len(config.IG_HORARIOS) and not avisar_resultado:
        return
    escolhida = banco.melhor_para_face(horas=12, desconto_minimo=config.IG_DESCONTO_MINIMO, coluna="insta")
    if not escolhida:
        log.info("Instagram: nenhuma oferta boa o bastante agora; pulando este horário.")
        if avisar_resultado:
            await avisar_admin(bot, f"Nenhuma oferta das últimas 12h com {config.IG_DESCONTO_MINIMO}% OFF ou mais "
                                    "que ainda não foi pro Instagram.")
        return
    chave, dados = escolhida
    try:
        imagem = await _baixar_imagem(bot, dados.get("imagem"))
        artes = await asyncio.to_thread(criativo.gerar, dados, imagem)
        await instagram.postar_foto(artes[1][1], formatar.instagram(dados))  # feed 4:5
        banco.marcar_face(chave, coluna="insta")
        log.info("Postado no Instagram: %s — %d hoje", dados.get("titulo"), feitos + 1)
        if config.IG_STORIES:
            try:
                await instagram.postar_story(artes[0][1])  # story 9:16
            except Exception as e:  # noqa: BLE001 — o post do feed já foi
                log.warning("Story do Instagram falhou: %s", e)
        if avisar_resultado:
            await avisar_admin(bot, f"✅ Postado no Instagram: {dados.get('titulo')} ({dados.get('desconto') or 0}% OFF)")
    except Exception as e:  # noqa: BLE001
        log.error("Erro ao postar no Instagram: %s", e)
        await avisar_admin(bot, f"⚠️ Não consegui postar no Instagram: {e}")


async def mandar_criativos(bot, dados, forcar=False):
    """Gera a arte do Instagram da oferta e manda no privado do admin."""
    if not dados or not config.ADMIN_ID or not (config.CRIATIVOS or forcar):
        return
    try:
        imagem = await _baixar_imagem(bot, dados.get("imagem"))
        artes = await asyncio.to_thread(criativo.gerar, dados, imagem)
        for i, (nome, png) in enumerate(artes):
            legenda = "🎨 Arte pronta pro Instagram (Story e Feed)" if i == 0 else None
            await _com_retentativa(lambda: bot.send_document(
                config.ADMIN_ID, document=png, filename=nome, caption=legenda, write_timeout=180))
    except Exception as e:  # noqa: BLE001
        log.exception("Erro ao gerar a arte")
        await avisar_admin(bot, f"⚠️ Não consegui gerar a arte do Instagram: {e}")


async def mandar_video(bot, avisar_se_vazio=False, reels=False, para_tiktok=None):
    """Monta o vídeo "achados de hoje" com as melhores ofertas das últimas 24h."""
    if not config.ADMIN_ID:
        return
    ofertas = banco.melhores_postadas(horas=24, quantidade=3)
    if not ofertas:
        if avisar_se_vazio:
            await avisar_admin(bot, "Ainda não tem ofertas com preço postadas nas últimas 24h para montar o vídeo.")
        return
    try:
        imagens = [await _baixar_imagem(bot, o.get("imagem")) for o in ofertas]
        caminho = await asyncio.to_thread(video_achados.gerar, list(zip(ofertas, imagens)))
        try:
            with open(caminho, "rb") as arq:
                await bot.send_document(
                    config.ADMIN_ID, document=arq,
                    filename=f"achados-{datetime.now(FUSO):%Y-%m-%d}.mp4",
                    caption=f"🎬 Vídeo \"{len(ofertas)} achados de hoje\" pronto pro TikTok, Reels e Stories "
                            "(com narração e batida próprias, sem direitos autorais).",
                    read_timeout=120, write_timeout=120,
                )
            await bot.send_message(config.ADMIN_ID, f"<pre>{escape(formatar.legenda_tiktok(ofertas))}</pre>",
                                   parse_mode=ParseMode.HTML)
            if reels and config.insta_ativo() and config.IG_REELS:
                try:
                    await instagram.postar_reels(caminho, formatar.legenda_reels(ofertas))
                    await avisar_admin(bot, "✅ Vídeo de hoje postado no Reels do Instagram.")
                except Exception as e:  # noqa: BLE001
                    log.error("Erro ao postar o Reels: %s", e)
                    await avisar_admin(bot, f"⚠️ Não consegui postar o vídeo no Reels: {e}")
            if (reels if para_tiktok is None else para_tiktok) and tiktok.ativo() and _formatos_tiktok() == ["achados"]:
                try:
                    await avisar_admin(bot, await tiktok.enviar(caminho, formatar.legenda_tiktok(ofertas)))
                except Exception as e:  # noqa: BLE001
                    log.error("Erro ao enviar para o TikTok: %s", e)
                    await avisar_admin(bot, f"⚠️ Não consegui mandar o vídeo pro TikTok: {e}")
        finally:
            Path(caminho).unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001
        log.exception("Erro ao gerar o vídeo")
        await avisar_admin(bot, f"⚠️ Não consegui gerar o vídeo: {e}")


async def mandar_para_whats(bot, avisar_se_vazio=False):
    """Separa a melhor oferta ainda não usada e manda arte + texto prontos pro WhatsApp."""
    if not config.ADMIN_ID:
        return
    escolhida = banco.proxima_para_whats(horas=12)
    if not escolhida:
        if avisar_se_vazio:
            await avisar_admin(bot, "Não tem oferta nova (com preço e link) postada nas últimas 12h para o WhatsApp.")
        return
    chave, dados = escolhida
    try:
        imagem = await _baixar_imagem(bot, dados.get("imagem"))
        arte = None
        try:
            artes = await asyncio.to_thread(criativo.gerar, dados, imagem, False)  # sem o link do próprio WhatsApp
            arte = artes[1][1]  # versão feed (4:5), fica melhor no WhatsApp
        except Exception:  # noqa: BLE001 — sem arte, manda só o texto
            log.exception("Erro ao gerar a arte do WhatsApp")
        await bot.send_message(
            config.ADMIN_ID,
            "📲 <b>Oferta pro WhatsApp</b>\n1. Salve a imagem\n2. Toque no texto abaixo para copiar\n"
            "3. No canal, poste a imagem e cole o texto como legenda",
            parse_mode=ParseMode.HTML,
        )
        if arte:
            await _com_retentativa(lambda: bot.send_photo(config.ADMIN_ID, arte, write_timeout=180))
        link = await links.link_canal(dados, "whats")
        await bot.send_message(
            config.ADMIN_ID, f"<pre>{escape(formatar.whatsapp(dados, link))}</pre>",
            parse_mode=ParseMode.HTML, disable_web_page_preview=True,
        )
        banco.marcar_whats(chave)
    except Exception as e:  # noqa: BLE001
        log.exception("Erro ao preparar oferta do WhatsApp")
        await avisar_admin(bot, f"⚠️ Não consegui preparar a oferta do WhatsApp: {e}")


async def avisar_cookie_ml(bot):
    """Avisa sobre o cookie do ML no máximo a cada 6 horas."""
    if _t.time() - estado["aviso_cookie"] > 6 * 3600:
        estado["aviso_cookie"] = _t.time()
        await avisar_admin(bot, AVISO_COOKIE)


def combina_filtro_ml(titulo):
    if "*" in config.ML_FILTRO:
        return True
    t = (titulo or "").lower()
    return any(p in t for p in config.ML_FILTRO)


def anotar_precos(ofertas):
    """Calcula o selo de menor preço e guarda o preço de agora no histórico.

    Devolve {chave: histórico antes de agora}, usado para decidir se a oferta é forte.
    """
    antes = {}
    for o in ofertas:
        antes[o.chave] = banco.historico_preco(o.chave)
        o.selo = banco.selo_preco(o.chave, o.preco)
        banco.registrar_preco(o.chave, o.preco)
    return antes


def oferta_forte(o, historico):
    """Oferta fora da curva: desconto muito alto (mas ainda confiável) ou preço bem abaixo do menor já visto."""
    if config.ALERTA_DESCONTO and o.desconto >= config.ALERTA_DESCONTO:
        return f"{o.desconto}% OFF"
    if config.ALERTA_QUEDA and historico:
        menor, _maior, dias = historico
        if dias >= 7 and o.preco <= menor * (1 - config.ALERTA_QUEDA / 100):
            return f"{round((1 - o.preco / menor) * 100)}% abaixo do menor preço dos últimos {int(dias)} dias"
    return None


async def enfileirar_oferta(bot, o, historico=None):
    """Põe a oferta na fila. Se for forte, ela fura a fila e você recebe o alerta com o kit do WhatsApp."""
    motivo = oferta_forte(o, historico)
    alerta = (motivo and not banco.ja_alertado(o.chave)
              and banco.alertas_hoje(_inicio_do_dia()) < config.ALERTA_MAX_DIA)
    dados = asdict(o)
    banco.enfileirar(formatar.oferta(o), foto=o.imagem, chave=o.chave, dados=dados, prioridade=1 if alerta else 0)
    if not alerta:
        return
    agora_pode = no_horario()  # de madrugada o alerta espera o HORA_INICIO para não te acordar
    banco.marcar_alerta(o.chave, motivo, avisado=agora_pode)
    log.info("Oferta forte (%s): %s%s", motivo, o.titulo[:60], "" if agora_pode else " (aviso fica pra manhã)")
    if agora_pode:
        await mandar_alerta(bot, dados, motivo)


async def mandar_alerta(bot, dados, motivo):
    """Manda no seu privado o aviso de oferta forte com o kit do WhatsApp."""
    if not config.ADMIN_ID:
        return
    try:
        await bot.send_message(config.ADMIN_ID, f"🚨 <b>OFERTA FORTE</b> ({escape(motivo)})\n"
                               "Ela vai pro Telegram no próximo horário. Abaixo, o kit pra postar já no WhatsApp:",
                               parse_mode=ParseMode.HTML)
        imagem = await _baixar_imagem(bot, dados.get("imagem"))
        arte = (await asyncio.to_thread(criativo.gerar, dados, imagem, False))[1][1]
        await _com_retentativa(lambda: bot.send_photo(config.ADMIN_ID, arte, write_timeout=180))
        link = await links.link_canal(dados, "whats")
        await bot.send_message(config.ADMIN_ID, f"<pre>{escape('🚨 ' + formatar.whatsapp(dados, link))}</pre>",
                               parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception as e:  # noqa: BLE001
        log.warning("Não consegui mandar o alerta completo (%s)", e)


async def buscar_ml(bot):
    """Lê a página de ofertas do ML, filtra, gera seus links e coloca na fila."""
    try:
        ofertas = await mercadolivre.buscar_ofertas()
    except mercadolivre.SessaoMLInvalida:
        log.warning("Mercado Livre: barrado pela página \"Segurança\" (cookie vencido ou bloqueio do ML)")
        estado["ml_ultima"] = f"{datetime.now(FUSO):%H:%M} cookie vencido ou página de verificação"
        await avisar_cookie_ml(bot)
        return 0
    except Exception as e:  # noqa: BLE001
        estado["ml_ultima"] = f"{datetime.now(FUSO):%H:%M} erro: {e}"
        log.error("Erro lendo ofertas do Mercado Livre: %s", e)
        return 0
    historico = anotar_precos(ofertas)
    parecidos = banco.titulos_recentes()
    boas = [o for o in ofertas if combina_filtro_ml(o.titulo) and passa_no_filtro(o)]
    boas.sort(key=lambda o: o.desconto, reverse=True)
    novas = 0
    for o in boas[: config.OFERTAS_POR_BUSCA * len(config.ML_OFERTAS_URLS)]:
        if not passa_no_filtro(o, parecidos):
            continue
        try:
            o.link = await links.link_mercadolivre(o.link)
        except links.CookieMLExpirado:
            await avisar_cookie_ml(bot)
            break
        except Exception as e:  # noqa: BLE001
            log.warning("Não consegui gerar o link do ML (%s)", e)
            continue
        await enfileirar_oferta(bot, o, historico.get(o.chave))
        _anotar_titulo(parecidos, o)
        novas += 1
    log.info("Mercado Livre: %d ofertas novas na fila (de %d na página)", novas, len(ofertas))
    estado["ml_ultima"] = (f"{datetime.now(FUSO):%H:%M} — {len(ofertas)} na página, {len(boas)} passaram no filtro, "
                           f"{novas} novas na fila")
    return novas


# ---------------------------------------------------------------- tarefas agendadas

async def busca_agendada(context: ContextTypes.DEFAULT_TYPE):
    """A busca de toda hora. Pausado, não busca (o /buscar e o botão Buscar continuam funcionando)."""
    if estado["pausado"]:
        log.info("Pausado: pulando a busca automática.")
        return
    await buscar_ofertas(context)


async def buscar_ofertas(context: ContextTypes.DEFAULT_TYPE):
    novas = 0
    if config.ml_ativo() and config.ML_OFERTAS:
        novas += await buscar_ml(context.bot)

    fontes = []
    if config.shopee_ativo():
        fontes.append(shopee)
    if config.aliexpress_ativo():
        fontes.append(aliexpress)
    if not fontes:
        return novas

    # PALAVRAS_POR_BUSCA palavras-chave por rodada, em rodízio. Na Shopee, a ordem alterna a cada rodada
    # (mais vendidos, maior comissão...).
    palavras = config.PALAVRAS_CHAVE
    rodada = [palavras[(estado["indice_palavra"] + i) % len(palavras)]
              for i in range(min(max(config.PALAVRAS_POR_BUSCA, 1), len(palavras)))]
    estado["indice_palavra"] += len(rodada)
    ordem = config.SHOPEE_ORDENS[estado["rodada"] % len(config.SHOPEE_ORDENS)]
    estado["rodada"] += 1
    parecidos = banco.titulos_recentes()

    for palavra in rodada:
        for fonte in fontes:
            nome = fonte.__name__.split(".")[-1]
            try:
                ofertas = await (fonte.buscar(palavra, ordem=ordem) if fonte is shopee else fonte.buscar(palavra))
            except Exception as e:  # noqa: BLE001
                log.error("Erro buscando em %s: %s", nome, e)
                await avisar_admin(context.bot, f"⚠️ Erro ao buscar ofertas em {nome}: {e}")
                continue
            historico = anotar_precos(ofertas)
            boas = [o for o in ofertas if passa_no_filtro(o, parecidos)]
            boas.sort(key=lambda o: o.desconto, reverse=True)
            for o in boas[: config.OFERTAS_POR_BUSCA]:
                if not passa_no_filtro(o, parecidos):  # outro anúncio do mesmo produto entrou nesta busca
                    continue
                if fonte is shopee and o.url_produto:  # link com Sub ID "telegram"
                    try:
                        o.link = await shopee.gerar_link(o.url_produto, "telegram")
                    except Exception as e:  # noqa: BLE001 — fica o link da busca, que também é de afiliado
                        log.warning("Não consegui gerar o link da Shopee com Sub ID (%s)", e)
                await enfileirar_oferta(context.bot, o, historico.get(o.chave))
                _anotar_titulo(parecidos, o)
                novas += 1
    log.info("Busca %s (ordem %s): %d ofertas novas na fila (fila: %d)", rodada, ordem, novas, banco.tamanho_fila())
    return novas


async def publicar_proxima(context: ContextTypes.DEFAULT_TYPE):
    if estado["pausado"] or not no_horario():
        return
    descartar_velhas()
    item = banco.proximo_da_fila()
    # Ofertas automáticas que entraram na fila antes do filtro de desconto máximo: descarta.
    while item and item["chave"] and item["dados"] and not banco.desconto_confiavel(item["dados"]):
        log.info("Descartada da fila (desconto suspeito): %s", item["dados"].get("titulo"))
        banco.remover_da_fila(item["id"])
        item = banco.proximo_da_fila()
    if not item:
        return
    try:
        await enviar(context.bot, config.CANAL, item["texto"], item["foto"])
        banco.marcar_postado(item["chave"] or f"manual:{item['id']}", item["dados"])
        if item["chave"] and banco.alerta_avisado(item["chave"]):
            banco.marcar_whats(item["chave"])  # o kit do WhatsApp já foi junto com o alerta
        log.info("Publicado: %s", item["chave"] or "post manual")
        await mandar_criativos(context.bot, item["dados"])
    except Exception as e:  # noqa: BLE001
        log.error("Erro ao publicar no canal: %s", e)
        await avisar_admin(context.bot, f"⚠️ Não consegui publicar no canal: {e}")
    finally:
        # Remove mesmo se falhar, para um post com problema não travar a fila.
        banco.remover_da_fila(item["id"])


def descartar_velhas():
    """Tira da fila as ofertas automáticas velhas (FILA_MAX_HORAS) e soma no contador do relatório."""
    saiu = banco.descartar_velhas(config.FILA_MAX_HORAS)
    if saiu:
        banco.gravar_valor("descartadas", banco.ler_valor("descartadas") + saiu)
        log.info("Fila: %d ofertas com mais de %dh descartadas (preço pode ter mudado)", saiu, config.FILA_MAX_HORAS)


async def alertas_da_manha(context: ContextTypes.DEFAULT_TYPE):
    """No HORA_INICIO: manda os alertas de oferta forte que apareceram de madrugada."""
    if estado["pausado"]:  # pausado = nada sai sozinho; os comandos manuais continuam
        return
    for chave, motivo, dados in banco.alertas_pendentes():
        banco.marcar_alerta_avisado(chave)
        await mandar_alerta(context.bot, dados, motivo + ", achada de madrugada")
        if banco.ja_visto(chave) and not banco.ja_visto_na_fila(chave):
            banco.marcar_whats(chave)  # já foi postada: o kit do WhatsApp foi junto com o alerta


async def mandar_relatorio(bot, zerar=False):
    """Resumo dos últimos 7 dias no seu privado."""
    r = banco.resumo_periodo(7)
    descartadas = banco.ler_valor("descartadas")
    lojas = "\n".join(f"  • {loja}: {n}" for loja, n in sorted(r["por_loja"].items(), key=lambda x: -x[1])) or "  • nenhuma"
    c = r["canais"]
    top = "\n".join(f"  {i}. {escape((o.get('titulo') or 'Oferta')[:60])} — {formatar.reais(o['preco'])} "
                    f"({int(o['desconto'])}% OFF, {escape(o.get('fonte') or '')})"
                    for i, o in enumerate(r["top"], 1)) or "  (nenhuma com desconto)"
    texto = (
        "📊 <b>Relatório da semana</b> (últimos 7 dias)\n\n"
        f"Ofertas postadas no Telegram: <b>{r['total']}</b>\n{lojas}\n\n"
        f"Desconto médio: {r['media_desconto']}%\n"
        f"Alertas de oferta forte: {r['alertas']}\n"
        f"Também foram pro: Facebook {c['face']} · Instagram {c['insta']} · WhatsApp {c['whats']} · TikTok {c['tiktok']}\n"
        f"Descartadas por ficarem velhas na fila: {descartadas}\n"
        f"Fila agora: {banco.tamanho_fila()}\n\n"
        f"🏆 <b>Maiores descontos</b>\n{top}\n\n"
        "Vendas por canal da Shopee: no painel de afiliados, Relatório de conversões, coluna Sub ID "
        "(telegram, whats, face)."
    )
    if config.ADMIN_ID:
        await bot.send_message(config.ADMIN_ID, texto, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    if zerar:
        banco.gravar_valor("descartadas", 0)


async def relatorio_semanal(context: ContextTypes.DEFAULT_TYPE):
    await mandar_relatorio(context.bot, zerar=True)


async def post_face(context: ContextTypes.DEFAULT_TYPE):
    if estado["pausado"]:  # pausado = nada sai sozinho; os comandos manuais continuam
        return
    await postar_facebook(context.bot)


async def kit_whats(context: ContextTypes.DEFAULT_TYPE):
    if estado["pausado"]:  # pausado = nada sai sozinho; os comandos manuais continuam
        return
    await mandar_para_whats(context.bot)


def _formatos_tiktok():
    """Lista de formatos do rodízio, conforme TIKTOK_FORMATO."""
    f = config.TIKTOK_FORMATO
    if f in ("alternar", "todos", ""):
        return list(video_casual.FORMATOS)
    lista = [x for x in f.split(",") if x in video_casual.FORMATOS]
    return lista or (["achados"] if f == "achados" else list(video_casual.FORMATOS))


def _formato_do_dia():
    """'achados' = o vídeo dos 3 achados vai pro TikTok; senão, o próximo formato descontraído do rodízio."""
    lista = _formatos_tiktok()
    return "achados" if lista == ["achados"] else banco.proximo_formato(lista)


async def mandar_casual(bot, formato, para_tiktok=False, avisar_se_vazio=False):
    """Vídeo descontraído com a(s) oferta(s) de maior desconto que ainda não viraram vídeo no TikTok."""
    if not config.ADMIN_ID:
        return
    precisa = 2 if formato == "duelo" else 1
    if para_tiktok:
        escolhidas = banco.proximas_para_tiktok(precisa)
        if not escolhidas and precisa == 2:  # sem 2 ofertas novas, o duelo vira adivinha
            formato, precisa = "adivinha", 1
            escolhidas = banco.proximas_para_tiktok(1)
        if not escolhidas:
            if avisar_se_vazio:
                await avisar_admin(bot, "Todas as ofertas dos últimos dias já viraram vídeo no TikTok. "
                                        "Assim que o robô postar ofertas novas, o /tiktok volta a funcionar.")
            return
    else:
        escolhidas = [(None, o) for o in banco.melhores_postadas(horas=24, quantidade=precisa)]
        if not escolhidas:
            if avisar_se_vazio:
                await avisar_admin(bot, "Ainda não tem ofertas com preço postadas nas últimas 24h para montar o vídeo.")
            return
    oferta = escolhidas[0][1]
    try:
        imagens = [await _baixar_imagem(bot, o.get("imagem")) for _, o in escolhidas]
        caminho = await asyncio.to_thread(video_casual.gerar, formato,
                                          [(o, img) for (_, o), img in zip(escolhidas, imagens)])
        legenda = formatar.legenda_casual(formato, oferta)
        try:
            with open(caminho, "rb") as arq:
                await bot.send_document(
                    config.ADMIN_ID, document=arq, filename=f"{formato}-{datetime.now(FUSO):%Y-%m-%d-%Hh}.mp4",
                    caption=f"🎬 Vídeo \"{video_casual.NOMES.get(formato, formato)}\" pro TikTok. "
                            "Legenda abaixo: toque nela para copiar.",
                    read_timeout=120, write_timeout=120)
            await bot.send_message(config.ADMIN_ID, f"<pre>{escape(legenda)}</pre>", parse_mode=ParseMode.HTML)
            if para_tiktok and tiktok.ativo():
                try:
                    aviso = await tiktok.enviar(caminho, legenda)
                    for chave, _ in escolhidas:
                        banco.marcar_tiktok(chave)
                    await avisar_admin(bot, aviso)
                except Exception as e:  # noqa: BLE001
                    log.error("Erro ao enviar para o TikTok: %s", e)
                    await avisar_admin(bot, f"⚠️ Não consegui mandar o vídeo pro TikTok: {e}")
        finally:
            Path(caminho).unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001
        log.exception("Erro ao gerar o vídeo descontraído")
        await avisar_admin(bot, f"⚠️ Não consegui gerar o vídeo: {e}")


async def video_tiktok(context: ContextTypes.DEFAULT_TYPE):
    """Nos TIKTOK_HORARIOS: o próximo vídeo descontraído do rodízio vai pro TikTok."""
    if estado["pausado"]:  # pausado = nada sai sozinho; os comandos manuais continuam
        return
    if _formatos_tiktok() != ["achados"]:
        await mandar_casual(context.bot, _formato_do_dia(), para_tiktok=True)


async def video_diario(context: ContextTypes.DEFAULT_TYPE):
    if estado["pausado"]:  # pausado = nada sai sozinho; os comandos manuais continuam
        return
    await mandar_video(context.bot, reels=True)


async def post_insta(context: ContextTypes.DEFAULT_TYPE):
    if estado["pausado"]:  # pausado = nada sai sozinho; os comandos manuais continuam
        return
    await postar_instagram(context.bot)


async def limpeza_diaria(context: ContextTypes.DEFAULT_TYPE):
    banco.esquecer_antigos(dias=30)
    banco.esquecer_precos(dias=90)


# ---------------------------------------------------------------- comandos

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Seu ID no Telegram é: {update.effective_user.id}")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not eh_admin(update):
        await update.message.reply_text(
            "Olá! Este robô publica promoções no canal. "
            "Se você é o dono, coloque o seu ID (/id) em ADMIN_ID no arquivo .env."
        )
        return
    await update.message.reply_text(
        "Comandos:\n"
        "/status — situação do robô\n"
        "/buscar — buscar ofertas agora\n"
        "/postar — publicar o próximo da fila agora\n"
        "/pausar e /retomar — pausar as publicações\n"
        "/limpar — esvaziar a fila\n"
        "/video — vídeo \"3 achados de hoje\" para o Instagram\n"
        "/whats — melhor oferta pronta pra colar no WhatsApp\n"
        "/face — posta agora a melhor oferta na página do Facebook\n"
        "/insta — posta agora a melhor oferta no Instagram\n"
        "/tiktok — manda agora o vídeo de hoje pros rascunhos do TikTok\n"
        "/adivinha — vídeo \"adivinha o preço\" com a melhor oferta (só no seu Telegram)\n"
        "/conversa — vídeo de conversa com a melhor oferta (só no seu Telegram)\n"
        "/casual — escolher qualquer formato de vídeo (POV, duelo, comparação...)\n"
        "/relatorio — resumo dos últimos 7 dias\n"
        "/log — te manda o arquivo de log do robô\n\n"
        "Para postar manualmente, me mande um link ou uma foto com legenda contendo o link."
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not eh_admin(update):
        return
    fontes = [n for n, ok in [("Mercado Livre", config.ml_ativo() and config.ML_OFERTAS),
                              ("Shopee", config.shopee_ativo()), ("AliExpress", config.aliexpress_ativo())] if ok]
    await update.message.reply_text(
        f"{'⏸ Pausado' if estado['pausado'] else '▶️ Rodando'}\n"
        f"Fila: {banco.tamanho_fila()} posts\n"
        f"Busca automática: {', '.join(fontes) or 'nenhuma configurada'} — "
        f"{'todo tipo de promoção' if config.TODOS else 'só PC gamer'} (NICHO={config.NICHO})\n"
        f"Última busca no Mercado Livre: {estado.get('ml_ultima') or 'ainda não rodou'}\n"
        f"Fila por loja: {', '.join(f'{loja} {n}' for loja, n in banco.fila_por_loja().items()) or 'vazia'}\n"
        f"Amazon (tag): {config.AMAZON_TAG or 'não configurada'}\n"
        f"KaBuM! (Awin): {'links automáticos' if config.AWIN_ID else 'não configurada (AWIN_ID)'}\n"
        f"Shopee: {'API oficial' if config.shopee_ativo() else ('links automáticos (cookie do portal)' if config.SHOPEE_COOKIE else 'não configurada')}\n"
        f"Mercado Livre: {('links automáticos + busca de ofertas' if config.ML_OFERTAS else 'links automáticos') if config.ml_ativo() else ('não configurado (ML_COOKIE vazio)' if not config.ML_COOKIE else 'não configurado')}\n"
        f"Horário de posts: {config.HORA_INICIO}h às {config.HORA_FIM}h "
        f"({'dentro' if no_horario() else 'fora'} do horário agora)\n"
        f"Posta a cada {config.INTERVALO_POST_MIN} min (a melhor da fila, alternando as lojas), "
        f"busca a cada {config.INTERVALO_BUSCA_MIN} min\n"
        f"Oferta automática sai da fila depois de: {f'{config.FILA_MAX_HORAS}h' if config.FILA_MAX_HORAS else 'nunca'}\n"
        f"Artes do Instagram: {'ligadas' if config.CRIATIVOS else 'desligadas'}\n"
        f"Vídeo diário: {f'às {config.VIDEO_HORA}h' if 0 <= config.VIDEO_HORA <= 23 else 'desligado'}\n"
        f"Kit WhatsApp: {', '.join(f'{h}h' for h in config.WHATS_HORARIOS) or 'desligado'}\n"
        f"Página do Facebook: {_status_face()}\n"
        f"Instagram: {_status_insta()}\n"
        f"TikTok: {_status_tiktok()}\n"
        f"Voz dos vídeos: {config.VOZ if voz.ligada() else 'desligada'}\n"
        f"Arquivo .env: {config.ARQUIVO_ENV}"
        f"{' — linhas com problema: ' + ', '.join(map(str, config.LINHAS_RUINS_ENV)) if config.LINHAS_RUINS_ENV else ''}\n"
        f"Alerta de oferta forte: "
        f"{f'{config.ALERTA_DESCONTO}%+ OFF ou {config.ALERTA_QUEDA}% abaixo do menor preço, até {config.ALERTA_MAX_DIA}/dia' if config.ALERTA_DESCONTO or config.ALERTA_QUEDA else 'desligado'}"
    )


def _status_tiktok():
    if tiktok.ativo():
        lista = _formatos_tiktok()
        formatos = "3 achados" if lista == ["achados"] else ", ".join(video_casual.NOMES[x] for x in lista)
        destino = "posta direto com legenda (Somente eu)" if config.TIKTOK_MODO == "privado" else "vai pros rascunhos"
        horas = ", ".join(f"{h}h" for h in (config.TIKTOK_HORARIOS or [config.VIDEO_HORA]))
        return f"ligado — às {horas} {destino}. Rodízio: {formatos}"
    if config.TIKTOK:
        return "falta conectar a conta (conectar-tiktok.bat) ou o TIKTOK_CLIENT_KEY/SECRET no .env"
    return "desligado (TIKTOK=1 no .env)"


def _status_insta():
    if not config.insta_ativo():
        return "desligado (INSTA=1 no .env)"
    horarios = ", ".join(f"{h}h45" for h in config.IG_HORARIOS) or "sem horários"
    feitos = banco.postados_face_hoje(_inicio_do_dia(), coluna="insta")
    extras = [n for n, ok in [("Story", config.IG_STORIES), ("Reels do vídeo diário", config.IG_REELS)] if ok]
    return (f"ligado — {config.IG_DESCONTO_MINIMO}%+ OFF, às {horarios}"
            f"{' + ' + ' e '.join(extras) if extras else ''}. Hoje: {feitos}")


def _status_face():
    if not config.facebook_ativo():
        return "não configurada"
    horarios = ", ".join(f"{h}h" for h in config.FB_HORARIOS) or "sem horários"
    feitos = banco.postados_face_hoje(_inicio_do_dia())
    return (f"ligada — só as melhores ({config.FB_DESCONTO_MINIMO}%+ OFF), às {horarios}. "
            f"Hoje: {feitos}")


async def cmd_face(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if eh_admin(update):
        await postar_facebook(context.bot, avisar_resultado=True)


async def cmd_insta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if eh_admin(update):
        await postar_instagram(context.bot, avisar_resultado=True)


async def cmd_buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not eh_admin(update):
        return
    if not (config.shopee_ativo() or config.aliexpress_ativo() or (config.ml_ativo() and config.ML_OFERTAS)):
        await update.message.reply_text("Nenhuma busca automática configurada (Mercado Livre/Shopee/AliExpress) no .env.")
        return
    await update.message.reply_text("Buscando...")
    novas = await buscar_ofertas(context)
    await update.message.reply_text(f"{novas} ofertas novas na fila. Total na fila: {banco.tamanho_fila()}.")


async def cmd_postar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not eh_admin(update):
        return
    descartar_velhas()
    item = banco.proximo_da_fila()
    while item and item["chave"] and item["dados"] and not banco.desconto_confiavel(item["dados"]):
        banco.remover_da_fila(item["id"])  # desconto suspeito (preço "De" inflado)
        item = banco.proximo_da_fila()
    if not item:
        await update.message.reply_text("A fila está vazia.")
        return
    try:
        await enviar(context.bot, config.CANAL, item["texto"], item["foto"])
    except Exception as e:  # noqa: BLE001 — o item continua na fila
        await update.message.reply_text(f"⚠️ Não consegui publicar no canal ({e}). O post continua na fila.")
        return
    banco.marcar_postado(item["chave"] or f"manual:{item['id']}", item["dados"])
    banco.remover_da_fila(item["id"])
    await update.message.reply_text("Publicado ✅")
    await mandar_criativos(context.bot, item["dados"])


async def cmd_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not eh_admin(update):
        return
    await update.message.reply_text("🎬 Montando o vídeo com as melhores ofertas das últimas 24h... leva uns minutinhos.")
    await mandar_video(context.bot, avisar_se_vazio=True)


async def cmd_tiktok(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not eh_admin(update):
        return
    if not tiktok.ativo():
        await update.message.reply_text(f"TikTok: {_status_tiktok()}. Veja o GUIA-TIKTOK.md.")
        return
    await update.message.reply_text("🎬 Montando o vídeo e mandando pros rascunhos do TikTok... leva uns minutinhos.")
    formato = _formato_do_dia()
    if formato == "achados":
        await mandar_video(context.bot, avisar_se_vazio=True, para_tiktok=True)
    else:
        await mandar_casual(context.bot, formato, para_tiktok=True, avisar_se_vazio=True)


async def cmd_adivinha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not eh_admin(update):
        return
    await update.message.reply_text("🎬 Montando o \"adivinha o preço\"... leva uns minutinhos.")
    await mandar_casual(context.bot, "adivinha", avisar_se_vazio=True)


async def cmd_casual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/casual pov — monta um formato específico só pro seu Telegram (pra testar)."""
    if not eh_admin(update):
        return
    pedido = (context.args[0].lower() if context.args else "")
    if pedido not in video_casual.FORMATOS:
        lista = "\n".join(f"/casual {f} — {video_casual.NOMES[f]}" for f in video_casual.FORMATOS)
        await update.message.reply_text(f"Escolha um formato:\n{lista}")
        return
    await update.message.reply_text(f"🎬 Montando \"{video_casual.NOMES[pedido]}\"... leva uns minutinhos.")
    await mandar_casual(context.bot, pedido, avisar_se_vazio=True)


async def cmd_conversa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not eh_admin(update):
        return
    await update.message.reply_text("🎬 Montando o vídeo de conversa... leva uns minutinhos.")
    await mandar_casual(context.bot, "conversa", avisar_se_vazio=True)


async def cmd_whats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if eh_admin(update):
        await mandar_para_whats(context.bot, avisar_se_vazio=True)


def definir_pausa(pausado):
    """Pausa ou retoma as publicações e guarda no banco (continua valendo se o robô reiniciar)."""
    estado["pausado"] = bool(pausado)
    banco.gravar_valor("pausado", 1 if pausado else 0)


async def cmd_pausar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if eh_admin(update):
        definir_pausa(True)
        await update.message.reply_text(
            "⏸ Robô pausado: não busca nem posta nada sozinho (Telegram, Facebook, Instagram, WhatsApp, TikTok e "
            "vídeo), mesmo se reiniciar. Os comandos manuais continuam funcionando. Use /retomar para voltar.")


async def cmd_retomar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if eh_admin(update):
        definir_pausa(False)
        await update.message.reply_text("▶️ Publicações retomadas.")


async def cmd_limpar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if eh_admin(update):
        banco.limpar_fila()
        await update.message.reply_text("Fila esvaziada.")


# ---------------------------------------------------------------- post manual

async def receber_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not eh_admin(update):
        return
    msg = update.message
    texto = msg.caption if msg.photo else msg.text
    if not texto or not links.PADRAO_URL.search(texto):
        await msg.reply_text("Me mande um texto ou foto com legenda que contenha o link do produto.")
        return

    texto_novo, avisos = await links.converter_texto(texto)
    foto = msg.photo[-1].file_id if msg.photo else None
    final = formatar.manual(texto_novo)

    dados = criativo.ler_oferta_do_texto(texto)
    if dados and foto:
        dados["imagem"] = "tg:" + foto
    if dados:
        link = links.PADRAO_URL.search(texto_novo)
        dados["link"] = link.group(0) if link else None
        if links.url_shopee(texto):  # guarda o link comum para gerar os links por canal (Sub ID)
            dados["url_produto"] = links.url_shopee(texto)

    # Link do Mercado Livre sem foto ou sem preço: o robô busca tudo na página do produto.
    url_ml = next((u for u in links.PADRAO_URL.findall(texto) if mercadolivre.PADRAO_ID.search(u)
                   and "mercadoli" in u and "/social/" not in u and "/sec/" not in u), None)
    if url_ml and config.ml_ativo() and (not dados or not foto):
        info = None
        try:
            info = await mercadolivre.dados_produto(url_ml)
        except mercadolivre.SessaoMLInvalida:
            avisos.append(AVISO_COOKIE)
        except Exception as e:  # noqa: BLE001
            avisos.append(f"Não consegui ler a página do produto no ML ({e}).")
        if info:
            link_af = next((u for u in links.PADRAO_URL.findall(texto_novo) if "meli.la" in u or "mercadoli" in u), url_ml)
            if not dados:  # só mandou o link: o robô monta o post inteiro
                o = Oferta(fonte="Mercado Livre", id=mercadolivre.PADRAO_ID.search(url_ml).group(1).upper().replace("-", ""),
                           titulo=info["titulo"], preco=info["preco"], preco_original=info["preco_original"],
                           desconto=info["desconto"], imagem=info["imagem"], link=link_af)
                anotar_precos([o])
                final = formatar.oferta(o)
                dados = asdict(o)
            if not foto and info.get("imagem"):
                foto = info["imagem"]
                dados["imagem"] = info["imagem"]
    # Link da KaBuM! sem foto ou sem preço: tenta ler a página do produto.
    url_kb = next((u for u in links.PADRAO_URL.findall(texto) if kabum.PADRAO_ID.search(u)), None)
    if url_kb and (not dados or not foto):
        info = None
        try:
            info = await kabum.dados_produto(url_kb)
        except Exception as e:  # noqa: BLE001
            log.warning("Não consegui ler a página da KaBuM! (%s)", e)
        if not info:
            avisos.append("Não consegui ler a página da KaBuM!: mande foto + legenda com o preço (ex: De R$ 299 por R$ 199).")
        else:
            link_af = next((u for u in links.PADRAO_URL.findall(texto_novo) if "awin1.com" in u or "kabum" in u), url_kb)
            if not dados:
                o = Oferta(fonte="KaBuM!", id=kabum.PADRAO_ID.search(url_kb).group(1), titulo=info["titulo"],
                           preco=info["preco"], preco_original=info["preco_original"], desconto=info["desconto"],
                           imagem=info["imagem"], link=link_af)
                final = formatar.oferta(o)
                dados = asdict(o)
            if not foto and info.get("imagem"):
                foto = info["imagem"]
                dados["imagem"] = info["imagem"]
    if dados and config.DESCONTO_MAXIMO and (dados.get("desconto") or 0) > config.DESCONTO_MAXIMO:
        avisos.append(f"{int(dados['desconto'])}% OFF parece preço \"De\" inflado pelo vendedor. "
                      "Confira o preço antigo antes de publicar.")
    if config.CRIATIVOS and not dados:
        avisos.append("Sem preço no texto, então não vou gerar a arte do Instagram. Ex: \"De R$ 449 por R$ 269\".")
    elif config.CRIATIVOS and not foto:
        avisos.append("Mande com foto para a arte do Instagram sair com a imagem do produto.")

    pendente_id = uuid.uuid4().hex[:8]
    context.user_data[pendente_id] = {"texto": final, "foto": foto, "dados": dados}

    linhas_botoes = [[
        InlineKeyboardButton("✅ Publicar agora", callback_data=f"agora:{pendente_id}"),
        InlineKeyboardButton("🕒 Pôr na fila", callback_data=f"fila:{pendente_id}"),
    ]]
    if dados:
        linhas_botoes.append([InlineKeyboardButton("🎨 Só gerar a arte", callback_data=f"arte:{pendente_id}")])
    linhas_botoes.append([InlineKeyboardButton("❌ Cancelar", callback_data=f"cancelar:{pendente_id}")])
    botoes = InlineKeyboardMarkup(linhas_botoes)
    await msg.reply_text("Prévia do post:")
    await enviar(context.bot, msg.chat_id, final, foto)
    aviso_txt = ("\n\n⚠️ " + "\n⚠️ ".join(avisos)) if avisos else ""
    await msg.reply_text("O que faço com ele?" + aviso_txt, reply_markup=botoes)


async def botao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not eh_admin(update):
        return
    acao, pendente_id = q.data.split(":", 1)
    post = context.user_data.pop(pendente_id, None)
    if not post:
        await q.edit_message_text("Esse post já foi resolvido ou expirou.")
        return
    if acao == "agora":
        try:
            await enviar(context.bot, config.CANAL, post["texto"], post["foto"])
        except Exception as e:  # noqa: BLE001 — devolve o post para você tentar de novo
            context.user_data[pendente_id] = post
            await q.edit_message_text(f"⚠️ Não consegui publicar no canal ({e}). Toque de novo para tentar.",
                                      reply_markup=q.message.reply_markup)
            return
        await q.edit_message_text("Publicado no canal ✅")
        banco.marcar_postado(f"manual:{pendente_id}", post.get("dados"))
        await mandar_criativos(context.bot, post.get("dados"))
    elif acao == "arte":
        await q.edit_message_text("Gerando a arte... 🎨")
        await mandar_criativos(context.bot, post.get("dados"), forcar=True)
    elif acao == "fila":
        banco.enfileirar(post["texto"], foto=post["foto"], prioridade=1, dados=post.get("dados"))
        await q.edit_message_text(f"Na fila (vai na frente das automáticas). Fila: {banco.tamanho_fila()}.")
    else:
        await q.edit_message_text("Cancelado.")


# ---------------------------------------------------------------- início

async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/log — manda o arquivo de log (sem token e sem cookies) pra você encaminhar quando precisar de ajuda."""
    if not eh_admin(update):
        return
    if not ARQUIVO_LOG.is_file():
        await update.message.reply_text("Ainda não tem log gravado. Espere uns minutos depois de ligar o robô.")
        return
    for h in logging.getLogger().handlers:
        h.flush()
    dados = ARQUIVO_LOG.read_bytes()[-3_000_000:]  # últimos ~3 MB
    await update.message.reply_document(
        document=dados, filename=f"robo-log-{datetime.now(FUSO):%Y-%m-%d-%Hh%M}.txt",
        caption="📄 Log do robô (token e cookies escondidos). Pode encaminhar para quem está te ajudando.",
        write_timeout=120)


async def cmd_relatorio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if eh_admin(update):
        await mandar_relatorio(context.bot)


async def ao_iniciar(app: Application):
    estado["pausado"] = bool(banco.ler_valor("pausado"))
    aviso = " ⏸ O robô está PAUSADO: não busca nem posta sozinho (use /retomar)." if estado["pausado"] else ""
    await avisar_admin(app.bot, "🤖 Robô de promoções ligado. Mande /status para ver a situação." + aviso)


def main():
    config.validar()
    _log_em_arquivo()
    # Tempos maiores: envio de fotos/artes/vídeo em rede lenta não dá "Timed out".
    construtor = (
        Application.builder().token(config.TELEGRAM_TOKEN).post_init(ao_iniciar)
        .connect_timeout(30).read_timeout(60).write_timeout(120).pool_timeout(30)
    )
    if hasattr(construtor, "media_write_timeout"):  # versões mais novas da biblioteca
        construtor = construtor.media_write_timeout(180)
    app = construtor.build()

    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler(["start", "ajuda"], cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("buscar", cmd_buscar))
    app.add_handler(CommandHandler("postar", cmd_postar))
    app.add_handler(CommandHandler("pausar", cmd_pausar))
    app.add_handler(CommandHandler("retomar", cmd_retomar))
    app.add_handler(CommandHandler("limpar", cmd_limpar))
    app.add_handler(CommandHandler("video", cmd_video))
    app.add_handler(CommandHandler("tiktok", cmd_tiktok))
    app.add_handler(CommandHandler("adivinha", cmd_adivinha))
    app.add_handler(CommandHandler("conversa", cmd_conversa))
    app.add_handler(CommandHandler("casual", cmd_casual))
    app.add_handler(CommandHandler("whats", cmd_whats))
    app.add_handler(CommandHandler("face", cmd_face))
    app.add_handler(CommandHandler("insta", cmd_insta))
    app.add_handler(CommandHandler("relatorio", cmd_relatorio))
    app.add_handler(CommandHandler("log", cmd_log))
    app.add_handler(CallbackQueryHandler(botao))
    app.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & (filters.TEXT | filters.PHOTO) & ~filters.COMMAND, receber_manual)
    )

    jobs = app.job_queue
    jobs.run_repeating(busca_agendada, interval=config.INTERVALO_BUSCA_MIN * 60, first=10)
    jobs.run_repeating(publicar_proxima, interval=config.INTERVALO_POST_MIN * 60, first=60)
    jobs.run_repeating(limpeza_diaria, interval=86400, first=3600)
    if 0 <= config.VIDEO_HORA <= 23:
        jobs.run_daily(video_diario, time=hora_do_dia(hour=config.VIDEO_HORA, tzinfo=FUSO))
    for h in config.TIKTOK_HORARIOS or ([config.VIDEO_HORA] if 0 <= config.VIDEO_HORA <= 23 else []):
        jobs.run_daily(video_tiktok, time=hora_do_dia(hour=h, minute=5, tzinfo=FUSO))
    for h in config.WHATS_HORARIOS:
        jobs.run_daily(kit_whats, time=hora_do_dia(hour=h, tzinfo=FUSO))
    for h in config.FB_HORARIOS:  # minuto 30 para não coincidir com o kit do WhatsApp
        jobs.run_daily(post_face, time=hora_do_dia(hour=h, minute=30, tzinfo=FUSO))
    if 0 <= config.HORA_INICIO <= 23:
        jobs.run_daily(alertas_da_manha, time=hora_do_dia(hour=config.HORA_INICIO, tzinfo=FUSO))
    if config.RELATORIO:  # domingo às 21h (0 = domingo)
        jobs.run_daily(relatorio_semanal, time=hora_do_dia(hour=21, minute=50, tzinfo=FUSO), days=(0,))
    if config.insta_ativo():
        for h in config.IG_HORARIOS:  # minuto 45: depois do Facebook, para dar tempo de escolher outra oferta
            jobs.run_daily(post_insta, time=hora_do_dia(hour=h, minute=45, tzinfo=FUSO))

    try:
        import painel_ponte  # opcional: botões do painel (painel.pyw)
        painel_ponte.instalar(app, globals())
    except ImportError:
        pass

    log.info("Robô iniciado. Canal: %s | .env e robo.db em: %s", config.CANAL, config.PASTA_DADOS)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
