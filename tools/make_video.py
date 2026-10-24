"""Ролик в фирменном стиле v2: озвучка, субтитры по словам, частицы, вспышки, звук.

python -m tools.make_video reels/01_pick3_he_thinks.json out/01.mp4 [--engine edge|rhvoice|silent] [--send] [--handle @mira.taro]

Каждая сцена в JSON может иметь поле "say" — текст, который озвучивается во время сцены.
Длительность сцены подстраивается под голос, так что картинка и речь всегда совпадают.
"""
import argparse
import json
import math
import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools import audio, voice  # noqa: E402
from tools.make_reel import card_img, ease, flip, make_background, shadowed  # noqa: E402

W, H, FPS = 1080, 1920, 30
GOLD = (217, 178, 111)
GOLD_HI = (245, 200, 110)
WHITE = (255, 250, 242)
FONTS = ROOT / "assets" / "fonts"
MIN_DUR = {"hook": 2.2, "pick3": 3.0, "reveal": 3.0, "list": 4.0, "cta": 3.2}
SUB_Y = 1450  # центр строки субтитров: выше интерфейса Reels, ниже карт
_fonts = {}


def font(name, size):
    key = (name, size)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(str(FONTS / f"{name}.ttf"), size)
    return _fonts[key]


# ---------- Эффекты ----------

def glow(size, color=(255, 210, 140)):
    g = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(g).ellipse([size * 0.2, size * 0.2, size * 0.8, size * 0.8], fill=color + (150,))
    return g.filter(ImageFilter.GaussianBlur(size // 7))


GLOW = None


class Dust:
    """Золотая пыль: частицы медленно поднимаются и мерцают."""

    def __init__(self, n=70, seed=11):
        rnd = random.Random(seed)
        self.p = [(rnd.uniform(0, W), rnd.uniform(0, H), rnd.uniform(14, 40), rnd.uniform(1.2, 3.6), rnd.uniform(0, 6.28))
                  for _ in range(n)]

    def draw(self, layer, t):
        d = ImageDraw.Draw(layer)
        for x, y, speed, r, ph in self.p:
            yy = (y - speed * t) % H
            xx = x + 12 * math.sin(t * 0.7 + ph)
            a = int(120 + 120 * math.sin(t * 2.3 + ph))
            d.ellipse([xx - r, yy - r, xx + r, yy + r], fill=GOLD_HI + (max(0, a),))


def scale(im, k):
    return im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS)


# ---------- Субтитры ----------

def chunks(words, max_words=3, max_chars=20):
    """Группирует слова в короткие фразы субтитров."""
    out, cur = [], []
    for w in words:
        text = " ".join(x[2] for x in cur + [w])
        if cur and (len(cur) >= max_words or len(text) > max_chars or re.search(r"[.!?…,]$", cur[-1][2])):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def draw_subs(d, groups, t):
    for k, g in enumerate(groups):
        start = g[0][0]
        end = groups[k + 1][0][0] if k + 1 < len(groups) else g[-1][1] + 0.6
        if not start <= t < end:
            continue
        f = font("Montserrat-800", 74)
        space = d.textlength(" ", font=f)
        widths = [d.textlength(w[2], font=f) for w in g]
        total = sum(widths) + space * (len(g) - 1)
        pop = 1 - 0.12 * max(0, 1 - (t - start) / 0.12)  # лёгкий «прыжок» при появлении фразы
        x = (W - total) / 2
        for (s, e, word), wd in zip(g, widths):
            color = GOLD_HI if s <= t < max(e, s + 0.25) else WHITE
            y = SUB_Y - 40 + (1 - pop) * 30
            d.text((x, y), word, font=f, fill=color, stroke_width=7, stroke_fill=(12, 8, 24))
            x += wd + space
        return


# ---------- Сцены ----------

def title(d, text, y=200, size=92):
    """Крупный заголовок: жирный шрифт с обводкой, перенос по ширине кадра."""
    size = int(size * 0.8)
    f = font("Montserrat-800", size)
    lines = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            test = f"{cur} {word}".strip()
            if cur and d.textlength(test, font=f) > W - 140:
                lines.append(cur)
                cur = word
            else:
                cur = test
        lines.append(cur)
    for i, line in enumerate(lines):
        d.text(((W - d.textlength(line, font=f)) / 2, y + i * size * 1.18), line, font=f, fill=GOLD_HI,
               stroke_width=6, stroke_fill=(12, 8, 24))
    return y + len(lines) * size * 1.18


def scene_hook(s, words):
    def draw(frame, d, t):
        title(d, s["text"], 210, 100)
        cw = 290
        k = 1 + 0.35 * (1 - ease(t / 0.35))
        for i in range(3):
            im = scale(card_img("back", cw), k)
            x = W / 2 + (i - 1) * (cw + 40) - im.width / 2
            y = 760 + 14 * math.sin(t * 2 + i) - (im.height - cw * 5 / 3) / 2
            shadowed(frame, im, x, y)
    return draw


def scene_pick3(s, words):
    marks = []  # (время, номер карты), когда диктор называет «первая/вторая/третья»
    for st, _, w in words:
        for i, stem in enumerate(("перв", "втор", "трет")):
            if w.lower().startswith(stem):
                marks.append((st, i))

    def draw(frame, d, t):
        title(d, s["text"], 200, 84)
        cw = 300
        for i in range(3):
            lit = any(st <= t < st + 0.7 and n == i for st, n in marks)
            x = 60 + i * (cw + 30)
            y = 700 + 12 * math.sin(t * 2 + i) - (25 if lit else 0)
            if lit:
                frame.alpha_composite(GLOW, (int(x + cw / 2 - GLOW.width / 2), int(y + cw * 5 / 6 - GLOW.height / 2)))
            shadowed(frame, card_img("back", cw), x, y)
            f = font("Cormorant-700", 120)
            num = str(i + 1)
            d.text((x + cw / 2 - d.textlength(num, font=f) / 2, y + cw * 5 / 3 + 20), num, font=f,
                   fill=GOLD_HI if lit else GOLD, stroke_width=3, stroke_fill=(12, 8, 24))
    return draw


def scene_reveal(s, words):
    def draw(frame, d, t):
        if s.get("title"):
            title(d, s["title"], 150, 78)
        cw = 560
        p = ease((t - 0.1) / 0.6)
        if p >= 0.5:
            pulse = 0.9 + 0.1 * math.sin(t * 3)
            g = scale(GLOW, 1.5 * pulse)
            frame.alpha_composite(g, (int(W / 2 - g.width / 2), int(300 + cw * 5 / 6 - g.height / 2)))
        im = flip(s["card"], cw, p)
        shadowed(frame, im, (W - im.width) / 2, 300 + 8 * math.sin(t * 1.5))
        # Вспышка в момент, когда карта открылась
        flash = max(0.0, 1 - abs(t - 0.4) / 0.18)
        if flash:
            frame.alpha_composite(Image.new("RGBA", (W, H), (255, 240, 210, int(110 * flash))))
    return draw


def scene_list(s, words):
    n = len(s["items"])

    def draw(frame, d, t):
        title(d, s["text"], 190, 80)
        per = max(0.6, (s["duration"] - 0.8) / n)
        for i, item in enumerate(s["items"]):
            order = n - 1 - i if s.get("countdown") else i
            a = ease((t - 0.4 - order * per) / 0.35)
            if a <= 0:
                continue
            y = 520 + i * (760 / n)
            d.text((120, y), f"{i + 1}.", font=font("Cormorant-700", 70), fill=GOLD)
            d.text((220, y + 6), item, font=font("Montserrat-700", 54), fill=tuple(int(c * a) for c in WHITE))
    return draw


def scene_cta(s, words):
    def draw(frame, d, t):
        title(d, s.get("text", "Напиши в комментариях"), 230, 84)
        frame.alpha_composite(GLOW, (int(W / 2 - GLOW.width / 2), int(780 - GLOW.height / 2)))
        shadowed(frame, card_img("back", 340), (W - 340) / 2, 500 + 10 * math.sin(t * 2))
        word = s.get("word", "КАРТА")
        pulse = 1 + 0.07 * math.sin(t * 6)
        f = font("Montserrat-800", int(150 * pulse))
        d.text(((W - d.textlength(word, font=f)) / 2, 1110), word, font=f, fill=GOLD_HI, stroke_width=8, stroke_fill=(12, 8, 24))
    return draw


SCENES = {"hook": scene_hook, "pick3": scene_pick3, "reveal": scene_reveal, "list": scene_list, "cta": scene_cta}


# ---------- Сборка ----------

def build(script, out, engine="edge", handle="@mira.taro"):
    global GLOW
    GLOW = glow(700)
    tmp = Path(tempfile.mkdtemp())
    timeline, words_all, voice_parts, fx, t0 = [], [], [], [], 0.0
    for i, s in enumerate(script["scenes"]):
        say = s.get("say", "")
        if say:
            wav = tmp / f"s{i}.wav"
            length, words = voice.synth(say, wav, engine)
            voice_parts.append((t0 + 0.15, audio.read_wav(wav)))
        else:
            length, words = 0, []
        dur = max(MIN_DUR.get(s["type"], 2.5), length + 0.45)
        s = {**s, "duration": dur}
        local = [(a + 0.15, b + 0.15, w) for a, b, w in words]
        timeline.append((t0, dur, SCENES[s["type"]](s, local)))
        words_all += [(t0 + a, t0 + b, w) for a, b, w in local]
        if s["type"] == "reveal":
            fx += [(t0 + 0.05, "whoosh"), (t0 + 0.4, "chime")]
        elif s["type"] in ("hook", "cta"):
            fx.append((t0, "whoosh"))
        t0 += dur
    total = t0
    groups = chunks(words_all)

    track = tmp / "mix.wav"
    audio.write_wav(track, audio.mix(total, voice_parts, fx))

    bg = make_background()
    dust = Dust()
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", "-i", str(track), "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "192k",
           "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
           "-shortest", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for n in range(int(total * FPS)):
        t = n / FPS
        z = 1.0 + 0.1 * (t / total)
        cw, ch = int(W * 1.15 / z), int(H * 1.15 / z)
        ox, oy = (bg.width - cw) // 2, (bg.height - ch) // 2
        frame = bg.crop((ox, oy, ox + cw, oy + ch)).resize((W, H), Image.BILINEAR).convert("RGBA")
        dust.draw(frame, t)
        d = ImageDraw.Draw(frame)
        for start, dur, draw in timeline:
            if t < start + dur:
                draw(frame, d, t - start)
                break
        d = ImageDraw.Draw(frame)
        draw_subs(d, groups, t)
        # Полоса прогресса сверху и подпись канала
        d.rectangle([0, 0, int(W * t / total), 8], fill=GOLD_HI)
        f = font("Montserrat-700", 34)
        d.text((W - 50 - d.textlength(handle, font=f), 60), handle, font=f, fill=GOLD + (200,))
        proc.stdin.write(frame.convert("RGB").tobytes())
    proc.stdin.close()
    proc.wait()
    return total


async def send_telegram(path, caption):
    import aiohttp
    from bot import config
    form = aiohttp.FormData()
    form.add_field("chat_id", str(config.ADMIN_ID))
    form.add_field("caption", caption)
    form.add_field("video", Path(path).read_bytes(), filename=Path(path).name, content_type="video/mp4")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as s:
        async with s.post(f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendVideo", data=form) as r:
            print("Telegram:", r.status)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("out")
    ap.add_argument("--engine", default="edge")
    ap.add_argument("--handle", default="@mira.taro")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    secs = build(json.loads(Path(a.script).read_text()), a.out, a.engine, a.handle)
    print(f"Готово: {a.out}, {secs:.1f} сек")
    if a.send:
        import asyncio
        asyncio.run(send_telegram(a.out, f"Пробный ролик: {Path(a.script).stem}"))
