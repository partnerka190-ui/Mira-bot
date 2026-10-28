"""Рендер вертикального ролика 1080×1920 в фирменном стиле «Мира».

python -m tools.make_reel reels/pick3_love.json out.mp4 [voice.mp3]
Сценарий — JSON со списком сцен, см. reels/*.json.
"""
import json
import math
import random
import subprocess
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bot.cards import BY_ID  # noqa: E402

W, H, FPS = 1080, 1920, 30
GOLD = (217, 178, 111)
CREAM = (246, 236, 220)
CARDS = ROOT / "assets" / "cards"
FONT = ROOT / "assets" / "fonts"
_fonts = {}


def font(size, weight=600):
    key = (size, weight)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(str(FONT / f"Cormorant-{weight}.ttf"), size)
    return _fonts[key]


def ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def make_background():
    """Звёздное небо в 1.15 раза больше кадра — под медленный наезд камеры."""
    bw, bh = int(W * 1.15), int(H * 1.15)
    img = Image.new("RGB", (bw, bh))
    d = ImageDraw.Draw(img)
    top, bottom = (14, 12, 34), (50, 26, 72)
    for y in range(bh):
        t = y / bh
        d.line([(0, y), (bw, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    glow = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(glow).ellipse([bw * 0.05, bh * 0.25, bw * 0.95, bh * 0.7], fill=90)
    glow = glow.filter(ImageFilter.GaussianBlur(220))
    img = Image.composite(Image.new("RGB", (bw, bh), (120, 80, 190)), img, glow)
    d = ImageDraw.Draw(img)
    rnd = random.Random(7)
    for _ in range(420):
        x, y, r = rnd.uniform(0, bw), rnd.uniform(0, bh), rnd.choice([1, 1, 1, 2, 2, 3])
        d.ellipse([x - r, y - r, x + r, y + r], fill=rnd.choice([(255, 244, 225), (200, 190, 230), (230, 210, 170)]))
    return img


BG = None
_card_cache = {}


def card_img(card_id, width):
    key = (card_id, width)
    if key not in _card_cache:
        im = Image.open(CARDS / f"{card_id}.png").convert("RGBA")
        _card_cache[key] = im.resize((width, int(width * im.height / im.width)), Image.LANCZOS)
    return _card_cache[key]


def shadowed(frame, im, x, y):
    sh = Image.new("RGBA", (im.width + 80, im.height + 80), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([40, 50, im.width + 40, im.height + 50], radius=30, fill=(0, 0, 0, 140))
    sh = sh.filter(ImageFilter.GaussianBlur(24))
    frame.paste(sh, (int(x - 40), int(y - 40)), sh)
    frame.paste(im, (int(x), int(y)), im)


def text_block(d, text, y, size, color=CREAM, width=22, weight=600, alpha=1.0, line_gap=1.12):
    lines = []
    for para in text.split("\n"):
        lines += textwrap.wrap(para, width) or [""]
    f = font(size, weight)
    color = tuple(int(c * alpha + 20 * (1 - alpha)) for c in color)
    for i, line in enumerate(lines):
        w = d.textlength(line, font=f)
        yy = y + i * size * line_gap
        d.text(((W - w) / 2 + 3, yy + 4), line, font=f, fill=(10, 6, 20))
        d.text(((W - w) / 2, yy), line, font=f, fill=color)
    return y + len(lines) * size * line_gap


def flip(card_id, width, p):
    """Переворот карты: рубашка сжимается, затем раскрывается лицо."""
    face = card_img("back" if p < 0.5 else card_id, width)
    k = abs(math.cos(p * math.pi))
    w = max(2, int(face.width * k))
    return face.resize((w, face.height))


# ---------- Сцены: каждая возвращает функцию кадра f(frame, d, t_local) ----------

def scene_hook(s):
    def draw(frame, d, t):
        a = ease(t / 0.6)
        text_block(d, s["text"], 700, 96, color=GOLD, width=21, alpha=a)
        if s.get("sub"):
            text_block(d, s["sub"], 1180, 58, width=26, alpha=ease((t - 0.5) / 0.6))
    return draw


def scene_pick3(s):
    def draw(frame, d, t):
        text_block(d, s["text"], 260, 74, color=GOLD, width=28)
        cw = 300
        for i in range(3):
            p = ease((t - 0.3 * i) / 0.5)
            if p <= 0:
                continue
            x = 60 + i * (cw + 30)
            y = 760 + (1 - p) * 300 + 12 * math.sin(t * 2 + i)
            shadowed(frame, card_img("back", cw), x, y)
            label = str(i + 1)
            f = font(110, 700)
            d.text((x + cw / 2 - d.textlength(label, font=f) / 2, y + cw * 1.67 + 40), label, font=f, fill=GOLD)
        if s.get("sub"):
            text_block(d, s["sub"], 1560, 56, width=28, alpha=ease((t - 1.2) / 0.5))
    return draw


def scene_reveal(s):
    def draw(frame, d, t):
        if s.get("title"):
            text_block(d, s["title"], 170, 70, color=GOLD, width=22)
        cw = 520
        im = flip(s["card"], cw, ease(t / 0.9))
        y = 330 + 10 * math.sin(t * 1.5)
        shadowed(frame, im, (W - im.width) / 2, y)
        if t > 0.9:
            a = ease((t - 0.9) / 0.5)
            text_block(d, BY_ID[s["card"]]["name"].upper(), 1260, 72, color=GOLD, alpha=a, width=22)
            if s.get("text"):
                text_block(d, s["text"], 1380, 54, width=30, alpha=ease((t - 1.2) / 0.5))
    return draw


def scene_list(s):
    def draw(frame, d, t):
        text_block(d, s["text"], 200, 76, color=GOLD, width=20)
        n = len(s["items"])
        per = max(0.6, (s["duration"] - 1) / n)
        for i, item in enumerate(s["items"]):
            order = n - 1 - i if s.get("countdown") else i
            a = ease((t - 0.6 - order * per) / 0.4)
            if a <= 0:
                continue
            y = 520 + i * (1150 / n)
            f = font(64, 700)
            num = f"{i + 1}."
            d.text((130, y), num, font=f, fill=GOLD)
            text_block_left(d, item, 240, y, 60, alpha=a)
    return draw


def text_block_left(d, text, x, y, size, alpha=1.0):
    color = tuple(int(c * alpha + 20 * (1 - alpha)) for c in CREAM)
    for i, line in enumerate(textwrap.wrap(text, 24)):
        d.text((x + 3, y + 4 + i * size * 1.1), line, font=font(size), fill=(10, 6, 20))
        d.text((x, y + i * size * 1.1), line, font=font(size), fill=color)


def scene_cta(s):
    def draw(frame, d, t):
        shadowed(frame, card_img("back", 380), (W - 380) / 2, 380 + 10 * math.sin(t * 2))
        a = ease(t / 0.5)
        y = text_block(d, s.get("text", "Напиши"), 1120, 70, width=26, alpha=a)
        word = s.get("word", "КАРТА")
        pulse = 1 + 0.06 * math.sin(t * 6)
        f = font(int(150 * pulse), 700)
        d.text(((W - d.textlength(word, font=f)) / 2, y + 10), word, font=f, fill=GOLD)
        text_block(d, s.get("sub", "в комментариях — Мира сделает\nтебе личный расклад бесплатно"), y + 230, 54, width=34, alpha=ease((t - 0.4) / 0.5))
    return draw


SCENES = {"hook": scene_hook, "pick3": scene_pick3, "reveal": scene_reveal, "list": scene_list, "cta": scene_cta}


def render(script, out, voice=None):
    global BG
    BG = make_background()
    scenes = [(s, SCENES[s["type"]](s)) for s in script["scenes"]]
    total = sum(s["duration"] for s, _ in scenes)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    if voice:
        cmd += ["-i", voice, "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "160k", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    n = int(total * FPS)
    for i in range(n):
        t = i / FPS
        z = 1.0 + 0.1 * (t / total)  # медленный наезд
        cw, ch = int(W * 1.15 / z), int(H * 1.15 / z)
        ox, oy = (BG.width - cw) // 2, (BG.height - ch) // 2
        frame = BG.crop((ox, oy, ox + cw, oy + ch)).resize((W, H), Image.BILINEAR).convert("RGBA")
        d = ImageDraw.Draw(frame)
        acc = 0
        for s, draw in scenes:
            if t < acc + s["duration"]:
                draw(frame, d, t - acc)
                break
            acc += s["duration"]
        f = font(40, 600)
        d.text((W - 60 - d.textlength("@mira.taro", font=f), H - 110), "@mira.taro", font=f, fill=GOLD + (160,))
        proc.stdin.write(frame.convert("RGB").tobytes())
    proc.stdin.close()
    proc.wait()


if __name__ == "__main__":
    render(json.loads(Path(sys.argv[1]).read_text()), sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
