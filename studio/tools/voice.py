"""Озвучка текста с таймингами слов — для субтитров, которые подсвечивают слово в момент произнесения.

Движки:
  edge     — нейроголоса Microsoft (бесплатно, нужен интернет; на сервере работает)
  rhvoice  — офлайн-голос RHVoice, если установлен (pip install rhvoice-wrapper-bin rhvoice-wrapper)
  silent   — без звука, тайминги по длине слов (для черновой проверки картинки)
  file     — готовая озвучка одним файлом (например, ElevenLabs): режется по самым длинным паузам на сцены
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


def _silences(path, noise="-38dB", min_len=0.3):
    """[(начало, конец)] пауз в файле по ffmpeg silencedetect."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", f"silencedetect=noise={noise}:d={min_len}",
                        "-f", "null", "-"], capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    return list(zip(starts, ends))


def split_by_pauses(src, n, out_dir, manual=None):
    """Режет одну дорожку на n кусков по n-1 самым длинным паузам. Возвращает пути к wav.
    manual — список секунд разреза из сценария ("voice_cuts"), если автоматика ошиблась."""
    out_dir = Path(out_dir)
    full = out_dir / "full.wav"
    to_wav(src, full)
    if manual:
        if len(manual) != n - 1:
            raise RuntimeError(f"voice_cuts: нужно {n - 1} разрезов, а указано {len(manual)}")
        bounds = [0.0] + [float(x) for x in manual] + [duration(full)]
    else:
        cuts = sorted(sorted(_silences(full), key=lambda p: p[1] - p[0], reverse=True)[:n - 1])
        if len(cuts) < n - 1:
            raise RuntimeError(f"в озвучке {len(cuts) + 1} фраз, а сцен с текстом {n}")
        bounds = [0.0] + [(a + b) / 2 for a, b in cuts] + [duration(full)]
    paths = []
    for i in range(n):
        p = out_dir / f"part{i}.wav"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(full), "-ss", f"{bounds[i]:.3f}",
                        "-to", f"{bounds[i + 1]:.3f}", str(p)], check=True)
        paths.append(p)
    return paths


def timed(text, wav):
    """Тайминги слов для готового куска: каждое предложение растягивается на свой отрезок речи между паузами."""
    total = duration(wav)
    sil = _silences(wav, min_len=0.2)
    edges = [0.0] + [x for p in sil for x in p] + [total]
    speech = [(a, b) for a, b in zip(edges[::2], edges[1::2]) if b - a > 0.15]
    sentences = [x.strip() for x in re.findall(r"[^.!?…]+[.!?…]*", text) if x.strip()]
    if len(sentences) != len(speech):
        speech, sentences = [(speech[0][0], speech[-1][1])] if speech else [(0, total)], [text]
    words = []
    for (a, b), sent in zip(speech, sentences):
        est, end = _estimate(sent)
        k = (b - a) / end if end else 1
        words += [(a + s * k, a + e * k, w) for s, e, w in est]
    return total, words
