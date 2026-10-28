"""Painel do robô de promoções.

Clique duas vezes neste arquivo (ou no atalho da Área de Trabalho) para abrir a janela.
Pela janela você liga e desliga o robô, vê o que ele está fazendo e aperta os botões
de "Publicar agora", "Buscar ofertas", "Vídeo pro TikTok" etc. sem abrir o VS Code.

O robô continua sendo o mesmo bot.py: o painel só liga ele por baixo e mostra o que acontece.
Os botões precisam do painel_ponte.py (fica na mesma pasta).
"""
import json
import os
import queue
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from hashlib import md5
from pathlib import Path
from tkinter import messagebox, ttk

PASTA = Path(__file__).resolve().parent
WINDOWS = sys.platform == "win32"


def _achar_dados():
    """Mesma regra do config.py: o .env e o robo.db ficam na pasta do robô ou numa pasta acima."""
    for pasta in [PASTA, *list(PASTA.parents)[:3]]:
        if (pasta / ".env").is_file():
            return pasta
    return PASTA


PASTA_DADOS = _achar_dados()
ARQUIVO_ENV = PASTA_DADOS / ".env"
ARQUIVO_BANCO = PASTA_DADOS / "robo.db"
ARQUIVO_REGISTRO = PASTA_DADOS / "painel-registro.log"
ARQUIVO_PREFS = PASTA / "painel.json"

# Cores da marca
FUNDO = "#121212"
CARTAO = "#1C1C1C"
CARTAO2 = "#262626"
BORDA = "#2E2E2E"
AMARELO = "#FFD000"
AMARELO_ESCURO = "#E0B700"
TEXTO = "#F4F4F4"
SUAVE = "#9C9C9C"
VERDE = "#35D07F"
VERMELHO = "#FF5C5C"
LARANJA = "#FFA53D"

AJUSTES = [  # (nome no .env, rótulo, padrão, dica)
    ("HORA_INICIO", "Começa a postar às (hora)", "8", "0 a 23"),
    ("HORA_FIM", "Para de postar às (hora)", "22", "0 a 23"),
    ("INTERVALO_POST_MIN", "Posta a cada (minutos)", "20", "ex.: 20"),
    ("INTERVALO_BUSCA_MIN", "Busca ofertas a cada (minutos)", "60", "ex.: 60"),
    ("DESCONTO_MINIMO", "Desconto mínimo (%)", "20", "ex.: 20"),
    ("DESCONTO_MAXIMO", "Desconto máximo (%)", "60", "acima disso é preço \"De\" inflado"),
    ("PRECO_MAXIMO", "Preço máximo (R$)", "0", "0 = sem limite"),
    ("PALAVRAS_CHAVE", "Palavras-chave da busca", "mouse gamer, teclado mecânico, headset gamer, ssd nvme, placa de vídeo",
     "separe por vírgula"),
    ("VIDEO_HORA", "Hora do vídeo diário", "20", "-1 = desligado"),
    ("TIKTOK_HORARIOS", "Horários do TikTok", "", "ex.: 12,20 (até 5)"),
    ("WHATS_HORARIOS", "Horários do kit do WhatsApp", "", "ex.: 10,15,19"),
]
NUMEROS = {"HORA_INICIO", "HORA_FIM", "INTERVALO_POST_MIN", "INTERVALO_BUSCA_MIN", "DESCONTO_MINIMO",
           "DESCONTO_MAXIMO", "PRECO_MAXIMO", "VIDEO_HORA"}
LISTAS_HORAS = {"TIKTOK_HORARIOS", "WHATS_HORARIOS"}

FORMATOS_PADRAO = {"adivinha": "adivinha o preço", "conversa": "conversa", "pov": "POV",
                   "nao_compre": "não compre antes de ver", "duelo": "duelo", "comparacao": "comparação"}


# ---------------------------------------------------------------- Windows: fonte, ícone, nitidez

def _preparar_windows():
    if not WINDOWS:
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # letras nítidas em tela com zoom
    except Exception:  # noqa: BLE001
        pass
    try:  # ícone próprio na barra de tarefas (em vez do ícone do Python)
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DealsBot.Painel")
    except Exception:  # noqa: BLE001
        pass
    for nome in ("Inter-Bold.otf", "Inter-SemiBold.otf", "Inter-Black.otf"):
        arq = PASTA / "arte" / nome
        if arq.is_file():
            try:
                ctypes.windll.gdi32.AddFontResourceExW(str(arq), 0x10, 0)  # só para este programa
            except Exception:  # noqa: BLE001
                pass


def _so_uma_janela():
    """Impede abrir o painel duas vezes (dois robôs ao mesmo tempo dão erro no Telegram)."""
    trava = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        trava.bind(("127.0.0.1", 47613))
    except OSError:
        return None
    return trava


# ---------------------------------------------------------------- preferências e .env

def ler_prefs():
    try:
        return json.loads(ARQUIVO_PREFS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def salvar_prefs(prefs):
    try:
        ARQUIVO_PREFS.write_text(json.dumps(prefs, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def ler_env():
    """Só as linhas NOME=valor. Os valores secretos nunca aparecem na tela."""
    valores = {}
    try:
        linhas = ARQUIVO_ENV.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return valores
    for linha in linhas:
        txt = linha.strip()
        if not txt or txt.startswith("#") or "=" not in txt:
            continue
        nome, valor = txt.split("=", 1)
        valor = valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "'\"":
            valor = valor[1:-1]
        valores[nome.strip().removeprefix("export ").strip()] = valor
    return valores


def gravar_env(novos):
    """Troca só as linhas dos ajustes; o resto do .env (tokens, cookies...) fica como está."""
    bruto = ARQUIVO_ENV.read_bytes() if ARQUIVO_ENV.is_file() else b""
    texto = bruto.decode("utf-8-sig", errors="replace")
    quebra = "\r\n" if "\r\n" in texto else "\n"
    linhas = texto.splitlines()
    faltando = dict(novos)
    for i, linha in enumerate(linhas):
        txt = linha.strip()
        if txt.startswith("#") or "=" not in txt:
            continue
        nome = txt.split("=", 1)[0].strip().removeprefix("export ").strip()
        if nome in faltando:
            linhas[i] = f"{nome}={faltando.pop(nome)}"
    if faltando:
        linhas += ["", "# Ajustes feitos pelo painel"] + [f"{n}={v}" for n, v in faltando.items()]
    if bruto:
        (PASTA_DADOS / ".env.antes-do-painel").write_bytes(bruto)  # cópia de segurança
    ARQUIVO_ENV.write_text(quebra.join(linhas) + quebra, encoding="utf-8", newline="")


# ---------------------------------------------------------------- leitura do robo.db

def _db():
    con = sqlite3.connect(ARQUIVO_BANCO, timeout=2)
    con.execute(
        "CREATE TABLE IF NOT EXISTS painel_comandos ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, comando TEXT, arg TEXT, criado REAL,"
        " status TEXT DEFAULT 'novo', resposta TEXT, terminado REAL)"
    )
    con.execute("CREATE TABLE IF NOT EXISTS painel_estado (nome TEXT PRIMARY KEY, valor TEXT)")
    return con


def _tem_tabela(con, nome):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nome,)).fetchone() is not None


def ler_situacao():
    """Tudo que a tela mostra, numa leitura só."""
    s = {"robo": None, "fila": 0, "hoje": 0, "posts": [], "proximas": [], "pausado_db": None}
    if not ARQUIVO_BANCO.is_file():
        return s
    with _db() as con:
        linha = con.execute("SELECT valor FROM painel_estado WHERE nome='robo'").fetchone()
        if linha:
            try:
                s["robo"] = json.loads(linha[0])
            except ValueError:
                pass
        if _tem_tabela(con, "fila"):
            s["fila"] = con.execute("SELECT COUNT(*) FROM fila").fetchone()[0]
            s["proximas"] = con.execute(
                "SELECT criado, dados, texto, prioridade FROM fila ORDER BY prioridade DESC, id ASC LIMIT 60").fetchall()
        if _tem_tabela(con, "postados"):
            meia_noite = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            s["hoje"] = con.execute("SELECT COUNT(*) FROM postados WHERE quando >= ?", (meia_noite,)).fetchone()[0]
            s["posts"] = con.execute(
                "SELECT quando, dados, chave FROM postados ORDER BY quando DESC LIMIT 60").fetchall()
        if _tem_tabela(con, "contadores"):
            linha = con.execute("SELECT valor FROM contadores WHERE nome='pausado'").fetchone()
            if linha:
                s["pausado_db"] = bool(int(linha[0] or 0))
    return s


def pedir(comando, arg=""):
    with _db() as con:
        return con.execute("INSERT INTO painel_comandos (comando, arg, criado) VALUES (?,?,?)",
                           (comando, arg, time.time())).lastrowid


def respostas(ids):
    if not ids:
        return []
    with _db() as con:
        marcas = ",".join("?" * len(ids))
        return con.execute(f"SELECT id, comando, status, resposta FROM painel_comandos WHERE id IN ({marcas}) "
                           "AND status IN ('ok','erro','expirado')", list(ids)).fetchall()


# ---------------------------------------------------------------- exportar log (para mandar pro suporte)

SEGREDOS = ("TOKEN", "SECRET", "COOKIE", "KEY", "SENHA", "PASS", "AUTH")


def _area_de_trabalho():
    if WINDOWS:
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0 and Path(buf.value).is_dir():
                return Path(buf.value)  # funciona com a Área de Trabalho do OneDrive
        except Exception:  # noqa: BLE001
            pass
    casa = Path.home() / "Desktop"
    return casa if casa.is_dir() else PASTA_DADOS


def _final_do_arquivo(arq, linhas=1500):
    try:
        return arq.read_text(encoding="utf-8", errors="replace").splitlines()[-linhas:]
    except OSError as e:
        return [f"(não consegui ler: {e})"]


def exportar_log(situacao):
    """Junta log + situação num .txt na Área de Trabalho, sem tokens nem cookies. Devolve o caminho."""
    env = ler_env()
    secretos = []
    for nome, valor in env.items():
        if any(p in nome.upper() for p in SEGREDOS) and valor:
            secretos.append(valor)
            secretos += [v.split("=", 1)[-1] for v in valor.split(";") if len(v.split("=", 1)[-1].strip()) >= 8]
    secretos = sorted({x.strip() for x in secretos if len(x.strip()) >= 6}, key=len, reverse=True)

    def limpar(texto):
        for x in secretos:
            texto = texto.replace(x, "***")
        return texto

    agora = datetime.now()
    out = [f"LOG DO ROBÔ DE PROMOÇÕES — exportado em {agora:%d/%m/%Y %H:%M}",
           f"Pasta do robô: {PASTA}", f"Pasta do .env: {PASTA_DADOS} ({'achei o .env' if ARQUIVO_ENV.is_file() else 'SEM .env'})",
           f"Python: {sys.version.split()[0]} | Windows: {WINDOWS}", ""]

    out.append("=== .env (tokens e cookies só aparecem como preenchido/vazio) ===")
    for nome, valor in env.items():
        if any(p in nome.upper() for p in SEGREDOS):
            out.append(f"{nome} = {'(preenchido, ' + str(len(valor)) + ' letras)' if valor else '(VAZIO)'}")
        else:
            out.append(f"{nome} = {valor}")
    out.append("")

    out.append("=== Situação ===")
    r = situacao.get("robo") or {}
    if r:
        visto = time.time() - r.get("visto", 0)
        out.append(f"Último sinal do robô: há {int(visto)} s ({'ligado' if visto < 12 else 'parece desligado'})")
        out.append(f"Pausado: {r.get('pausado')} | No horário: {r.get('no_horario')} | Horário: {r.get('horario')}")
        out.append("Lojas: " + ", ".join(f"{n} {'ligada' if ok else 'desligada'}" for n, ok in r.get("lojas", {}).items()))
        for n, t in r.get("canais", {}).items():
            out.append(f"{n}: {t}")
    else:
        out.append("O robô ainda não mandou sinal pro painel (desligado ou bot.py sem o painel_ponte).")
    try:
        with _db() as con:
            if _tem_tabela(con, "fila"):
                por_loja = {}
                for (dados,) in con.execute("SELECT dados FROM fila"):
                    loja = (json.loads(dados) if dados else {}).get("fonte") or "manual"
                    por_loja[loja] = por_loja.get(loja, 0) + 1
                out.append(f"Fila: {sum(por_loja.values())} — " + (", ".join(f"{k}: {v}" for k, v in por_loja.items()) or "vazia"))
            if _tem_tabela(con, "postados"):
                por_loja = {}
                for (dados,) in con.execute("SELECT dados FROM postados WHERE quando >= ?", (time.time() - 86400,)):
                    loja = (json.loads(dados) if dados else {}).get("fonte") or "manual"
                    por_loja[loja] = por_loja.get(loja, 0) + 1
                out.append(f"Postados nas últimas 24h: {sum(por_loja.values())} — "
                           + (", ".join(f"{k}: {v}" for k, v in por_loja.items()) or "nenhum"))
            if _tem_tabela(con, "painel_comandos"):
                out.append("Últimos botões do painel:")
                for cmd, st, resp, criado in con.execute(
                        "SELECT comando, status, resposta, criado FROM painel_comandos ORDER BY id DESC LIMIT 10"):
                    out.append(f"  {datetime.fromtimestamp(criado):%d/%m %H:%M} {cmd}: {st} — {resp or ''}")
    except (sqlite3.Error, ValueError) as e:
        out.append(f"(não consegui ler o robo.db: {e})")
    out.append("")

    logs = [ARQUIVO_REGISTRO] + sorted({a for pasta in {PASTA, PASTA_DADOS} for a in pasta.glob("*.log")
                                        if a.resolve() != ARQUIVO_REGISTRO.resolve()
                                        and not a.name.endswith(".antigo.log")})
    for arq in logs:
        if arq.is_file():
            out.append(f"=== {arq.name} (últimas linhas) ===")
            out += _final_do_arquivo(arq)
            out.append("")

    destino = _area_de_trabalho() / f"log-robo-promocoes-{agora:%Y%m%d-%H%M}.txt"
    destino.write_text(limpar("\n".join(out)) + "\n", encoding="utf-8")
    return destino


# ---------------------------------------------------------------- o processo do robô

class Robo:
    """Liga o bot.py por baixo (sem janela preta) e junta tudo que ele escreve."""

    def __init__(self, ao_escrever):
        self.proc = None
        self.inicio = None
        self.parado_por_mim = True
        self.ao_escrever = ao_escrever
        self.preparando = False

    @staticmethod
    def python():
        exe = Path(sys.executable)
        if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").is_file():
            return str(exe.parent / "python.exe")
        return str(exe)

    def ligado(self):
        return self.proc is not None and self.proc.poll() is None

    def _rodar(self, args):
        flags = subprocess.CREATE_NO_WINDOW if WINDOWS else 0
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        return subprocess.Popen(args, cwd=PASTA, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, creationflags=flags, env=env,
                                encoding="utf-8", errors="replace")

    def _ler(self, proc):
        for linha in proc.stdout:
            self.ao_escrever(linha.rstrip())

    def ligar(self, prefs):
        """Instala as bibliotecas se o requirements.txt mudou e liga o bot.py."""
        if self.ligado() or self.preparando:
            return
        self.preparando = True
        self.parado_por_mim = False
        threading.Thread(target=self._ligar, args=(prefs,), daemon=True).start()

    def _ligar(self, prefs):
        try:
            req = PASTA / "requirements.txt"
            marca = md5(req.read_bytes()).hexdigest() if req.is_file() else ""
            if marca and prefs.get("requirements") != marca:
                self.ao_escrever("[painel] Conferindo as bibliotecas do robô (só na primeira vez ou depois de atualizar)...")
                pip = self._rodar([self.python(), "-m", "pip", "install", "-q", "--disable-pip-version-check",
                                   "-r", "requirements.txt"])
                self._ler(pip)
                if pip.wait() == 0:
                    prefs["requirements"] = marca
                    salvar_prefs(prefs)
                else:
                    self.ao_escrever("[painel] ERRO: não consegui instalar as bibliotecas. Veja as linhas acima.")
            if self.parado_por_mim:
                return
            self.ao_escrever("[painel] Ligando o robô...")
            self.proc = self._rodar([self.python(), "-u", "bot.py"])
            self.inicio = time.time()
            threading.Thread(target=self._ler, args=(self.proc,), daemon=True).start()
        except Exception as e:  # noqa: BLE001
            self.ao_escrever(f"[painel] ERRO ao ligar o robô: {e}")
        finally:
            self.preparando = False

    def desligar(self):
        self.parado_por_mim = True
        proc = self.proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.inicio = None


# ---------------------------------------------------------------- peças da tela

class Botao(tk.Label):
    """Botão chapado com cor da marca e efeito ao passar o mouse."""

    def __init__(self, pai, texto, comando, cor=CARTAO2, cor_texto=TEXTO, fonte=None, largura=None, **kw):
        super().__init__(pai, text=texto, bg=cor, fg=cor_texto, font=fonte, cursor="hand2",
                         padx=14, pady=9, width=largura, **kw)
        self.cor, self.comando, self.ativo = cor, comando, True
        self.bind("<Enter>", lambda e: self.ativo and self.config(bg=self._clarear(self.cor)))
        self.bind("<Leave>", lambda e: self.config(bg=self.cor))
        self.bind("<Button-1>", lambda e: self.ativo and self.comando and self.comando())

    @staticmethod
    def _clarear(cor):
        r, g, b = (int(cor[i:i + 2], 16) for i in (1, 3, 5))
        return "#%02x%02x%02x" % tuple(min(255, int(c + (255 - c) * 0.12)) for c in (r, g, b))

    def mudar(self, texto=None, cor=None, cor_texto=None, ativo=None):
        if texto is not None:
            self.config(text=texto)
        if cor is not None:
            self.cor = cor
            self.config(bg=cor)
        if cor_texto is not None:
            self.config(fg=cor_texto)
        if ativo is not None:
            self.ativo = ativo
            self.config(bg=self.cor)
            self.config(cursor="hand2" if ativo else "arrow", fg=(cor_texto or self.cget("fg")) if ativo else "#666")


def cartao(pai, **kw):
    return tk.Frame(pai, bg=CARTAO, highlightbackground=BORDA, highlightthickness=1, **kw)


class Painel:
    def __init__(self, raiz):
        self.raiz = raiz
        self.prefs = ler_prefs()
        self.fila_log = queue.Queue()
        self.robo = Robo(self.fila_log.put)
        self.pedidos = {}  # id -> nome do botão
        self.quedas = []  # horários em que o robô caiu (para não ficar religando sem parar)
        self.religar_em = None
        self.situacao = {}
        self.ultimo_sem_ponte = 0
        self._fontes()
        self._montar()
        self._carregar_registro()
        self.raiz.protocol("WM_DELETE_WINDOW", self.fechar)
        self.raiz.after(200, self._tique_log)
        self.raiz.after(300, self._tique)
        if self.prefs.get("ligar_ao_abrir", False):
            self.raiz.after(800, self.ligar)

    # ------------------------------------------------ aparência
    def _fontes(self):
        familias = set(tkfont.families())
        base = next((f for f in ("Inter", "Inter SemiBold", "Segoe UI", "Helvetica") if f in familias), "TkDefaultFont")
        negrito = "Inter" if "Inter" in familias else base
        self.f_txt = (base, 10)
        self.f_peq = (base, 9)
        self.f_bold = (negrito, 10, "bold")
        self.f_titulo = (negrito, 17, "bold")
        self.f_num = (negrito, 22, "bold")
        self.f_grande = (negrito, 12, "bold")
        self.f_mono = ("Consolas" if "Consolas" in familias else "Courier", 9)

        st = ttk.Style(self.raiz)
        st.theme_use("clam")
        sem_borda = dict(bordercolor=FUNDO, lightcolor=FUNDO, darkcolor=FUNDO)
        st.configure("TNotebook", background=FUNDO, borderwidth=0, tabmargins=(0, 0, 0, 0), **sem_borda)
        st.configure("TNotebook.Tab", background=CARTAO, foreground=SUAVE, padding=(16, 8), borderwidth=0,
                     font=self.f_bold, **sem_borda)
        st.map("TNotebook.Tab", background=[("selected", CARTAO2)], foreground=[("selected", AMARELO)],
               lightcolor=[("selected", CARTAO2)], bordercolor=[("selected", FUNDO)])
        st.configure("Treeview", background=CARTAO, fieldbackground=CARTAO, foreground=TEXTO, borderwidth=0,
                     rowheight=28, font=self.f_txt)
        st.configure("Treeview.Heading", background=CARTAO2, foreground=SUAVE, borderwidth=0, font=self.f_bold)
        st.map("Treeview", background=[("selected", "#3A3200")], foreground=[("selected", AMARELO)])
        st.map("Treeview.Heading", background=[("active", CARTAO2)])
        st.configure("Vertical.TScrollbar", background=CARTAO2, troughcolor=CARTAO, borderwidth=0, arrowcolor=SUAVE,
                     **sem_borda)
        st.configure("Treeview", **sem_borda)

    def _imagem(self, arquivo, tamanho):
        try:
            from PIL import Image, ImageTk
            img = Image.open(arquivo).convert("RGBA").resize((tamanho, tamanho), Image.LANCZOS)
            return ImageTk.PhotoImage(img)
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------ montagem da janela
    def _montar(self):
        r = self.raiz
        r.title("Painel de Ofertas")
        r.configure(bg=FUNDO)
        larg, alt = min(1120, r.winfo_screenwidth() - 40), min(800, r.winfo_screenheight() - 80)
        r.geometry(f"{larg}x{alt}+{max(0, (r.winfo_screenwidth() - larg) // 2)}+10")
        r.minsize(900, 560)
        if r.winfo_screenheight() < 860 and WINDOWS:
            r.state("zoomed")  # tela pequena (notebook): abre maximizado
        ico = PASTA / "arte" / "painel.ico"
        if WINDOWS and ico.is_file():
            r.iconbitmap(default=str(ico))
        else:
            self._icone = self._imagem(PASTA / "arte" / "logo.png", 64)
            if self._icone:
                r.iconphoto(True, self._icone)

        # Cabeçalho
        topo = tk.Frame(r, bg=FUNDO)
        topo.pack(fill="x", padx=20, pady=(16, 10))
        self._logo = self._imagem(PASTA / "arte" / "logo.png", 52)
        if self._logo:
            tk.Label(topo, image=self._logo, bg=FUNDO).pack(side="left")
        titulos = tk.Frame(topo, bg=FUNDO)
        titulos.pack(side="left", padx=12)
        tk.Label(titulos, text="Painel de Ofertas", font=self.f_titulo, bg=FUNDO, fg=TEXTO).pack(anchor="w")
        tk.Label(titulos, text="Painel do robô de ofertas · versão 3", font=self.f_txt, bg=FUNDO, fg=SUAVE).pack(anchor="w")

        self.selo = tk.Canvas(topo, width=230, height=40, bg=FUNDO, highlightthickness=0)
        self.selo.pack(side="right")

        # Linha de controle
        ctrl = cartao(r)
        ctrl.pack(fill="x", padx=20, pady=(0, 12))
        dentro = tk.Frame(ctrl, bg=CARTAO)
        dentro.pack(fill="x", padx=14, pady=(12, 8))
        self.b_ligar = Botao(dentro, "▶  Ligar robô", self.ligar_desligar, cor=AMARELO, cor_texto="#111",
                             fonte=self.f_grande, largura=16)
        self.b_ligar.pack(side="left")
        self.b_pausa = Botao(dentro, "⏸  Pausar robô", self.pausar_retomar, fonte=self.f_grande, largura=16)
        self.b_pausa.pack(side="left", padx=10)
        Botao(dentro, "⚙  Ajustes", self.abrir_ajustes, fonte=self.f_grande).pack(side="left")
        Botao(dentro, "📤  Exportar log", self.exportar, fonte=self.f_grande).pack(side="left", padx=10)

        opcoes = tk.Frame(ctrl, bg=CARTAO)  # numa linha própria, para caber em tela pequena
        opcoes.pack(fill="x", padx=14, pady=(0, 10))
        self.v_religar = tk.BooleanVar(value=self.prefs.get("religar", True))
        self.v_ao_abrir = tk.BooleanVar(value=self.prefs.get("ligar_ao_abrir", False))
        for txt, var in [("Religar sozinho se cair", self.v_religar), ("Ligar o robô ao abrir o painel", self.v_ao_abrir)]:
            tk.Checkbutton(opcoes, text=txt, variable=var, command=self._salvar_opcoes, bg=CARTAO, fg=TEXTO,
                           selectcolor=CARTAO2, activebackground=CARTAO, activeforeground=AMARELO,
                           font=self.f_txt, highlightthickness=0, bd=0).pack(side="left", padx=(0, 24))

        # Números
        numeros = tk.Frame(r, bg=FUNDO)
        numeros.pack(fill="x", padx=20, pady=(0, 12))
        self.tiles = {}
        for i, (chave, rotulo) in enumerate([("ligado", "Robô"), ("fila", "Na fila"), ("hoje", "Postados hoje"),
                                             ("horario", "Horário de posts")]):
            c = cartao(numeros)
            c.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 10, 0))
            numeros.columnconfigure(i, weight=1, uniform="t")
            tk.Label(c, text=rotulo.upper(), font=self.f_peq, bg=CARTAO, fg=SUAVE).pack(anchor="w", padx=14, pady=(12, 0))
            valor = tk.Label(c, text="—", font=self.f_num, bg=CARTAO, fg=TEXTO)
            valor.pack(anchor="w", padx=14)
            sub = tk.Label(c, text="", font=self.f_peq, bg=CARTAO, fg=SUAVE)
            sub.pack(anchor="w", padx=14, pady=(0, 12))
            self.tiles[chave] = (valor, sub)

        # Ações rápidas
        acoes = cartao(r)
        acoes.pack(fill="x", padx=20, pady=(0, 12))
        tk.Label(acoes, text="Ações rápidas", font=self.f_bold, bg=CARTAO, fg=TEXTO).pack(anchor="w", padx=14, pady=(12, 8))
        grade = tk.Frame(acoes, bg=CARTAO)
        grade.pack(fill="x", padx=14, pady=(0, 14))
        self.botoes_acao = []
        lista = [
            ("🚀  Publicar agora", "postar", None),
            ("🔎  Buscar ofertas", "buscar", None),
            ("🎵  Vídeo pro TikTok", "tiktok", "Monta o vídeo e manda pros rascunhos do TikTok?"),
            ("😄  Vídeo casual ▾", "casual", None),
            ("🎬  Vídeo 3 achados", "video", None),
            ("💬  Kit WhatsApp", "whats", None),
            ("📘  Postar no Facebook", "face", "Postar agora a melhor oferta na página do Facebook?"),
            ("📸  Postar no Instagram", "insta", "Postar agora a melhor oferta no Instagram?"),
            ("📊  Relatório", "relatorio", None),
            ("🧹  Limpar fila", "limpar", "Apagar TODAS as ofertas que estão esperando na fila?"),
        ]
        for i, (txt, cmd, confirmar) in enumerate(lista):
            b = Botao(grade, txt, None, fonte=self.f_txt, anchor="w")
            b.comando = (lambda b=b: self.menu_casual(b)) if cmd == "casual" else \
                (lambda c=cmd, t=txt, q=confirmar: self.acao(c, t, q))
            b.grid(row=i // 5, column=i % 5, sticky="ew", padx=(0 if i % 5 == 0 else 8, 0), pady=4)
            self.botoes_acao.append(b)
        for c in range(5):
            grade.columnconfigure(c, weight=1, uniform="a")

        # Rodapé (antes das abas, para nunca sumir em tela pequena)
        rodape = tk.Frame(r, bg=FUNDO)
        rodape.pack(side="bottom", fill="x", padx=20, pady=(0, 10))
        self.l_aviso = tk.Label(rodape, text="", font=self.f_txt, bg=FUNDO, fg=SUAVE, anchor="w")
        self.l_aviso.pack(side="left", fill="x", expand=True)
        Botao(rodape, "📁  Abrir pasta do robô", self.abrir_pasta, cor=FUNDO, cor_texto=SUAVE, fonte=self.f_peq).pack(side="right")
        Botao(rodape, "📤  Exportar log", self.exportar, cor=AMARELO, cor_texto="#111", fonte=self.f_bold).pack(side="right", padx=10)

        # Abas
        abas = ttk.Notebook(r)
        abas.pack(fill="both", expand=True, padx=20, pady=(0, 8))
        self.abas = abas

        f_log = tk.Frame(abas, bg=CARTAO)
        self.txt_log = tk.Text(f_log, bg="#0D0D0D", fg="#D6D6D6", insertbackground=TEXTO, font=self.f_mono,
                               bd=0, padx=10, pady=8, wrap="word", state="disabled", highlightthickness=0)
        sb = ttk.Scrollbar(f_log, command=self.txt_log.yview)
        self.txt_log.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt_log.pack(fill="both", expand=True)
        for tag, cor in [("erro", VERMELHO), ("aviso", LARANJA), ("ok", VERDE), ("painel", AMARELO), ("hora", "#6F6F6F")]:
            self.txt_log.tag_config(tag, foreground=cor)
        abas.add(f_log, text="Registro")

        self.t_posts = self._tabela(abas, "Últimos posts", [("hora", "Quando", 120), ("loja", "Loja", 110),
                                                          ("produto", "Produto", 520), ("preco", "Preço", 100),
                                                          ("desc", "Desconto", 90)])
        self.t_fila = self._tabela(abas, "Fila", [("hora", "Entrou", 120), ("loja", "Loja", 110),
                                                  ("produto", "Produto", 520), ("preco", "Preço", 100),
                                                  ("desc", "Desconto", 90)])
        f_lojas = tk.Frame(abas, bg=CARTAO)
        tk.Label(f_lojas, text="Lojas", font=self.f_bold, bg=CARTAO, fg=TEXTO).pack(anchor="w", padx=16, pady=(14, 4))
        self.q_lojas = tk.Frame(f_lojas, bg=CARTAO)
        self.q_lojas.pack(fill="x", padx=16)
        tk.Label(f_lojas, text="Canais", font=self.f_bold, bg=CARTAO, fg=TEXTO).pack(anchor="w", padx=16, pady=(14, 4))
        self.q_canais = tk.Frame(f_lojas, bg=CARTAO)
        self.q_canais.pack(fill="x", padx=16, pady=(0, 14))
        abas.add(f_lojas, text="Lojas e canais")

    def _tabela(self, abas, titulo, colunas):
        f = tk.Frame(abas, bg=CARTAO)
        t = ttk.Treeview(f, columns=[c[0] for c in colunas], show="headings", selectmode="browse")
        for nome, rotulo, larg in colunas:
            t.heading(nome, text=rotulo, anchor="w")
            t.column(nome, width=larg, anchor="w", stretch=nome == "produto")
        sb = ttk.Scrollbar(f, command=t.yview)
        t.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        t.pack(fill="both", expand=True)
        t.tag_configure("par", background="#202020")
        abas.add(f, text=titulo)
        return t

    def abrir_ajustes(self):
        if getattr(self, "janela_ajustes", None) and self.janela_ajustes.winfo_exists():
            self.janela_ajustes.lift()
            return
        j = self.janela_ajustes = tk.Toplevel(self.raiz, bg=CARTAO)
        j.title("Ajustes do robô")
        j.transient(self.raiz)
        j.resizable(False, False)
        tk.Label(j, text="Ajustes do robô", font=self.f_grande, bg=CARTAO, fg=TEXTO).pack(anchor="w", padx=20, pady=(16, 2))
        tk.Label(j, text="Mude o básico sem abrir o .env. Tokens e cookies não aparecem aqui e não são mexidos.",
                 font=self.f_txt, bg=CARTAO, fg=SUAVE).pack(anchor="w", padx=20, pady=(0, 10))
        grade = tk.Frame(j, bg=CARTAO)
        grade.pack(fill="x", padx=20)
        self.campos = {}
        for i, (nome, rotulo, padrao, dica) in enumerate(AJUSTES):
            tk.Label(grade, text=rotulo, font=self.f_txt, bg=CARTAO, fg=TEXTO).grid(row=i, column=0, sticky="w",
                                                                                      padx=(0, 12), pady=4)
            e = tk.Entry(grade, bg=CARTAO2, fg=TEXTO, insertbackground=TEXTO, relief="flat", font=self.f_txt,
                         width=46 if nome == "PALAVRAS_CHAVE" else 12, highlightthickness=1,
                         highlightbackground=BORDA, highlightcolor=AMARELO)
            e.grid(row=i, column=1, sticky="w", pady=4, ipady=4)
            tk.Label(grade, text=dica, font=self.f_peq, bg=CARTAO, fg="#777").grid(
                row=i if nome != "PALAVRAS_CHAVE" else i, column=2, sticky="w", padx=(10, 0))
            self.campos[nome] = e
        botoes = tk.Frame(j, bg=CARTAO)
        botoes.pack(fill="x", padx=20, pady=(14, 18))
        Botao(botoes, "💾  Salvar", self.salvar_ajustes, cor=AMARELO, cor_texto="#111",
              fonte=self.f_bold).pack(side="left")
        Botao(botoes, "Fechar", j.destroy, fonte=self.f_bold).pack(side="left", padx=10)
        self.carregar_ajustes()

    # ------------------------------------------------ ajustes
    def carregar_ajustes(self):
        env = ler_env()
        for nome, _, padrao, _ in AJUSTES:
            e = self.campos[nome]
            e.delete(0, "end")
            e.insert(0, env.get(nome) or padrao)

    def salvar_ajustes(self):
        novos = {}
        for nome, rotulo, _, _ in AJUSTES:
            valor = self.campos[nome].get().strip()
            if nome in NUMEROS:
                try:
                    int(valor)
                except ValueError:
                    messagebox.showwarning("Ajustes", f"\"{rotulo}\" precisa ser um número.", parent=self.janela_ajustes)
                    return
                if nome.startswith("HORA_") and not 0 <= int(valor) <= 24:
                    messagebox.showwarning("Ajustes", f"\"{rotulo}\" precisa ser uma hora de 0 a 24.", parent=self.janela_ajustes)
                    return
            if nome in LISTAS_HORAS:
                horas = [h.strip() for h in valor.split(",") if h.strip()]
                if any(not h.isdigit() or int(h) > 23 for h in horas):
                    messagebox.showwarning("Ajustes", f"\"{rotulo}\": use horas de 0 a 23 separadas por vírgula.", parent=self.janela_ajustes)
                    return
                valor = ",".join(horas)
            novos[nome] = valor
        env = ler_env()
        mudou = {n: v for n, v in novos.items() if env.get(n, "") != v
                 and not (n not in env and v == next(a[2] for a in AJUSTES if a[0] == n))}
        if not mudou:
            self.avisar("Nada mudou nos ajustes.")
            return
        try:
            gravar_env(mudou)
        except OSError as e:
            messagebox.showerror("Ajustes", f"Não consegui salvar o .env:\n{e}", parent=self.janela_ajustes)
            return
        self.avisar(f"Ajustes salvos ({', '.join(mudou)}). Cópia do .env antigo: .env.antes-do-painel", VERDE)
        self.escrever_painel(f"Ajustes salvos no .env: {', '.join(mudou)}")
        if self.robo.ligado():
            if messagebox.askyesno("Ajustes", "Salvo! Religar o robô agora para valer?", parent=self.janela_ajustes):
                self.robo.desligar()
                self.raiz.after(1500, self.ligar)
        elif self._robo_vivo():
            messagebox.showinfo("Ajustes", "Salvo! Feche e abra o robô de novo para valer.", parent=self.janela_ajustes)
        else:
            messagebox.showinfo("Ajustes", "Salvo! Vale na próxima vez que ligar o robô.", parent=self.janela_ajustes)
        self.janela_ajustes.destroy()

    # ------------------------------------------------ ações
    def _salvar_opcoes(self):
        self.prefs["religar"] = self.v_religar.get()
        self.prefs["ligar_ao_abrir"] = self.v_ao_abrir.get()
        salvar_prefs(self.prefs)

    def _robo_vivo(self):
        r = self.situacao.get("robo")
        return bool(r and time.time() - r.get("visto", 0) < 12)

    def ligar_desligar(self):
        if self.robo.ligado() or self.robo.preparando:
            if messagebox.askyesno("Desligar", "Desligar o robô? Ele para de buscar e postar ofertas."):
                self.religar_em = None
                self.robo.desligar()
                self.escrever_painel("Robô desligado.")
        elif self._robo_vivo():
            messagebox.showinfo("Já ligado", "O robô já está ligado fora do painel (talvez na janela preta do "
                                             "ligar-robo.bat).\n\nOs botões funcionam mesmo assim. Para ligar e "
                                             "desligar por aqui, feche aquela janela primeiro.")
        else:
            self.ligar()

    def ligar(self):
        if self._robo_vivo() and not self.robo.ligado():
            self.escrever_painel("O robô já está ligado fora do painel; não liguei outro.")
            return
        self.religar_em = None
        self.robo.ligar(self.prefs)

    def pausar_retomar(self):
        pausado = self._pausado()
        self.acao("retomar" if pausado else "pausar", "Retomar robô" if pausado else "Pausar robô")

    def _pausado(self):
        r = self.situacao.get("robo")
        if r and self._robo_vivo():
            return r.get("pausado")
        return bool(self.situacao.get("pausado_db"))

    def acao(self, comando, nome, confirmar=None):
        if not self._robo_vivo():
            if self.robo.ligado():
                self.avisar("O robô está ligando, espere uns segundos e tente de novo.", LARANJA)
            else:
                self.avisar("Ligue o robô primeiro.", LARANJA)
            return
        if confirmar and not messagebox.askyesno(nome.split("  ")[-1], confirmar):
            return
        arg = ""
        if ":" in comando:
            comando, arg = comando.split(":", 1)
        try:
            cid = pedir(comando, arg)
        except sqlite3.Error as e:
            self.avisar(f"Não consegui mandar o pedido ({e}).", VERMELHO)
            return
        self.pedidos[cid] = nome.split("  ")[-1].replace(" ▾", "")
        self.avisar(f"⏳ {self.pedidos[cid]}: pedido enviado ao robô...", AMARELO)

    def menu_casual(self, botao):
        formatos = (self.situacao.get("robo") or {}).get("formatos") or FORMATOS_PADRAO
        m = tk.Menu(self.raiz, tearoff=0, bg=CARTAO2, fg=TEXTO, activebackground=AMARELO, activeforeground="#111",
                    font=self.f_txt, bd=0)
        for chave, nome in formatos.items():
            m.add_command(label=f"  {nome}  ", command=lambda c=chave, n=nome: self.acao(f"casual:{c}", f"x  Vídeo {n}"))
        m.tk_popup(botao.winfo_rootx(), botao.winfo_rooty() + botao.winfo_height())

    def abrir_pasta(self):
        if WINDOWS:
            os.startfile(PASTA_DADOS)  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", str(PASTA_DADOS)])

    def exportar(self):
        try:
            arq = exportar_log(self.situacao)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Exportar log", f"Não consegui criar o arquivo:\n{e}")
            return
        self.escrever_painel(f"Log exportado: {arq}")
        self.avisar(f"✅ Log salvo na Área de Trabalho: {arq.name}", VERDE)
        if WINDOWS:
            subprocess.Popen(["explorer", "/select,", str(arq)])  # abre a pasta com o arquivo marcado
        messagebox.showinfo("Exportar log", f"Pronto! O arquivo {arq.name} está na sua Área de Trabalho.\n\n"
                                            "Tokens e cookies foram apagados dele. É só anexar na conversa com o Claude.")

    def avisar(self, texto, cor=SUAVE):
        self.l_aviso.config(text=texto, fg=cor)

    # ------------------------------------------------ registro (log)
    def _carregar_registro(self):
        try:
            linhas = ARQUIVO_REGISTRO.read_text(encoding="utf-8", errors="replace").splitlines()[-300:]
        except OSError:
            linhas = []
        for linha in linhas:
            self._mostrar_linha(linha)
        # Abre já no final (o mais recente), e de novo quando a janela terminar de desenhar
        self._abrindo = True
        self.txt_log.see("end")
        self.raiz.after(600, self._ir_pro_fim)
        self.escrever_painel(f"Painel aberto. Pasta do robô: {PASTA}")
        if not ARQUIVO_ENV.is_file():
            self.escrever_painel("ATENÇÃO: não achei o .env. O painel precisa ficar dentro da pasta robo-promocoes.")

    def _ir_pro_fim(self):
        self._abrindo = False
        self.txt_log.see("end")

    def escrever_painel(self, texto):
        self.fila_log.put(f"{datetime.now():%Y-%m-%d %H:%M:%S} [painel] {texto}")

    def _mostrar_linha(self, linha):
        tag = None
        baixo = linha.lower()
        if "[painel]" in linha:
            tag = "painel"
        elif " error " in baixo or "traceback" in baixo or "erro" in baixo:
            tag = "erro"
        elif " warning " in baixo or "⚠" in linha:
            tag = "aviso"
        elif "publicado" in baixo or "postado" in baixo or "✅" in linha:
            tag = "ok"
        t = self.txt_log
        t.config(state="normal")
        if len(linha) > 20 and linha[4] == "-" and linha[13] == ":":
            hora, resto = linha[11:19], linha[19:]
            resto = resto.split(",", 1)[-1][3:] if resto.startswith(",") else resto
            t.insert("end", hora + " ", "hora")
            t.insert("end", resto.strip() + "\n", tag)
        else:
            t.insert("end", linha + "\n", tag)
        if int(t.index("end-1c").split(".")[0]) > 3000:
            t.delete("1.0", "500.0")
        t.config(state="disabled")

    def _tique_log(self):
        novas = []
        try:
            while len(novas) < 300:
                novas.append(self.fila_log.get_nowait())
        except queue.Empty:
            pass
        if novas:
            # Segue o final só se você já estava lá embaixo (se rolou pra cima pra ler, não pula)
            no_fim = getattr(self, "_abrindo", False) or self.txt_log.yview()[1] > 0.98
            for linha in novas:
                if linha.startswith("[painel]"):
                    linha = f"{datetime.now():%Y-%m-%d %H:%M:%S} {linha}"
                self._mostrar_linha(linha)
            if no_fim:
                self.txt_log.see("end")
            try:
                if ARQUIVO_REGISTRO.is_file() and ARQUIVO_REGISTRO.stat().st_size > 3_000_000:
                    ARQUIVO_REGISTRO.replace(ARQUIVO_REGISTRO.with_suffix(".antigo.log"))
                with ARQUIVO_REGISTRO.open("a", encoding="utf-8") as arq:
                    arq.write("\n".join(novas) + "\n")
            except OSError:
                pass
        self.raiz.after(250, self._tique_log)

    # ------------------------------------------------ atualização da tela
    def _tique(self):
        try:
            self.situacao = ler_situacao()
        except sqlite3.Error:
            pass  # banco ocupado: tenta de novo daqui a pouco
        self._ver_queda()
        self._ver_respostas()
        self._atualizar()
        self.raiz.after(2000, self._tique)

    def _ver_queda(self):
        p = self.robo.proc
        if p is None or p.poll() is None or self.robo.preparando:
            if self.religar_em and time.time() >= self.religar_em and not self.robo.ligado():
                self.religar_em = None
                self.ligar()
            return
        codigo = p.returncode
        self.robo.proc = None
        self.robo.inicio = None
        if self.robo.parado_por_mim:
            return
        self.escrever_painel(f"O robô parou sozinho (código {codigo}). Veja as linhas acima para entender.")
        self.quedas = [q for q in self.quedas if time.time() - q < 600] + [time.time()]
        if not self.v_religar.get():
            self.avisar("O robô parou. Veja o Registro.", VERMELHO)
        elif len(self.quedas) > 4:
            self.escrever_painel("Caiu 5 vezes em 10 minutos; não vou religar sozinho. Confira o .env e o Registro.")
            self.avisar("O robô caiu várias vezes seguidas. Veja o Registro.", VERMELHO)
        else:
            self.religar_em = time.time() + 20
            self.escrever_painel("Religando em 20 segundos...")

    def _ver_respostas(self):
        if not self.pedidos:
            return
        try:
            feitas = respostas(list(self.pedidos))
        except sqlite3.Error:
            return
        for cid, comando, status, resposta in feitas:
            nome = self.pedidos.pop(cid, comando)
            if status == "ok":
                self.avisar(f"✅ {nome}: {resposta}", VERDE)
            elif status == "expirado":
                self.avisar(f"{nome}: o robô não respondeu a tempo.", LARANJA)
            else:
                self.avisar(f"⚠️ {nome}: {resposta}", VERMELHO)

    def _selo(self, texto, cor):
        c = self.selo
        c.delete("all")
        larg = 230
        c.create_oval(larg - 222, 6, larg - 194, 34, fill=CARTAO2, outline="")
        c.create_oval(larg - 214, 14, larg - 202, 26, fill=cor, outline="")
        c.create_text(larg - 186, 20, text=texto, anchor="w", fill=cor, font=self.f_grande)

    def _atualizar(self):
        s = self.situacao
        r = s.get("robo") or {}
        vivo = self._robo_vivo()
        nosso = self.robo.ligado()
        pausado = self._pausado()

        if self.robo.preparando or (nosso and not vivo and time.time() - (self.robo.inicio or 0) < 40):
            estado, cor = "Ligando...", AMARELO
        elif vivo and pausado:
            estado, cor = "Pausado", LARANJA
        elif vivo or nosso:
            estado, cor = "Rodando", VERDE
        elif self.religar_em:
            estado, cor = "Religando...", LARANJA
        else:
            estado, cor = "Desligado", VERMELHO
        self._selo(estado, cor)

        if nosso or self.robo.preparando:
            self.b_ligar.mudar("■  Desligar robô", cor=CARTAO2, cor_texto=VERMELHO)
        elif vivo:
            self.b_ligar.mudar("●  Ligado fora do painel", cor=CARTAO2, cor_texto=SUAVE)
        else:
            self.b_ligar.mudar("▶  Ligar robô", cor=AMARELO, cor_texto="#111")
        self.b_pausa.mudar("▶  Retomar robô" if pausado else "⏸  Pausar robô",
                           cor_texto=LARANJA if pausado else TEXTO, ativo=vivo)
        for b in self.botoes_acao:
            b.mudar(ativo=vivo)

        # botões sem resposta: robô ligado há um tempo, mas sem painel_ponte no bot.py
        if nosso and not vivo and self.robo.inicio and time.time() - self.robo.inicio > 60 \
                and time.time() - self.ultimo_sem_ponte > 600:
            self.ultimo_sem_ponte = time.time()
            self.escrever_painel("O robô está ligado, mas os botões não respondem: falta o painel_ponte no bot.py "
                                 "(atualize o bot.py para a v4.5).")

        v, sub = self.tiles["ligado"]
        v.config(text=estado, fg=cor)
        if nosso and self.robo.inicio:
            minutos = int((time.time() - self.robo.inicio) // 60)
            sub.config(text=f"ligado há {minutos // 60}h{minutos % 60:02d}" if minutos >= 60 else f"ligado há {minutos} min")
        elif vivo:
            sub.config(text="ligado fora do painel")
        else:
            sub.config(text="clique em Ligar robô")
        v, sub = self.tiles["fila"]
        v.config(text=str(s.get("fila", 0)))
        sub.config(text=f"posta 1 a cada {r['intervalo_post']} min" if r.get("intervalo_post") else "posts esperando")
        v, sub = self.tiles["hoje"]
        v.config(text=str(s.get("hoje", 0)))
        sub.config(text=f"no canal {r['canal']}" if r.get("canal") else "no canal do Telegram")
        v, sub = self.tiles["horario"]
        env = None
        if r.get("horario"):
            v.config(text=r["horario"].replace(" às ", "–"))
            sub.config(text="dentro do horário agora" if r.get("no_horario") else "fora do horário agora")
        else:
            env = ler_env()
            v.config(text=f"{env.get('HORA_INICIO') or 8}h–{env.get('HORA_FIM') or 22}h")
            sub.config(text="segundo o .env")

        self._lojas_canais(r if vivo else None, env)
        self._preencher(self.t_posts, s.get("posts", []), posts=True)
        self._preencher(self.t_fila, s.get("proximas", []), posts=False)

    def _lojas_canais(self, r, env):
        if r and r.get("lojas"):
            lojas = r["lojas"]
            canais = r.get("canais", {})
        else:
            env = env or ler_env()
            lojas = {"Mercado Livre": bool(env.get("ML_COOKIE")),
                     "Shopee": bool(env.get("SHOPEE_APP_ID") or env.get("SHOPEE_COOKIE")),
                     "AliExpress": bool(env.get("ALI_APP_KEY")), "Amazon": True, "KaBuM!": bool(env.get("AWIN_ID"))}
            canais = {"TikTok": "ligado" if env.get("TIKTOK") == "1" else "desligado",
                      "Instagram": "ligado" if env.get("INSTA") == "1" else "desligado",
                      "Facebook": "configurada" if env.get("FB_PAGE_ID") else "não configurada",
                      "WhatsApp": env.get("WHATS_HORARIOS") or "padrão"}
        chave = json.dumps([lojas, canais], sort_keys=True)
        if getattr(self, "_lojas_antes", None) == chave:
            return
        self._lojas_antes = chave
        for q in (self.q_lojas, self.q_canais):
            for w in q.winfo_children():
                w.destroy()
        for i, (nome, ok) in enumerate(lojas.items()):
            tk.Label(self.q_lojas, text=f"● {nome}", font=self.f_txt, bg=CARTAO,
                     fg=VERDE if ok else "#5A5A5A").grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 14), pady=2)
        for i, (nome, txt) in enumerate(canais.items()):
            desligado = any(p in txt for p in ("desligad", "não configurad", "falta"))
            tk.Label(self.q_canais, text=f"● {nome}", font=self.f_bold, bg=CARTAO,
                     fg=SUAVE if desligado else VERDE).grid(row=i, column=0, sticky="nw", pady=2)
            tk.Label(self.q_canais, text=txt, font=self.f_peq, bg=CARTAO, fg=SUAVE, wraplength=300,
                     justify="left").grid(row=i, column=1, sticky="w", padx=(8, 0), pady=2)

    def _preencher(self, tabela, linhas, posts):
        assinatura = (len(linhas), linhas[0][0] if linhas else None, linhas[-1][0] if linhas else None)
        if getattr(tabela, "_assinatura", None) == assinatura:
            return
        tabela._assinatura = assinatura
        tabela.delete(*tabela.get_children())
        for i, linha in enumerate(linhas):
            quando, dados = linha[0], linha[1]
            try:
                d = json.loads(dados) if dados else {}
            except ValueError:
                d = {}
            if posts:
                titulo = d.get("titulo") or ("post manual" if str(linha[2]).startswith("manual:") else linha[2])
            else:
                titulo = d.get("titulo") or (linha[2] or "").split("\n")[0][:90]
                if linha[3]:
                    titulo = "⭐ " + titulo  # post manual: vai na frente
            preco = d.get("preco")
            hora = datetime.fromtimestamp(quando or 0)
            quando_txt = hora.strftime("%H:%M") if hora.date() == datetime.now().date() else hora.strftime("%d/%m %H:%M")
            tabela.insert("", "end", tags=("par",) if i % 2 else (), values=(
                quando_txt, d.get("fonte") or "—", titulo,
                f"R$ {preco:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if preco else "—",
                f"{d['desconto']}% OFF" if d.get("desconto") else "—"))

    # ------------------------------------------------ fechar
    def fechar(self):
        if self.robo.ligado() or self.robo.preparando:
            resp = messagebox.askyesnocancel(
                "Fechar o painel",
                "O robô está ligado pelo painel. Se fechar, ele desliga junto.\n\n"
                "Sim = desligar o robô e fechar\nNão = só minimizar (o robô continua)\nCancelar = voltar")
            if resp is None:
                return
            if resp is False:
                self.raiz.iconify()
                return
            self.robo.desligar()
        self.raiz.destroy()


def main():
    _preparar_windows()
    trava = _so_uma_janela()
    raiz = tk.Tk()
    if trava is None:
        raiz.withdraw()
        messagebox.showinfo("Painel de Ofertas", "O painel já está aberto. Procure na barra de tarefas.")
        return
    Painel(raiz)
    raiz.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 — sem janela preta, o erro precisa aparecer em algum lugar
        import traceback
        erro = traceback.format_exc()
        (PASTA / "painel-erro.txt").write_text(erro, encoding="utf-8")
        try:
            messagebox.showerror("Painel de Ofertas", "O painel deu erro ao abrir. Os detalhes estão em painel-erro.txt "
                                                 "na pasta do robô.\n\n" + erro[-600:])
        except Exception:  # noqa: BLE001
            pass
