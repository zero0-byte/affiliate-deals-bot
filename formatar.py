"""Monta o texto das mensagens (HTML do Telegram)."""
import random
from html import escape

import config

LIMITE_LEGENDA = 1024  # limite do Telegram para legenda de foto


def reais(valor):
    texto = f"{valor:,.2f}"
    return "R$ " + texto.replace(",", "X").replace(".", ",").replace("X", ".")


def oferta(o):
    titulo = o.titulo if len(o.titulo) <= 150 else o.titulo[:147] + "..."
    linhas = [f"🔥 <b>{escape(titulo)}</b>", ""]
    if o.preco_original and o.preco_original > o.preco:
        linhas.append(f"De <s>{reais(o.preco_original)}</s>")
    preco = f"💰 <b>Por {reais(o.preco)}</b>"
    if o.desconto:
        preco += f"  ({o.desconto}% OFF)"
    linhas.append(preco)
    if o.selo:
        linhas.append(escape(o.selo))
    if o.vendas:
        linhas.append(f"⭐ {o.vendas} vendidos")
    linhas += ["", f"🛒 {o.fonte}", f"👉 {escape(o.link)}"]
    if config.WHATS_CANAL:
        linhas += ["", f'📲 <b><a href="{escape(config.WHATS_CANAL)}">ENTRE NO CANAL DO WHATSAPP</a></b> 👈']
    if config.RODAPE:
        linhas += ["", f"<i>{escape(config.RODAPE)}</i>"]
    return "\n".join(linhas)[:LIMITE_LEGENDA]


def manual(texto):
    """Post manual: mantém o seu texto e só acrescenta o rodapé."""
    corpo = escape(texto)
    if config.WHATS_CANAL and config.WHATS_CANAL not in texto:
        corpo += f'\n\n📲 <b><a href="{escape(config.WHATS_CANAL)}">ENTRE NO CANAL DO WHATSAPP</a></b> 👈'
    if config.RODAPE:
        corpo += f"\n\n<i>{escape(config.RODAPE)}</i>"
    return corpo


def whatsapp(dados, link=None):
    """Texto da oferta no formato do WhatsApp (*negrito*, ~riscado~, _itálico_), pronto pra colar."""
    import re
    titulo = re.sub(r"[*~_`]", "", dados.get("titulo") or "Oferta").strip()
    if len(titulo) > 120:
        titulo = titulo[:117] + "..."
    linhas = [f"🔥 *{titulo}*", ""]
    original = dados.get("preco_original")
    if original and original > dados["preco"]:
        linhas.append(f"~De {reais(original)}~")
    preco = f"💰 *Por {reais(dados['preco'])}*"
    if dados.get("desconto"):
        preco += f"  ({int(dados['desconto'])}% OFF)"
    linhas.append(preco)
    if dados.get("selo"):
        linhas.append(f"*{dados['selo']}*")
    if dados.get("fonte"):
        linhas += ["", f"🛒 {dados['fonte']}"]
    linhas.append(f"👉 {link or dados['link']}")
    if config.RODAPE:
        linhas += ["", f"_{config.RODAPE}_"]
    return "\n".join(linhas)


def facebook(dados, link=None):
    """Texto simples (sem marcações) para a legenda do post na página do Facebook."""
    titulo = (dados.get("titulo") or "Oferta").strip()
    linhas = [f"🔥 {titulo}", ""]
    original = dados.get("preco_original")
    if original and original > dados["preco"]:
        linhas.append(f"De {reais(original)}")
    preco = f"💰 Por {reais(dados['preco'])}"
    if dados.get("desconto"):
        preco += f" ({int(dados['desconto'])}% OFF)"
    linhas.append(preco)
    if dados.get("selo"):
        linhas.append(dados["selo"])
    if dados.get("fonte"):
        linhas.append(f"🛒 {dados['fonte']}")
    linhas += ["", f"👉 {link or dados['link']}"]
    if config.WHATS_CANAL:  # WhatsApp é a divulgação principal: vem em destaque, com o link completo (clicável)
        curto = config.WHATS_ARTE if "." in config.WHATS_ARTE and " " not in config.WHATS_ARTE else ""
        link_fb = ("https://" + curto if curto and not curto.startswith("http") else curto) or config.WHATS_CANAL
        linhas += ["", "📲 SIGA NOSSO CANAL NO WHATSAPP 👇", "Ofertas todo dia, de graça:", link_fb]
        if config.CANAL.startswith("@"):
            linhas += ["", f"Também no Telegram: t.me/{config.CANAL[1:]}"]
    elif config.CANAL.startswith("@"):
        linhas += ["", f"Mais ofertas todo dia no Telegram: t.me/{config.CANAL[1:]}"]
    if config.RODAPE:
        linhas += ["", config.RODAPE]
    return "\n".join(linhas)


def instagram(dados):
    """Legenda do post no Instagram. Link na legenda não é clicável, então manda pro canal do WhatsApp."""
    titulo = (dados.get("titulo") or "Oferta").strip()
    if len(titulo) > 120:
        titulo = titulo[:117] + "..."
    linhas = [f"🔥 {titulo}", ""]
    original = dados.get("preco_original")
    if original and original > dados["preco"]:
        linhas.append(f"De {reais(original)}")
    preco = f"💰 Por {reais(dados['preco'])}"
    if dados.get("desconto"):
        preco += f" ({int(dados['desconto'])}% OFF)"
    linhas.append(preco)
    if dados.get("selo"):
        linhas.append(dados["selo"])
    if dados.get("fonte"):
        linhas.append(f"🛒 {dados['fonte']}")
    destino = config.WHATS_ARTE if config.WHATS_CANAL else "link na bio"
    linhas += ["", "🔗 O link dessa oferta está no nosso canal do WhatsApp (link na bio)",
               f"📲 {destino}", "", "Oferta sujeita a alteração de preço e estoque."]
    if config.RODAPE:
        linhas += [config.RODAPE]
    linhas += ["", config.IG_HASHTAGS]
    return "\n".join(linhas)


def legenda_reels(ofertas):
    """Legenda do Reels do vídeo diário (igual à do TikTok, sem dizer 'link' clicável)."""
    return legenda_tiktok(ofertas).replace("Links no canal do WhatsApp 👉", "Links no canal do WhatsApp (link na bio) 👉")


def legenda_casual(formato, oferta):
    """Legenda pros vídeos descontraídos."""
    destino = config.WHATS_ARTE if config.WHATS_CANAL else "o canal"
    topos = {
        "adivinha": ["chutou quanto? comenta aí 👀 sem roubar olhando o final",
                     "aposto que você errou 😂 comenta seu chute"],
        "conversa": ["todo mundo tem esse amigo (ou essa mãe) 😂 marca ele aqui",
                     "isso já aconteceu com você? 😂 marca quem faria isso"],
        "pov": ["a melhor sensação do mundo 🤌 concorda?", "quem nunca 😂"],
        "nao_compre": ["salva pra não pagar caro depois 📌", "manda pra quem tá pensando em comprar 👀"],
        "duelo": ["1 ou 2? comenta 👇", "qual você levaria? só pode escolher um 👇"],
        "comparacao": ["diferença absurda 😳 você pagaria o preço normal?", "salva esse vídeo 📌"],
    }
    topo = random.choice(topos.get(formato, topos["adivinha"]))
    return "\n".join([topo, "", f"Link desse e de outros achados no canal do WhatsApp 👉 {destino}", "",
                      config.HASHTAGS + ("" if "#humor" in config.HASHTAGS else " #humor")])


def legenda_tiktok(ofertas):
    """Legenda pronta pra colar no TikTok/Reels do vídeo de achados."""
    linhas = [f"Achados {'' if config.TODOS else 'de PC '}de hoje 🔥 qual você levaria?", ""]
    for o in ofertas:
        titulo = (o.get("titulo") or "Oferta").strip()
        if len(titulo) > 50:
            titulo = titulo[:47] + "..."
        linhas.append(f"• {titulo}: {reais(o['preco'])}")
    destino = config.WHATS_ARTE if config.WHATS_CANAL else "o canal"
    linhas += ["", f"Links no canal do WhatsApp 👉 {destino}",
               "", config.HASHTAGS]
    return "\n".join(linhas)
