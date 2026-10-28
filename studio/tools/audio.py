"""Звук для роликов: свой эмбиент-фон и эффекты (без чужой музыки — никаких претензий за авторские права)."""
import wave

import numpy as np

RATE = 48000


def read_wav(path):
    with wave.open(str(path)) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    return data


def write_wav(path, data):
    data = np.clip(data, -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((data * 32767).astype(np.int16).tobytes())


def pad(seconds, seed=3):
    """Мистический эмбиент: ля-минорный аккорд с медленным дыханием и мягким шумом-«ветром»."""
    t = np.arange(int(seconds * RATE)) / RATE
    out = np.zeros_like(t)
    for f, a in [(55, 0.5), (110, 0.35), (164.81, 0.22), (220, 0.18), (261.63, 0.12), (329.63, 0.08)]:
        lfo = 0.6 + 0.4 * np.sin(2 * np.pi * (0.05 + f / 4000) * t + f)
        out += a * lfo * np.sin(2 * np.pi * f * t + 0.3 * np.sin(2 * np.pi * 0.2 * t))
    rng = np.random.default_rng(seed)
    noise = np.convolve(rng.standard_normal(len(t)), np.ones(400) / 400, mode="same")
    out += 0.6 * noise * (0.5 + 0.5 * np.sin(2 * np.pi * 0.07 * t))
    fade = np.minimum(1, np.minimum(t / 1.5, (seconds - t) / 1.5))
    return out / np.abs(out).max() * fade


def dark_pad(seconds, seed=7):
    """Мрачный эмбиент: низкий гул ре-минор с диссонансом, медленные волны и редкий далёкий колокол."""
    t = np.arange(int(seconds * RATE)) / RATE
    out = np.zeros_like(t)
    for f, a in [(36.71, 0.6), (73.42, 0.45), (77.78, 0.12), (110.0, 0.18), (146.83, 0.1), (174.61, 0.07)]:
        lfo = 0.55 + 0.45 * np.sin(2 * np.pi * (0.03 + f / 9000) * t + f)
        out += a * lfo * np.sin(2 * np.pi * f * t + 0.5 * np.sin(2 * np.pi * 0.11 * t))
    rng = np.random.default_rng(seed)
    noise = np.convolve(rng.standard_normal(len(t)), np.ones(900) / 900, mode="same")
    out += 0.9 * noise * (0.5 + 0.5 * np.sin(2 * np.pi * 0.05 * t + 1))
    # Далёкий колокол раз в ~7 секунд: негармонические обертоны, долгий спад
    for start in np.arange(1.0, seconds - 1, 7.0):
        i = int(start * RATE)
        tt = t[: len(t) - i]
        bell = sum(a * np.sin(2 * np.pi * f * tt) for f, a in [(98, 0.5), (196 * 1.19, 0.25), (98 * 2.76, 0.15), (98 * 5.4, 0.06)])
        out[i:] += 0.35 * bell * np.exp(-tt * 0.6)
    fade = np.minimum(1, np.minimum(t / 2.5, (seconds - t) / 2.0))
    return out / np.abs(out).max() * fade


def card_sound(seed=4):
    """Карта ложится на стол: короткое шуршание скольжения и мягкий щелчок бумаги."""
    rng = np.random.default_rng(seed)
    slide_n, snap_n = int(0.18 * RATE), int(0.07 * RATE)
    slide = np.convolve(rng.standard_normal(slide_n), np.ones(6) / 6, mode="same")
    slide = (slide - np.convolve(slide, np.ones(60) / 60, mode="same")) * np.linspace(0.2, 1, slide_n) ** 2 * 0.5
    snap = rng.standard_normal(snap_n)
    snap = (snap - np.convolve(snap, np.ones(25) / 25, mode="same")) * np.exp(-np.arange(snap_n) / (0.008 * RATE))
    thump_t = np.arange(snap_n) / RATE
    snap += 0.6 * np.sin(2 * np.pi * 140 * thump_t) * np.exp(-thump_t * 60)
    out = np.concatenate([slide, snap, np.zeros(int(0.05 * RATE))])
    return out / np.abs(out).max()


def whoosh(length=0.45, seed=1):
    rng = np.random.default_rng(seed)
    n = int(length * RATE)
    noise = rng.standard_normal(n)
    env = np.sin(np.linspace(0, np.pi, n)) ** 2
    # «Свист» — шум, сглаженный всё меньшим окном: звук светлеет к концу
    out = np.zeros(n)
    for k, win in enumerate([200, 80, 30, 12]):
        part = slice(k * n // 4, (k + 1) * n // 4 + 1)
        out[part] = np.convolve(noise, np.ones(win) / win, mode="same")[part]
    return out / np.abs(out).max() * env


def chime(length=1.6):
    t = np.arange(int(length * RATE)) / RATE
    out = sum(a * np.sin(2 * np.pi * f * t) * np.exp(-t * d) for f, a, d in
              [(1318.5, 0.5, 3.0), (1975.5, 0.3, 4.0), (2637, 0.2, 5.5), (659.25, 0.25, 2.2)])
    return out / np.abs(out).max()


def mix(total, voice_parts, fx_events, music_db=-30, voice_db=-1):
    """voice_parts: [(начало, массив)], fx_events: [(начало, 'whoosh'|'chime'|'card')]. Возвращает итоговую дорожку."""
    n = int(total * RATE) + RATE
    voice = np.zeros(n)
    for start, data in voice_parts:
        i = int(start * RATE)
        voice[i:i + len(data)] += data[:n - i]
    fx = np.zeros(n)
    sounds = {"whoosh": whoosh() * 0.18, "chime": chime() * 0.22, "card": card_sound() * 0.55}
    for start, name in fx_events:
        i = int(start * RATE)
        s = sounds[name]
        fx[i:i + len(s)] += s[:n - i]
    music = dark_pad(n / RATE)[:n]
    music = np.pad(music, (0, n - len(music)))  # защита от округления длины
    # Музыка приглушается, пока звучит голос
    env = np.convolve((np.abs(voice) > 0.02).astype(float), np.ones(RATE // 4) / (RATE // 4), mode="same")
    duck = 1 - 0.45 * np.clip(env * 3, 0, 1)
    peak = np.abs(voice).max() or 1
    out = voice / peak * 10 ** (voice_db / 20) + music * duck * 10 ** (music_db / 20) + fx
    return out[:int(total * RATE)]
