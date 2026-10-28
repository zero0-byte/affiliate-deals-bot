"""Lê as configurações do arquivo .env."""
import os
from pathlib import Path

from dotenv import load_dotenv

PASTA = Path(__file__).resolve().parent


def _achar_env():
    """Procura o .env na pasta do robô e nas pastas acima dela.

    Assim você deixa o .env (e o robo.db) numa pasta fixa e extrai as versões novas dentro dela,
    sem precisar copiar nada a cada atualização.
    """
    for pasta in [PASTA, *list(PASTA.parents)[:3]]:
        if (pasta / ".env").is_file():
            return pasta
    return PASTA


PASTA_DADOS = _achar_env()  # onde ficam o .env e o robo.db
ARQUIVO_ENV = PASTA_DADOS / ".env"
load_dotenv(ARQUIVO_ENV)


def _ler_env_na_mao():
    """Plano B: linhas que o python-dotenv não entendeu (ex.: cookie com aspas ou caractere estranho).

    Lê cada linha NOME=valor do jeito que está e preenche o que ficou vazio. Devolve as linhas com problema.
    """
    problemas = []
    try:
        linhas = ARQUIVO_ENV.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return problemas
    for n, linha in enumerate(linhas, 1):
        txt = linha.strip()
        if not txt or txt.startswith("#"):
            continue
        if "=" not in txt:
            problemas.append(n)
            continue
        nome, valor = txt.split("=", 1)
        nome = nome.strip().removeprefix("export ").strip()
        if not nome.replace("_", "").isalnum():
            problemas.append(n)
            continue
        valor = valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "'\"":
            valor = valor[1:-1]
        if valor and not os.getenv(nome, "").strip():
            os.environ[nome] = valor
    return problemas


LINHAS_RUINS_ENV = _ler_env_na_mao()  # números das linhas do .env sem NOME=valor (aparece no /status)


def _texto(nome, padrao=""):
    return os.getenv(nome, padrao).strip()


def _numero(nome, padrao):
    valor = _texto(nome)
    try:
        return int(valor) if valor else padrao
    except ValueError:
        raise SystemExit(f"Configuração inválida: {nome} precisa ser um número (está '{valor}').")


def _lista(nome):
    return [p.strip() for p in _texto(nome).split(",") if p.strip()]


# --- Nicho: "todos" = promoção de tudo (casa, beleza, moda, eletrônicos... e também PC/gamer);
#     "gamer" = só PC gamer, como era antes ---
NICHO = (_texto("NICHO") or "todos").lower()
TODOS = NICHO != "gamer"

# --- Telegram ---
TELEGRAM_TOKEN = _texto("TELEGRAM_TOKEN")
CANAL = _texto("CANAL")  # @nomedocanal ou -100xxxxxxxxxx
MARCA = _texto("MARCA") or "Ofertas do Dia"  # nome que aparece nas artes e vídeos
ADMIN_ID = _numero("ADMIN_ID", 0)  # seu ID no Telegram (descubra com /id)

# --- Shopee (busca automática) ---
SHOPEE_APP_ID = _texto("SHOPEE_APP_ID")
SHOPEE_SECRET = _texto("SHOPEE_SECRET")
# Enquanto a API não sai: cookie do portal de afiliados, para gerar os links s.shopee.com.br (não compartilhe!)
SHOPEE_COOKIE = _texto("SHOPEE_COOKIE")

# --- AliExpress (busca automática) ---
ALI_APP_KEY = _texto("ALI_APP_KEY")
ALI_APP_SECRET = _texto("ALI_APP_SECRET")
ALI_TRACKING_ID = _texto("ALI_TRACKING_ID")

# --- Mercado Livre (converte links comuns em link de afiliado usando a sua sessão) ---
ML_COOKIE = _texto("ML_COOKIE")  # cookie da sessão do portal de afiliados (não compartilhe!)
ML_TAG = _texto("ML_TAG") or ""  # sua etiqueta de afiliado no ML
# Busca automática na página de ofertas do ML (1 = ligada). Usa o mesmo cookie.
ML_OFERTAS = _numero("ML_OFERTAS", 1) == 1
# Páginas de ofertas lidas a cada busca, separadas por vírgula.
# Padrão: NICHO=todos -> ofertas do dia de todas as categorias (2 páginas) + Informática; gamer -> Informática + Games
_ML_PAGINAS_GAMER = [
    "https://www.mercadolivre.com.br/ofertas?category=MLB1648",  # Informática
    "https://www.mercadolivre.com.br/ofertas?category=MLB1144",  # Games
]
_ML_PAGINAS_TODOS = [
    "https://www.mercadolivre.com.br/ofertas",  # ofertas do dia, todas as categorias
    "https://www.mercadolivre.com.br/ofertas?page=2",
    "https://www.mercadolivre.com.br/ofertas?category=MLB1648",  # Informática (o público gamer continua)
]
ML_OFERTAS_URLS = _lista("ML_OFERTAS_URL")
if not ML_OFERTAS_URLS or ML_OFERTAS_URLS == _ML_PAGINAS_GAMER[:1]:  # vazio ou .env antigo, só com Informática
    ML_OFERTAS_URLS = _ML_PAGINAS_TODOS if TODOS else _ML_PAGINAS_GAMER
# Só posta ofertas cujo título tenha alguma dessas palavras ("*" = posta tudo da página).
# Com NICHO=todos e ML_FILTRO vazio, posta de tudo.
ML_FILTRO = [p.lower() for p in _lista("ML_FILTRO")] or (["*"] if TODOS else [
    "gamer", "ssd", "nvme", "placa de vídeo", "placa de video", "rtx", "radeon", "geforce", "mouse",
    "teclado", "headset", "fone", "monitor", "memória", "memoria", "ram", "ddr4", "ddr5", "processador",
    "ryzen", "intel core", "fonte", "gabinete", "cooler", "water cooler", "notebook", "placa mãe",
    "placa-mãe", "webcam", "microfone", "controle", "mousepad", "hd externo", "pendrive", "roteador",
    "console", "playstation", "ps5", "ps4", "xbox", "nintendo", "switch", "steam deck", "cadeira gamer",
])

# --- Página do Facebook (API oficial) ---
FB_PAGE_ID = _texto("FB_PAGE_ID")
FB_PAGE_TOKEN = _texto("FB_PAGE_TOKEN")
FB_POSTS_DIA = _numero("FB_POSTS_DIA", 8)  # quantos posts por dia na página (só as melhores)
# Desconto mínimo para uma oferta automática ir pro Facebook (posts manuais sempre podem ir)
FB_DESCONTO_MINIMO = _numero("FB_DESCONTO_MINIMO", 30)


def _horarios_face():
    """Horários dos posts no Facebook: FB_HORARIOS no .env, ou FB_POSTS_DIA espalhados no dia."""
    if _texto("FB_HORARIOS"):
        return sorted({int(h) for h in _lista("FB_HORARIOS") if h.isdigit() and 0 <= int(h) <= 23})
    padrao = [9, 11, 13, 15, 17, 19, 20, 21]
    if FB_POSTS_DIA == len(padrao):
        return padrao
    if FB_POSTS_DIA <= 0:
        return []
    inicio, fim = 9, 21
    passo = (fim - inicio) / max(FB_POSTS_DIA - 1, 1)
    return sorted({round(inicio + i * passo) for i in range(FB_POSTS_DIA)})


FB_HORARIOS = _horarios_face()

# --- Instagram (API oficial, pela mesma Página/token do Facebook) ---
INSTA = _numero("INSTA", 0) == 1  # 1 = robô posta sozinho no Instagram
IG_USER_ID = _texto("IG_USER_ID")  # vazio = o robô descobre sozinho pela Página do Facebook
IG_TOKEN = _texto("IG_TOKEN")  # vazio = usa o FB_PAGE_TOKEN (precisa das permissões do Instagram)
IG_HORARIOS = sorted({int(h) for h in _lista("IG_HORARIOS") if h.isdigit() and 0 <= int(h) <= 23}) \
    if _texto("IG_HORARIOS") else [10, 13, 16, 19, 21]
IG_DESCONTO_MINIMO = _numero("IG_DESCONTO_MINIMO", 30)
IG_STORIES = _numero("IG_STORIES", 1) == 1  # 1 = cada post do feed também vai pro Story
IG_REELS = _numero("IG_REELS", 1) == 1  # 1 = o vídeo diário "achados de hoje" vai pro Reels
HASHTAGS = (_texto("HASHTAGS") or
            ("#achadinhos #promoção #ofertas #desconto #compras #achadosdashopee #fyp" if TODOS else
             "#pcgamer #setupgamer #promoção #achadinhos #hardware #ofertas #tecnologia"))  # legendas dos vídeos
IG_HASHTAGS = _texto("IG_HASHTAGS") or HASHTAGS

# --- Alerta de oferta forte: te avisa na hora e a oferta fura a fila ---
ALERTA_DESCONTO = _numero("ALERTA_DESCONTO", 45)  # desconto a partir do qual vira alerta (0 = desligado)
ALERTA_QUEDA = _numero("ALERTA_QUEDA", 15)  # ou: preço caiu X% abaixo do menor preço já visto
ALERTA_MAX_DIA = _numero("ALERTA_MAX_DIA", 4)  # no máximo X alertas por dia

# --- KaBuM! (pela Awin): seu ID de publisher na Awin. O link vira awin1.com/cread.php?... ---
AWIN_ID = _texto("AWIN_ID")
KABUM_AWIN_MID = _texto("KABUM_AWIN_MID") or "17729"  # código da KaBuM! na Awin

# --- Amazon (modo manual: coloca sua tag nos links) ---
AMAZON_TAG = _texto("AMAZON_TAG") or ""  # sua ID de rastreamento da Amazon

# --- O que buscar ---
PALAVRAS_CHAVE = _lista("PALAVRAS_CHAVE") or ["mouse gamer", "teclado mecânico", "headset gamer", "ssd nvme", "placa de vídeo"]
# Com NICHO=todos, o robô também busca na Shopee estas palavras de outras categorias, intercaladas com as
# PALAVRAS_CHAVE (que continuam valendo). Troque a lista em PALAVRAS_GERAIS no .env se quiser.
PALAVRAS_GERAIS = _lista("PALAVRAS_GERAIS") or [
    "fone bluetooth", "air fryer", "organizador", "skincare", "smartwatch", "garrafa térmica", "tênis",
    "caixa de som bluetooth", "utensílios de cozinha", "maquiagem", "luminária led", "mochila", "perfume",
    "carregador turbo", "ferramentas", "brinquedo", "pet", "academia fitness", "panela", "relógio",
    "bolsa feminina", "capinha celular", "roupa de cama", "aspirador", "infantil",
]
if TODOS:  # intercala: 2 gerais, 1 do seu nicho, 2 gerais, 1 do nicho...
    _gerais, _nicho, _juntas = list(PALAVRAS_GERAIS), list(PALAVRAS_CHAVE), []
    while _gerais or _nicho:
        _juntas += _gerais[:2] + _nicho[:1]
        _gerais, _nicho = _gerais[2:], _nicho[1:]
    PALAVRAS_CHAVE = list(dict.fromkeys(_juntas))
DESCONTO_MINIMO = _numero("DESCONTO_MINIMO", 20)  # em %
# Desconto acima disso quase sempre é preço "De" inflado pelo vendedor: a oferta é descartada. 0 = sem limite.
DESCONTO_MAXIMO = _numero("DESCONTO_MAXIMO", 60)
PRECO_MAXIMO = _numero("PRECO_MAXIMO", 0)  # 0 = sem limite
OFERTAS_POR_BUSCA = _numero("OFERTAS_POR_BUSCA", 5)  # por plataforma e palavra-chave
PALAVRAS_POR_BUSCA = _numero("PALAVRAS_POR_BUSCA", 5 if TODOS else 3)  # quantas palavras-chave por rodada (Shopee/AliExpress)
# Ordens da busca na Shopee, alternadas: 2 = mais vendidos, 5 = maior comissão, 1 = relevância
SHOPEE_ORDENS = [int(o) for o in _lista("SHOPEE_ORDENS") if o.isdigit()] or [2, 5]

# --- Agenda ---
INTERVALO_BUSCA_MIN = _numero("INTERVALO_BUSCA_MIN", 60)
INTERVALO_POST_MIN = _numero("INTERVALO_POST_MIN", 20)
HORA_INICIO = _numero("HORA_INICIO", 8)
HORA_FIM = _numero("HORA_FIM", 22)
# Oferta automática que passou desse tempo na fila sem ser postada é descartada (o preço já pode ter mudado)
FILA_MAX_HORAS = _numero("FILA_MAX_HORAS", 12)
# Relatório da semana no seu privado, todo domingo às 21h (0 = desligado). Também com /relatorio
RELATORIO = _numero("RELATORIO", 1) == 1
FUSO = _texto("FUSO", "America/Sao_Paulo")

# --- Artes do Instagram ---
CRIATIVOS = _numero("CRIATIVOS", 1) == 1  # 1 = manda a arte de cada oferta publicada no seu privado
VIDEO_HORA = _numero("VIDEO_HORA", 20)  # hora do vídeo diário "achados de hoje" (-1 = desligado)

# --- Kit WhatsApp: horários em que o robô separa a melhor oferta pra você postar no WhatsApp ---
WHATS_HORARIOS = [int(h) for h in _lista("WHATS_HORARIOS") if h.isdigit() and 0 <= int(h) <= 23] \
    if _texto("WHATS_HORARIOS") else [9, 12, 18, 21]

# --- Link do seu canal no WhatsApp (vai nos posts e nas artes). Vazio = não mostra. ---
WHATS_CANAL = _texto("WHATS_CANAL") or ""
# O que aparece escrito nas artes (imagem não tem link clicável). Ex: "link na bio" ou "bit.ly/seucanal"
WHATS_ARTE = _texto("WHATS_ARTE") or "link na bio"


def whats_canal_curto():
    """Link do canal sem 'https://' e sem 'www.' (fica mais limpo na arte)."""
    link = WHATS_CANAL.split("?")[0].rstrip("/")
    for prefixo in ("https://", "http://", "www."):
        if link.lower().startswith(prefixo):
            link = link[len(prefixo):]
    return link


# --- Voz dos vídeos (narração) ---
# VOZ_MOTOR: edge (grátis, vozes neurais da Microsoft, precisa de internet), elevenlabs (pago) ou nenhum
VOZ_MOTOR = (_texto("VOZ_MOTOR") or "edge").lower()
VOZ = _texto("VOZ") or "pt-BR-AntonioNeural"  # rode ouvir-vozes.bat para escutar as opções
VOZ_VELOCIDADE = _numero("VOZ_VELOCIDADE", 8)  # em %: 0 = normal, 10 = um pouco mais rápida
VOZ_TOM = _numero("VOZ_TOM", 0)  # em Hz: negativo = mais grave
VOZ_MUSICA = _numero("VOZ_MUSICA", 30)  # volume da batida por baixo da voz, em % (0 a 100)
ELEVENLABS_API_KEY = _texto("ELEVENLABS_API_KEY")
ELEVENLABS_VOZ_ID = _texto("ELEVENLABS_VOZ_ID")

# --- TikTok: o vídeo diário vai para os RASCUNHOS do app (você só escolhe o som e publica) ---
TIKTOK = _numero("TIKTOK", 0) == 1
TIKTOK_CLIENT_KEY = _texto("TIKTOK_CLIENT_KEY")
TIKTOK_CLIENT_SECRET = _texto("TIKTOK_CLIENT_SECRET")
# Formato do vídeo do TikTok: alternar (rodízio entre todos), um formato fixo (adivinha, conversa, pov,
# nao_compre, duelo, comparacao), uma lista separada por vírgula (ex.: adivinha,pov,duelo) ou achados
TIKTOK_FORMATO = (_texto("TIKTOK_FORMATO") or "alternar").lower().replace(" ", "")
# Horários dos vídeos do TikTok (até 5 por dia, limite do TikTok). Vazio = só na VIDEO_HORA
TIKTOK_HORARIOS = sorted({int(h) for h in _lista("TIKTOK_HORARIOS") if h.isdigit() and 0 <= int(h) <= 23})[:5]
# rascunho = vai pros rascunhos do app | privado = posta direto com título e hashtags, como "Somente eu"
TIKTOK_MODO = (_texto("TIKTOK_MODO") or "rascunho").lower()

# --- Texto ---
# Aviso de afiliado (a Amazon exige). Vazio no .env = usa o texto padrão; RODAPE=nenhum desliga.
RODAPE = _texto("RODAPE") or "Link de afiliado: posso receber comissão pelas compras."
if RODAPE.lower() in ("nenhum", "0", "desligado"):
    RODAPE = ""

ARQUIVO_BANCO = PASTA_DADOS / "robo.db"


def shopee_ativo():
    return bool(SHOPEE_APP_ID and SHOPEE_SECRET)


def aliexpress_ativo():
    return bool(ALI_APP_KEY and ALI_APP_SECRET and ALI_TRACKING_ID)


def validar():
    faltando = [n for n, v in [("TELEGRAM_TOKEN", TELEGRAM_TOKEN), ("CANAL", CANAL)] if not v]
    if faltando:
        raise SystemExit("Preencha no arquivo .env: " + ", ".join(faltando))


def ml_ativo():
    return bool(ML_COOKIE and ML_TAG)


def facebook_ativo():
    return bool(FB_PAGE_ID and FB_PAGE_TOKEN)


def insta_ativo():
    return INSTA and bool(IG_TOKEN or FB_PAGE_TOKEN) and bool(IG_USER_ID or FB_PAGE_ID)
