"""Озвучка текста с таймингами слов — для субтитров, которые подсвечивают слово в момент произнесения.

Движки:
  edge     — нейроголоса Microsoft (бесплатно, нужен интернет; на сервере работает)
  rhvoice  — офлайн-голос RHVoice, если установлен (pip install rhvoice-wrapper-bin rhvoice-wrapper)
  silent   — без звука, тайминги по длине слов (для черновой проверки картинки)
Позже сюда же добавим ElevenLabs.
"""
import asyncio
import re
import subprocess
import wave
from pathlib import Path

EDGE_VOICE = "ru-RU-SvetlanaNeural"


def _estimate(text, start=0.0, cps=14.0):
    """Тайминги слов по числу букв: ~14 символов в секунду, паузы на знаках препинания."""
    words, t = [], start
    for w in text.split():
        d = max(0.18, len(w) / cps)
        words.append((t, t + d, w))
        t += d + (0.25 if re.search(r"[.!?…]$", w) else 0.08 if w.endswith(",") else 0.04)
    return words, t


async def _edge(text, out, rate="-6%", pitch="-2Hz"):
    import edge_tts
    comm = edge_tts.Communicate(text, EDGE_VOICE, rate=rate, pitch=pitch, boundary="WordBoundary")
    words = []
    with open(out, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                s = chunk["offset"] / 1e7
                words.append((s, s + chunk["duration"] / 1e7, chunk["text"]))
    return _attach_punct(words, text)


def _attach_punct(words, text):
    """Edge отдаёт слова без знаков препинания — возвращаем их из исходного текста."""
    src = text.split()
    out, j = [], 0
    for s, e, w in words:
        while j < len(src) and re.sub(r"\W", "", src[j]).lower() != re.sub(r"\W", "", w).lower():
            j += 1
        if j < len(src):
            out.append((s, e, src[j]))
            j += 1
        else:
            out.append((s, e, w))
    return out


def _rhvoice(text, out):
    from rhvoice_wrapper import TTS
    tts = TTS(threads=1)
    data = tts.get(text, voice="anna", format_="wav")
    tts.join()
    Path(out).write_bytes(data)
    return None


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def to_wav(src, dst, rate=48000):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", str(rate), str(dst)], check=True)


def silent_wav(dst, seconds, rate=48000):
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * int(seconds * rate))


def synth(text, out_wav, engine="edge"):
    """Озвучивает text в out_wav (48 кГц моно). Возвращает (длительность, [(начало, конец, слово)])."""
    out_wav = Path(out_wav)
    if engine == "silent":
        words, end = _estimate(text)
        silent_wav(out_wav, end)
        return end, words
    raw = out_wav.with_suffix(".src")
    words = asyncio.run(_edge(text, raw)) if engine == "edge" else _rhvoice(text, raw)
    to_wav(raw, out_wav)
    raw.unlink(missing_ok=True)
    total = duration(out_wav)
    if not words:
        words, est = _estimate(text)
        k = total / est if est else 1
        words = [(s * k, e * k, w) for s, e, w in words]
    return total, words
