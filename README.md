# Affiliate Deals Bot

**🇧🇷 [Português](#-português) · 🇺🇸 [English](#-english)**

Robô em Python que encontra promoções nas lojas, troca os links pelos links de afiliado, cria artes e vídeos sozinho e publica tudo no Telegram, Facebook, Instagram, WhatsApp e TikTok. Em uso real num canal de ofertas ativo.

Python bot that finds deals across Brazilian stores, rewrites links with affiliate tags, auto-generates images and short videos, and publishes to Telegram, Facebook, Instagram, WhatsApp and TikTok. Running in production on a live deals channel.

<p align="center">
  <img src="docs/img/exemplo-story.png" alt="Story gerado pelo robô" width="260">
  &nbsp;
  <img src="docs/img/exemplo-feed.png" alt="Post de feed gerado pelo robô" width="300">
</p>
<p align="center"><sub>Artes geradas automaticamente pelo robô / Auto-generated creatives</sub></p>

---

## 🇧🇷 Português

### O que ele faz

- **Busca automática de ofertas** na Shopee (Affiliate Open API), Mercado Livre (página de ofertas), AliExpress e KaBuM!, com filtro por desconto mínimo/máximo, preço e categoria.
- **Links de afiliado automáticos** para Amazon, Mercado Livre (`meli.la`), Shopee, AliExpress e KaBuM! (Awin), com sub-ids por canal para medir de onde vem cada venda.
- **Fila inteligente**: não repete produto, alterna lojas, descarta ofertas velhas e prioriza "ofertas fortes" (queda grande de preço vira alerta e fura a fila).
- **Publicação multicanal**: canal do Telegram, Página do Facebook (Graph API), Instagram (feed, stories e reels), kit pronto para o canal do WhatsApp e rascunhos no TikTok (Content Posting API).
- **Artes geradas na hora** com Pillow: story 9:16 e feed 4:5 com selo de preço e desconto.
- **Vídeos curtos automáticos** (1080x1920) com ffmpeg: "achados do dia" e 6 formatos casuais que giram (adivinha o preço, conversa, POV, duelo, comparação, "não compre"), com **narração neural** (Edge TTS / ElevenLabs) e batida própria gerada por código, sem direitos autorais.
- **Controle pelo Telegram**: `/status`, `/buscar`, `/postar`, `/pausar`, `/retomar`, `/video`, `/tiktok`, `/casual`, `/whats`, `/face`, `/insta`, `/relatorio` (semanal), `/log` e mais.
- **Painel de desktop** (Tkinter) para ligar/desligar, pausar, ver fila e logs, editar ajustes e exportar log, com reinício automático se o robô cair.
- **Agenda e limites**: horário de funcionamento, intervalos, posts por dia em cada rede, fuso horário.

### Tecnologias

Python 3.10+ · python-telegram-bot (JobQueue) · httpx · curl_cffi · BeautifulSoup · SQLite · Pillow · NumPy · ffmpeg (imageio-ffmpeg) · edge-tts · Tkinter · Meta Graph API · TikTok Content Posting API · Shopee Affiliate GraphQL API · AliExpress Open API (HMAC-SHA256)

### Como está organizado

```
bot.py              Robô principal: comandos do Telegram, agenda e fila de posts
config.py           Lê e valida o .env
banco.py            SQLite: histórico, fila, preços já vistos, métricas
links.py            Converte links comuns em links de afiliado
formatar.py         Texto das mensagens (HTML do Telegram)
criativo.py         Artes de story e feed
video_achados.py    Vídeo "achados do dia"
video_casual.py     Vídeos em formatos casuais para TikTok/Reels
voz.py              Narração (texto para voz) e mixagem com a trilha
trilha.py           Batida gerada por código
painel.pyw          Painel de desktop
painel_ponte.py     Comunicação entre painel e robô
fontes/             Integrações: shopee, mercadolivre, aliexpress, kabum, facebook, instagram, tiktok
```

### Como rodar

```bash
pip install -r requirements.txt
cp .env.example .env      # preencha pelo menos TELEGRAM_TOKEN, CANAL, ADMIN_ID e MARCA
python bot.py
```

No Windows dá para usar `ligar-robo.bat` (reinicia sozinho se cair) ou o painel (`criar-atalho-painel.bat` cria o atalho na área de trabalho). Cada recurso liga quando sua chave é preenchida no `.env`; campo vazio deixa o recurso desligado.

---

## 🇺🇸 English

### Features

- **Automatic deal discovery** from Shopee (Affiliate Open API), Mercado Livre (deals page), AliExpress and KaBuM!, filtered by min/max discount, price and category.
- **Automatic affiliate links** for Amazon, Mercado Livre (`meli.la`), Shopee, AliExpress and KaBuM! (Awin), with per-channel sub-ids to track which channel drives each sale.
- **Smart queue**: no repeated products, store rotation, stale-offer expiry and priority for "hot deals" (big price drops trigger an alert and jump the queue).
- **Multichannel publishing**: Telegram channel, Facebook Page (Graph API), Instagram (feed, stories and reels), ready-to-paste WhatsApp channel kit, and TikTok drafts (Content Posting API).
- **On-the-fly creatives** with Pillow: 9:16 story and 4:5 feed images with price and discount badges.
- **Automatic short videos** (1080x1920) with ffmpeg: "today's finds" plus 6 rotating casual formats (guess the price, chat, POV, duel, comparison, "don't buy"), with **neural voice-over** (Edge TTS / ElevenLabs) and a royalty-free beat generated in code.
- **Telegram admin commands**: `/status`, `/buscar`, `/postar`, `/pausar`, `/retomar`, `/video`, `/tiktok`, `/casual`, `/whats`, `/face`, `/insta`, weekly `/relatorio`, `/log` and more.
- **Desktop control panel** (Tkinter): start/stop, pause, queue and log viewer, settings editor, log export, and auto-restart on crash.
- **Scheduling and limits**: operating hours, intervals, daily post caps per network, time zone.

### Tech stack

Python 3.10+ · python-telegram-bot (JobQueue) · httpx · curl_cffi · BeautifulSoup · SQLite · Pillow · NumPy · ffmpeg (imageio-ffmpeg) · edge-tts · Tkinter · Meta Graph API · TikTok Content Posting API · Shopee Affiliate GraphQL API · AliExpress Open API (HMAC-SHA256)

### Getting started

```bash
pip install -r requirements.txt
cp .env.example .env      # fill in at least TELEGRAM_TOKEN, CANAL, ADMIN_ID and MARCA (brand name shown on images/videos)
python bot.py
```

Every integration is optional and turns on once its key is set in `.env`. The code, comments and messages are in Brazilian Portuguese, since the bot targets the Brazilian market.

---

Feito por / Built by **[@zero0-byte](https://github.com/zero0-byte)**
