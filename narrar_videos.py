"""Coloca narração (voz natural) nos 15 vídeos prontos do TikTok, mantendo a batida por baixo.

Uso: clique em narrar-videos-tiktok.bat e arraste a pasta 04-tiktok quando ele pedir.
Lê 04-tiktok/com-musica/*.mp4 e grava em 04-tiktok/narrados/. Os vídeos de conversa (05, 07, 08, 09 e 15)
não têm narração e ficam de fora. A voz é a do .env (VOZ=...).
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import voz

CTA = "Todo dia, de graça. Segue o canal no WhatsApp, link na bio!"


def seq(t0, passo, textos, ultimo_fim):
    """Falas em sequência: uma a cada `passo` segundos a partir de t0."""
    out = []
    for k, tx in enumerate(textos):
        ini = t0 + passo * k
        out.append((ini, ini + passo if k < len(textos) - 1 else ultimo_fim, tx))
    return out


# (início, fim do espaço, texto), nos tempos em que as cenas de cada vídeo foram montadas
ROTEIROS = {
    "tiktok-01-pagou-caro": [
        (0.05, 2.4, "Pagou caro no seu setup?"),
        (2.5, 6.6, "A gente acha o preço baixo pra você: mouse, teclado, placa e muito mais."), (6.7, 11.0, CTA)],
    "tiktok-02-preco-cheio": [
        (0.05, 2.4, "Para de pagar preço cheio!"),
        (2.5, 6.6, "Ofertas de PC filtradas todo dia: mouse, teclado, placa e mais."), (6.7, 11.0, CTA)],
    "tiktok-03-3-dicas": [(0.05, 2.4, "Três dicas pra não pagar caro em PC.")] + seq(2.45, 5.2, [
        "Um: desconfie do preço de. Muita loja infla o preço antigo pra parecer desconto grande.",
        "Dois: compare a mesma peça. O mesmo modelo muda bastante de preço de uma loja pra outra.",
        "Três: espere a hora certa. Em datas de promoção, o preço cai de verdade.",
        "Ou deixa que a gente faz isso por você. Link na bio!"], 22.2),
    "tiktok-04-setup-dos-sonhos": [
        (0.05, 4.4, "Eu montando o setup dos sonhos no carrinho."), (4.5, 6.7, "Total: quase catorze mil reais."),
        (6.8, 9.1, "Minha conta: trinta e sete e cinquenta."), (9.2, 11.7, "Por isso eu só compro em promoção.")],
    "tiktok-06-tipos-black-friday": [(0.05, 2.2, "Tipos de gamer na Black Friday.")] + seq(2.25, 3.4, [
        "Um: o que compra tudo no primeiro minuto, e depois vê mais barato.",
        "Dois: o que espera baixar mais, e o estoque acaba.",
        "Três: o que enche o carrinho, e não compra nada.",
        "E quatro: o que compara o preço em cinco lojas. Eu."], 16.8),
    "tiktok-11-tier-list-upgrades": [(0.05, 2.75, "Tier list dos upgrades que mais mudam o PC.")] + seq(2.8, 1.6, [
        "SSD: tier S.", "Placa de vídeo: S.", "Memória RAM: A.", "Monitor rápido: A.",
        "Headset bom: B.", "Water cooler: C.", "Gabinete novo: C.", "E RGB... tier D."], 15.9)
        + [(15.95, 18.4, "Concorda? Comenta a sua.")],
    "tiktok-12-red-flags-setup": [(0.05, 2.65, "Red flags no setup de alguém.")] + seq(2.7, 2.4, [
        "Fonte genérica de quinhentos watts real.", "Cabo pra todo lado.", "PC no chão do quarto.",
        "Monitor de sessenta hertz com placa top.", "E pagou preço cheio na Black Friday."], 15.0)
        + [(15.05, 17.5, "Qual dessas você tem?")],
    "tiktok-13-todo-gamer-ja-fez": [(0.05, 2.65, "Coisas que todo gamer já fez.")] + seq(2.7, 2.4, [
        "Ficou até três da manhã no só mais uma.", "Comprou jogo e nunca jogou.",
        "Olhou preço de placa sem ter dinheiro.", "Colocou RGB e achou que rodou melhor.",
        "Esperou uma promoção que nunca chegou."], 15.0) + [(15.05, 17.5, "Quantas você já fez? Comenta!")],
    "tiktok-14-pov-preco-caiu": seq(0.05, 2.8, [
        "POV: finalmente comprou a placa de vídeo.", "Chegou, instalou, tá tudo rodando liso.",
        "Aí você abre o site no dia seguinte...", "Baixou quatrocentos reais."], 10.8),
}

_perg = ["RGB aumenta o FPS?", "SSD aumenta o FPS no jogo?", "Memória em dual channel ajuda no desempenho?",
         "Fonte genérica tá de boa?", "E monitor rápido, faz diferença?"]
_resp = ["Mito. Infelizmente.", "Mito. Ele acelera o carregamento, não o FPS.",
         "Verdade. Duas memórias iguais rendem mais que uma.", "Mito! Fonte ruim pode levar outras peças junto.",
         "Verdade. Se o PC entregar o FPS, a diferença é enorme."]
ROTEIROS["tiktok-10-verdade-ou-mito"] = [(0.05, 2.2, "Verdade ou mito sobre PC gamer.")] + [
    f for k in range(5) for f in ((2.25 + 5.6 * k, 4.6 + 5.6 * k, _perg[k]), (4.65 + 5.6 * k, 7.8 + 5.6 * k, _resp[k]))
] + [(30.25, 32.8, "Quantas você acertou? Comenta aí!")]


def duracao(video):
    r = subprocess.run([voz._ffmpeg(), "-i", str(video)], capture_output=True, text=True).stderr
    h, m, s = r.split("Duration: ")[1].split(",")[0].split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def narrar(video, falas, saida):
    dur = duracao(video)
    pista = [(ini, voz.fala_que_cabe(tx, max(0.6, fim - ini - 0.1))) for ini, fim, tx in falas]
    with tempfile.TemporaryDirectory() as tmp:
        fala = Path(tmp) / "voz.wav"
        voz.salvar_wav(fala, voz.montar_trilha_de_voz(pista, dur))
        subprocess.run([voz._ffmpeg(), "-loglevel", "error", "-y", "-i", str(video), "-i", str(fala),
                        "-filter_complex", voz.filtro_mix(),
                        "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                        "-movflags", "+faststart", "-shortest", str(saida)], check=True)


def main():
    pasta = Path((sys.argv[1] if len(sys.argv) > 1 else input("Arraste aqui a pasta 04-tiktok e aperte Enter: "))
                 .strip().strip('"'))
    origem = pasta / "com-musica"
    if not origem.is_dir():
        raise SystemExit(f"Não achei a pasta {origem}. Arraste a pasta 04-tiktok (a que tem com-musica dentro).")
    destino = pasta / "narrados"
    destino.mkdir(exist_ok=True)
    so = [a for a in sys.argv[2:]]
    for nome, falas in ROTEIROS.items():
        if so and not any(s in nome for s in so):
            continue
        video = origem / f"{nome}.mp4"
        if not video.exists():
            print("não achei:", video.name)
            continue
        saida = destino / f"{nome}-narrado.mp4"
        narrar(video, falas, saida)
        print("pronto:", saida.name, flush=True)
    print(f"\nTudo em {destino}")


if __name__ == "__main__":
    main()
