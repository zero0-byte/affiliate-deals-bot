"""Banco SQLite: lembra o que já foi postado e guarda a fila de posts."""
import json
import re
import sqlite3
import time
import unicodedata

import config


def _conectar():
    con = sqlite3.connect(config.ARQUIVO_BANCO)
    con.execute("CREATE TABLE IF NOT EXISTS postados (chave TEXT PRIMARY KEY, quando REAL)")
    con.execute(
        "CREATE TABLE IF NOT EXISTS fila ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, chave TEXT, foto TEXT, texto TEXT,"
        " prioridade INTEGER DEFAULT 0, criado REAL)"
    )
    for tabela in ("fila", "postados"):
        colunas = [c[1] for c in con.execute(f"PRAGMA table_info({tabela})")]
        if "dados" not in colunas:  # dados da oferta, usados nas artes e no vídeo
            con.execute(f"ALTER TABLE {tabela} ADD COLUMN dados TEXT")
    colunas = [c[1] for c in con.execute("PRAGMA table_info(postados)")]
    if "whats" not in colunas:  # quando a oferta foi separada para o WhatsApp
        con.execute("ALTER TABLE postados ADD COLUMN whats REAL")
    if "face" not in colunas:  # quando a oferta foi postada na página do Facebook
        con.execute("ALTER TABLE postados ADD COLUMN face REAL")
    if "insta" not in colunas:  # quando a oferta foi postada no Instagram
        con.execute("ALTER TABLE postados ADD COLUMN insta REAL")
    if "tiktok" not in colunas:  # quando a oferta virou vídeo no TikTok
        con.execute("ALTER TABLE postados ADD COLUMN tiktok REAL")
    # Histórico de preços: cada vez que o robô vê um produto, guarda o preço (para o selo de menor preço)
    con.execute("CREATE TABLE IF NOT EXISTS precos (chave TEXT, preco REAL, quando REAL)")
    con.execute("CREATE INDEX IF NOT EXISTS precos_chave ON precos (chave, quando)")
    # Ofertas fortes que já viraram alerta no seu privado
    con.execute("CREATE TABLE IF NOT EXISTS alertas (chave TEXT PRIMARY KEY, quando REAL)")
    colunas = [c[1] for c in con.execute("PRAGMA table_info(alertas)")]
    if "avisado" not in colunas:  # quando o alerta chegou no seu privado (fora do horário, espera a manhã)
        con.execute("ALTER TABLE alertas ADD COLUMN avisado REAL")
        con.execute("UPDATE alertas SET avisado=quando")
    if "motivo" not in colunas:
        con.execute("ALTER TABLE alertas ADD COLUMN motivo TEXT")
    # Contadores e opções que precisam sobreviver quando o robô reinicia (rodízio, pausa...)
    con.execute("CREATE TABLE IF NOT EXISTS contadores (nome TEXT PRIMARY KEY, valor INTEGER)")
    return con


def ja_visto(chave):
    """True se a oferta já foi postada ou já está na fila."""
    with _conectar() as con:
        if con.execute("SELECT 1 FROM postados WHERE chave=?", (chave,)).fetchone():
            return True
        return con.execute("SELECT 1 FROM fila WHERE chave=?", (chave,)).fetchone() is not None


def ja_visto_na_fila(chave):
    with _conectar() as con:
        return con.execute("SELECT 1 FROM fila WHERE chave=?", (chave,)).fetchone() is not None


def enfileirar(texto, foto=None, chave=None, prioridade=0, dados=None):
    with _conectar() as con:
        con.execute(
            "INSERT INTO fila (chave, foto, texto, prioridade, criado, dados) VALUES (?,?,?,?,?,?)",
            (chave, foto, texto, prioridade, time.time(), json.dumps(dados) if dados else None),
        )


def _item(linha):
    return {"id": linha[0], "chave": linha[1], "foto": linha[2], "texto": linha[3],
            "dados": json.loads(linha[4]) if linha[4] else None}


def _loja(chave, dados=None):
    """'Shopee', 'Mercado Livre'... (pela oferta ou pelo começo da chave)."""
    return (dados or {}).get("fonte") or (chave or "").split(":")[0]


def descartar_velhas(horas):
    """Tira da fila as ofertas automáticas com mais de X horas (o preço provavelmente já mudou).

    Posts que você mandou (sem chave) nunca são descartados. Devolve quantas saíram.
    """
    if not horas:
        return 0
    with _conectar() as con:
        cur = con.execute("DELETE FROM fila WHERE chave IS NOT NULL AND criado < ?", (time.time() - horas * 3600,))
        return cur.rowcount


def proximo_da_fila():
    """O próximo post: primeiro os seus e os alertas (na ordem em que entraram);
    depois a MELHOR oferta automática, de preferência de uma loja diferente da última postada."""
    with _conectar() as con:
        linha = con.execute(
            "SELECT id, chave, foto, texto, dados FROM fila WHERE prioridade > 0 ORDER BY prioridade DESC, id ASC LIMIT 1"
        ).fetchone()
        if linha:
            return _item(linha)
        linhas = con.execute("SELECT id, chave, foto, texto, dados FROM fila").fetchall()
        ultima = con.execute(
            "SELECT chave, dados FROM postados WHERE chave NOT LIKE 'manual:%' ORDER BY quando DESC LIMIT 1"
        ).fetchone()
    if not linhas:
        return None
    itens = [_item(l) for l in linhas]
    loja_anterior = _loja(ultima[0], json.loads(ultima[1]) if ultima[1] else None) if ultima else None

    def nota(item):
        return nota_oferta(item["dados"]) if item["dados"] else 0

    outras = [i for i in itens if _loja(i["chave"], i["dados"]) != loja_anterior]
    return max(outras or itens, key=nota)


def remover_da_fila(item_id):
    with _conectar() as con:
        con.execute("DELETE FROM fila WHERE id=?", (item_id,))


def marcar_postado(chave, dados=None):
    if not chave:
        return
    with _conectar() as con:
        con.execute(
            "INSERT OR REPLACE INTO postados (chave, quando, dados) VALUES (?,?,?)",
            (chave, time.time(), json.dumps(dados) if dados else None),
        )


def desconto_confiavel(d):
    """False quando o desconto é alto demais para ser verdade (preço antigo inflado)."""
    return not (config.DESCONTO_MAXIMO and (d.get("desconto") or 0) > config.DESCONTO_MAXIMO)


def melhores_postadas(horas=24, quantidade=3):
    """As ofertas postadas nas últimas X horas com maior desconto (para o vídeo)."""
    with _conectar() as con:
        linhas = con.execute(
            "SELECT dados FROM postados WHERE dados IS NOT NULL AND quando >= ? ORDER BY quando DESC",
            (time.time() - horas * 3600,),
        ).fetchall()
    ofertas = [json.loads(l[0]) for l in linhas]
    ofertas = [o for o in ofertas if o.get("preco") and desconto_confiavel(o)]
    ofertas.sort(key=lambda o: o.get("desconto") or 0, reverse=True)
    return ofertas[:quantidade]


def tamanho_fila():
    with _conectar() as con:
        return con.execute("SELECT COUNT(*) FROM fila").fetchone()[0]


def fila_por_loja():
    """{'Shopee': 12, 'Mercado Livre': 3, 'Seus posts': 1}"""
    contagem = {}
    with _conectar() as con:
        for chave, dados in con.execute("SELECT chave, dados FROM fila").fetchall():
            loja = _loja(chave, json.loads(dados) if dados else None) or "Seus posts"
            contagem[loja] = contagem.get(loja, 0) + 1
    return contagem


def limpar_fila():
    with _conectar() as con:
        con.execute("DELETE FROM fila")


def esquecer_antigos(dias=30):
    """Depois de X dias a mesma oferta pode voltar a ser postada."""
    with _conectar() as con:
        con.execute("DELETE FROM postados WHERE quando < ?", (time.time() - dias * 86400,))


def proxima_para_whats(horas=12):
    """A oferta postada nas últimas X horas, com maior desconto, que ainda não foi pro WhatsApp."""
    with _conectar() as con:
        linhas = con.execute(
            "SELECT chave, dados FROM postados WHERE dados IS NOT NULL AND whats IS NULL AND quando >= ?",
            (time.time() - horas * 3600,),
        ).fetchall()
    candidatas = []
    for chave, dados in linhas:
        d = json.loads(dados)
        if d.get("preco") and d.get("link") and desconto_confiavel(d):
            candidatas.append((d.get("desconto") or 0, chave, d))
    if not candidatas:
        return None
    candidatas.sort(key=lambda c: c[0], reverse=True)
    return candidatas[0][1], candidatas[0][2]


def proximas_para_tiktok(n=1, horas=24):
    """As n ofertas de maior desconto que ainda não viraram vídeo no TikTok (24h; se faltar, 72h)."""
    for janela in (horas, max(horas, 72)):
        with _conectar() as con:
            linhas = con.execute(
                "SELECT chave, dados FROM postados WHERE dados IS NOT NULL AND tiktok IS NULL AND quando >= ?",
                (time.time() - janela * 3600,),
            ).fetchall()
        candidatas = []
        for chave, dados in linhas:
            d = json.loads(dados)
            if d.get("preco") and desconto_confiavel(d):
                candidatas.append((d.get("desconto") or 0, chave, d))
        if len(candidatas) >= n:
            candidatas.sort(key=lambda c: c[0], reverse=True)
            return [(c[1], c[2]) for c in candidatas[:n]]
    return []


def ler_valor(nome, padrao=0):
    with _conectar() as con:
        linha = con.execute("SELECT valor FROM contadores WHERE nome=?", (nome,)).fetchone()
    return linha[0] if linha else padrao


def gravar_valor(nome, valor):
    with _conectar() as con:
        con.execute("INSERT OR REPLACE INTO contadores (nome, valor) VALUES (?, ?)", (nome, valor))


def proximo_formato(formatos):
    """Rodízio que não depende de horário: cada chamada devolve o próximo formato da lista."""
    with _conectar() as con:
        linha = con.execute("SELECT valor FROM contadores WHERE nome='formato_tiktok'").fetchone()
        valor = (linha[0] if linha else -1) + 1
        con.execute("INSERT OR REPLACE INTO contadores (nome, valor) VALUES ('formato_tiktok', ?)", (valor,))
    return formatos[valor % len(formatos)]


def proxima_para_tiktok(horas=24):
    """A oferta de maior desconto das últimas X horas que ainda não virou vídeo no TikTok.

    Se todas as das últimas X horas já viraram vídeo, procura nas últimas 72h.
    """
    for janela in (horas, max(horas, 72)):
        with _conectar() as con:
            linhas = con.execute(
                "SELECT chave, dados FROM postados WHERE dados IS NOT NULL AND tiktok IS NULL AND quando >= ?",
                (time.time() - janela * 3600,),
            ).fetchall()
        candidatas = []
        for chave, dados in linhas:
            d = json.loads(dados)
            if d.get("preco") and desconto_confiavel(d):
                candidatas.append((d.get("desconto") or 0, chave, d))
        if candidatas:
            candidatas.sort(key=lambda c: c[0], reverse=True)
            return candidatas[0][1], candidatas[0][2]
    return None


def marcar_tiktok(chave):
    with _conectar() as con:
        con.execute("UPDATE postados SET tiktok=? WHERE chave=?", (time.time(), chave))


def marcar_whats(chave):
    with _conectar() as con:
        con.execute("UPDATE postados SET whats=? WHERE chave=?", (time.time(), chave))


def nota_oferta(d):
    """Quanto maior, melhor: desconto primeiro, depois quanto a pessoa economiza em reais."""
    desconto = d.get("desconto") or 0
    economia = (d.get("preco_original") or 0) - (d.get("preco") or 0)
    return desconto + min(max(economia, 0), 1000) / 100  # até +10 pontos por economia


def melhor_para_face(horas=12, desconto_minimo=30, coluna="face"):
    """A melhor oferta postada nas últimas X horas que ainda não foi pro Facebook (ou pro Instagram).

    Posts manuais (que você escolheu) passam na frente. Ofertas automáticas só entram
    com desconto >= desconto_minimo, foto e preço. Sem nenhuma boa, devolve None.
    """
    with _conectar() as con:
        linhas = con.execute(
            f"SELECT chave, dados FROM postados WHERE dados IS NOT NULL AND {_coluna(coluna)} IS NULL AND quando >= ?",
            (time.time() - horas * 3600,),
        ).fetchall()
    candidatas = []
    for chave, dados in linhas:
        d = json.loads(dados)
        if not (d.get("preco") and d.get("link") and d.get("imagem")):
            continue
        if not desconto_confiavel(d):
            continue
        manual = chave.startswith("manual:")
        if not manual and (d.get("desconto") or 0) < desconto_minimo:
            continue
        candidatas.append((manual, nota_oferta(d), chave, d))
    if not candidatas:
        return None
    candidatas.sort(key=lambda c: (c[0], c[1]), reverse=True)
    return candidatas[0][2], candidatas[0][3]


def _coluna(nome):
    if nome not in ("face", "insta"):
        raise ValueError(nome)
    return nome


def marcar_face(chave, coluna="face"):
    with _conectar() as con:
        con.execute(f"UPDATE postados SET {_coluna(coluna)}=? WHERE chave=?", (time.time(), chave))


def postados_face_hoje(inicio_do_dia, coluna="face"):
    with _conectar() as con:
        return con.execute(f"SELECT COUNT(*) FROM postados WHERE {_coluna(coluna)} >= ?",
                           (inicio_do_dia,)).fetchone()[0]


# ------------------------------------------------------------------ histórico de preços

def registrar_preco(chave, preco):
    """Guarda o preço visto agora (no máximo 1 registro a cada 6h por produto, ou quando o preço muda)."""
    if not (chave and preco):
        return
    with _conectar() as con:
        ultimo = con.execute("SELECT preco, quando FROM precos WHERE chave=? ORDER BY quando DESC LIMIT 1",
                             (chave,)).fetchone()
        if ultimo and abs(ultimo[0] - preco) < 0.01 and time.time() - ultimo[1] < 6 * 3600:
            return
        con.execute("INSERT INTO precos (chave, preco, quando) VALUES (?,?,?)", (chave, preco, time.time()))


def historico_preco(chave, dias=30):
    """(menor preço, maior preço, há quantos dias acompanhamos) antes de agora, ou None sem histórico."""
    with _conectar() as con:
        linha = con.execute(
            "SELECT MIN(preco), MAX(preco), MIN(quando), COUNT(*) FROM precos WHERE chave=? AND quando >= ?",
            (chave, time.time() - dias * 86400),
        ).fetchone()
    if not linha or not linha[3]:
        return None
    return linha[0], linha[1], (time.time() - linha[2]) / 86400


def selo_preco(chave, preco, dias=30, minimo_dias=7):
    """Texto do selo quando o preço de agora é o menor que o robô já viu no período."""
    hist = historico_preco(chave, dias)
    if not hist:
        return None
    menor, maior, acompanhando = hist
    if acompanhando < minimo_dias or preco > menor + 0.01 or maior <= preco:
        return None  # pouco histórico, não é o menor, ou o preço nunca mudou
    return f"📉 Menor preço dos últimos {min(dias, int(acompanhando))} dias"


def esquecer_precos(dias=90):
    with _conectar() as con:
        con.execute("DELETE FROM precos WHERE quando < ?", (time.time() - dias * 86400,))


# ------------------------------------------------------------------ alertas de oferta forte

def ja_alertado(chave):
    with _conectar() as con:
        return con.execute("SELECT 1 FROM alertas WHERE chave=?", (chave,)).fetchone() is not None


def alerta_avisado(chave):
    """True se o alerta dessa oferta já chegou no seu privado (com o kit do WhatsApp)."""
    with _conectar() as con:
        return con.execute("SELECT 1 FROM alertas WHERE chave=? AND avisado IS NOT NULL",
                           (chave,)).fetchone() is not None


def marcar_alerta(chave, motivo=None, avisado=True):
    agora = time.time()
    with _conectar() as con:
        con.execute("INSERT OR REPLACE INTO alertas (chave, quando, avisado, motivo) VALUES (?,?,?,?)",
                    (chave, agora, agora if avisado else None, motivo))


def marcar_alerta_avisado(chave):
    with _conectar() as con:
        con.execute("UPDATE alertas SET avisado=? WHERE chave=?", (time.time(), chave))


def alertas_pendentes(horas=14):
    """Alertas que apareceram fora do horário e ainda não chegaram no seu privado: [(chave, motivo, dados)]."""
    with _conectar() as con:
        linhas = con.execute("SELECT chave, motivo FROM alertas WHERE avisado IS NULL AND quando >= ?",
                             (time.time() - horas * 3600,)).fetchall()
        pendentes = []
        for chave, motivo in linhas:
            achado = (con.execute("SELECT dados FROM fila WHERE chave=? AND dados IS NOT NULL", (chave,)).fetchone()
                      or con.execute("SELECT dados FROM postados WHERE chave=? AND dados IS NOT NULL",
                                     (chave,)).fetchone())
            if achado:
                pendentes.append((chave, motivo or "oferta forte", json.loads(achado[0])))
            else:  # saiu da fila sem ser postada: não tem mais por que avisar
                con.execute("UPDATE alertas SET avisado=? WHERE chave=?", (time.time(), chave))
    return pendentes


def alertas_hoje(inicio_do_dia):
    with _conectar() as con:
        return con.execute("SELECT COUNT(*) FROM alertas WHERE quando >= ?", (inicio_do_dia,)).fetchone()[0]


# ------------------------------------------------------------------ produto repetido com outro nome

_PALAVRAS_VAZIAS = {"de", "da", "do", "das", "dos", "e", "com", "para", "pra", "em", "a", "o", "as", "os", "por", "na", "no", "original", "novo", "promocao", "oferta", "envio", "imediato"}


def palavras_do_titulo(titulo):
    """Conjunto de palavras do título, sem acento, sem pontuação e sem palavras vazias."""
    t = unicodedata.normalize("NFKD", (titulo or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    # separa número de letra ("8000dpi" -> "8000", "dpi"; "1tb" -> "1", "tb")
    partes = re.findall(r"[a-z]+|[0-9]+", t)
    return frozenset(p for p in partes if p not in _PALAVRAS_VAZIAS and (len(p) > 1 or p.isdigit()))


def parecido(a, b, limite=0.7):
    """True quando dois títulos têm quase as mesmas palavras (mesmo produto, outro vendedor/anúncio).

    Os números precisam ser iguais: SSD 1TB e SSD 2TB, ou RTX 4060 e RTX 4070, são produtos diferentes.
    """
    if len(a) < 4 or len(b) < 4:
        return False
    if {p for p in a if p.isdigit()} != {p for p in b if p.isdigit()}:
        return False
    return len(a & b) / len(a | b) >= limite


def titulos_recentes(dias=3):
    """Palavras dos títulos que estão na fila ou foram postados nos últimos X dias."""
    with _conectar() as con:
        linhas = con.execute("SELECT dados FROM fila WHERE dados IS NOT NULL").fetchall()
        linhas += con.execute("SELECT dados FROM postados WHERE dados IS NOT NULL AND quando >= ?",
                              (time.time() - dias * 86400,)).fetchall()
    titulos = []
    for (dados,) in linhas:
        try:
            titulos.append(palavras_do_titulo(json.loads(dados).get("titulo")))
        except ValueError:
            continue
    return titulos


# ------------------------------------------------------------------ relatório semanal

def resumo_periodo(dias=7):
    """Números dos últimos X dias para o relatório."""
    desde = time.time() - dias * 86400
    with _conectar() as con:
        linhas = con.execute("SELECT chave, dados, face, insta, whats, tiktok FROM postados WHERE quando >= ?",
                             (desde,)).fetchall()
        alertas = con.execute("SELECT COUNT(*) FROM alertas WHERE quando >= ?", (desde,)).fetchone()[0]
    por_loja, ofertas = {}, []
    canais = {"face": 0, "insta": 0, "whats": 0, "tiktok": 0}
    for chave, dados, face, insta, whats, tiktok in linhas:
        d = json.loads(dados) if dados else {}
        loja = "Seus posts" if chave.startswith("manual:") and not d.get("fonte") else _loja(chave, d)
        por_loja[loja] = por_loja.get(loja, 0) + 1
        for nome, valor in (("face", face), ("insta", insta), ("whats", whats), ("tiktok", tiktok)):
            canais[nome] += 1 if valor else 0
        if d.get("preco") and d.get("desconto"):
            ofertas.append(d)
    ofertas.sort(key=lambda o: o.get("desconto") or 0, reverse=True)
    descontos = [o["desconto"] for o in ofertas]
    return {"total": len(linhas), "por_loja": por_loja, "canais": canais, "alertas": alertas,
            "media_desconto": round(sum(descontos) / len(descontos)) if descontos else 0, "top": ofertas[:5]}
