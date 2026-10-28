"""Batida de funk genérica (estilo tamborzão), gerada do zero: sem direitos autorais.

gerar_wav(caminho, duracao, inicio_batida, transicoes) cria um .wav estéreo:
- impactos graves em 0 s e 0,42 s (o gancho batendo na tela);
- tambores subindo antes da batida e um "whoosh" na virada;
- batida de funk a partir de inicio_batida;
- um whoosh + impacto em cada tempo de `transicoes` (troca de cena).
"""
import wave

import numpy as np

SR, BPM = 44100, 130


def gerar_wav(caminho, duracao, inicio_batida=2.4, transicoes=()):
    s16 = 60 / BPM / 4
    n = int(SR * duracao)
    mix = np.zeros(n)
    rng = np.random.default_rng(7)

    def put(sig, start, gain=1.0):
        i = int(start * SR)
        if i >= n or i < 0:
            return
        j = min(n, i + len(sig))
        mix[i:j] += sig[: j - i] * gain

    def x_(L):
        return np.arange(int(SR * L)) / SR

    def k808(L=0.55, f0=48):
        x = x_(L); f = f0 + 140 * np.exp(-x * 35)
        return np.tanh(np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-x * 5.5) * 2.2)

    def clap():
        x = x_(0.22); nz = rng.standard_normal(len(x))
        return nz * (np.exp(-x * 28) + 0.6 * np.exp(-np.maximum(x - 0.012, 0) * 40) * (x > 0.012)) * 0.45

    def tambor(f=190, L=0.16):
        x = x_(L); ff = f * (1 + 0.6 * np.exp(-x * 60))
        return np.sin(2 * np.pi * np.cumsum(ff) / SR) * np.exp(-x * 22) + rng.standard_normal(len(x)) * np.exp(-x * 80) * 0.15

    def hat():
        x = x_(0.04); nz = np.diff(np.concatenate([[0], rng.standard_normal(len(x))]))
        return nz * np.exp(-x * 110) * 0.18

    def impacto():
        x = x_(1.0); f = 38 + 220 * np.exp(-x * 20)
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-x * 4) + rng.standard_normal(len(x)) * np.exp(-x * 30) * 0.4

    def whoosh(L=0.5):
        x = x_(L); nz = rng.standard_normal(len(x)); out = np.zeros_like(nz); y = 0.0
        for i, v in enumerate(nz):
            a = 0.02 + 0.5 * i / len(nz); y += a * (v - y); out[i] = y
        return out * np.hanning(len(x)) ** 1.5 * 1.2

    put(impacto(), 0.0, 0.9)
    put(impacto(), 0.42, 0.9)
    for k, tt in enumerate(np.arange(max(0.9, inicio_batida - 1.4), inicio_batida - 0.4, s16 * 2)):
        put(tambor(160 + k * 12), tt, 0.35 + k * 0.05)
    put(whoosh(), inicio_batida - 0.45, 0.9)

    KICK, CLAP, TA, TG = {0, 3, 6, 10}, {4, 12}, {2, 7, 9, 14}, {5, 11, 13}
    passo, tt = 0, inicio_batida
    while tt < duracao - 0.05:
        p = passo % 16
        if p in KICK: put(k808(), tt, 0.85)
        if p in CLAP: put(clap(), tt, 0.8)
        if p in TA: put(tambor(230), tt, 0.45)
        if p in TG: put(tambor(140, 0.2), tt, 0.5)
        if p % 2 == 0: put(hat(), tt, 1.0)
        passo += 1; tt += s16
    for t in transicoes:
        put(whoosh(), t - 0.4, 0.7)
        put(impacto(), t, 0.5)

    fade = int(SR * min(0.6, duracao / 4))
    mix[-fade:] *= np.linspace(1, 0, fade)
    mix = np.tanh(mix * 1.3)
    mix = mix / (np.max(np.abs(mix)) or 1) * 0.89
    pcm = (mix * 32767).astype(np.int16)
    with wave.open(str(caminho), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(np.column_stack([pcm, pcm]).ravel().tobytes())
    return caminho
