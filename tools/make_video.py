"""Ролик в фирменном стиле v3: фон-алтарь со свечами, искры и сияния, контрастные карты, плавные субтитры.

python -m tools.make_video reels/01_pick3_he_thinks.json out/01.mp4 [--engine edge|rhvoice|silent|file --audio voice.mp3] [--send] [--handle @mira.taro]

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
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools import audio, voice  # noqa: E402
from tools.make_reel import card_img as raw_card, ease  # noqa: E402

W, H, FPS = 1080, 1920, 30
GOLD = (222, 180, 112)
GOLD_HI = (250, 208, 128)
CHAMPAGNE = (244, 228, 200)  # основной цвет субтитров — тёплый, не чисто белый
EMBER = (255, 150, 70)
SHADOW = (14, 6, 20)
FONTS = ROOT / "assets" / "fonts"
BG_FILE = ROOT / "assets" / "video_bg.png"
TITLE_FONT = "Lighthaus"   # ар-нуво капитель, есть вся кириллица
ACCENT_FONT = "Garamond"   # курсив для мелких подписей (в нём нет «ё»)
MIN_DUR = {"hook": 2.2, "pick3": 3.0, "reveal": 3.0, "list": 4.0, "cta": 3.2}
SUB_Y = 1450  # центр строки субтитров: выше интерфейса Reels, ниже карт
_fonts = {}


def font(name, size):
    key = (name, size)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(str(FONTS / f"{name}.ttf"), size)
    return _fonts[key]


def scale(im, k):
    out = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS)
    if hasattr(im, "offset"):
        out.offset = (im.offset[0] * k, im.offset[1] * k)
    return out


def with_alpha(im, a):
    if a >= 1:
        return im
    out = im.copy()
    out.putalpha(im.getchannel("A").point(lambda v: int(v * max(0.0, a))))
    if hasattr(im, "offset"):
        out.offset = im.offset
    return out


def put(frame, im, x, y):
    frame.alpha_composite(im, (int(x), int(y)))


# ---------- Фон и цветокоррекция ----------

# Свечи на фоне (координаты в исходной картинке 941×1672) — у каждой мерцает ореол
CANDLES = [(62, 95), (20, 275), (925, 185), (828, 320), (16, 965), (915, 1225)]


def make_background():
    """Фон из assets/video_bg.png, чуть больше кадра — под медленный наезд камеры."""
    src = Image.open(BG_FILE).convert("RGB")
    k = max(W * 1.1 / src.width, H * 1.1 / src.height)
    bg = src.resize((int(src.width * k), int(src.height * k)), Image.LANCZOS)
    bg = ImageEnhance.Contrast(bg).enhance(1.08)
    bg.candles = [(x * k, y * k) for x, y in CANDLES]
    return bg


def glow(size, color=(255, 210, 140), strength=150):
    g = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(g).ellipse([size * 0.2, size * 0.2, size * 0.8, size * 0.8], fill=color + (strength,))
    return g.filter(ImageFilter.GaussianBlur(size // 7))


@lru_cache(None)
def vignette():
    m = Image.new("L", (W // 4, H // 4), 0)
    ImageDraw.Draw(m).ellipse([-W // 16, H // 20, W // 4 + W // 16, H // 4 - H // 20], fill=255)
    m = m.filter(ImageFilter.GaussianBlur(70)).resize((W, H), Image.BILINEAR)
    v = Image.new("RGBA", (W, H), SHADOW + (0,))
    v.putalpha(m.point(lambda a: int((255 - a) * 0.75)))
    # Затемнение под заголовками (верх) и субтитрами — текст читается поверх свечей
    zones = Image.new("L", (W // 4, H // 4), 0)
    zd = ImageDraw.Draw(zones)
    zd.ellipse([-20, -60, W // 4 + 20, 140], fill=150)
    zd.ellipse([0, SUB_Y // 4 - 40, W // 4, SUB_Y // 4 + 40], fill=130)
    zones = zones.filter(ImageFilter.GaussianBlur(22)).resize((W, H), Image.BILINEAR)
    v.putalpha(ImageChops.lighter(v.getchannel("A"), zones))
    return v


def grade(frame):
    """Свечение светлых участков (bloom) + виньетка."""
    small = frame.convert("RGB").resize((W // 6, H // 6), Image.BILINEAR)
    bright = small.point(lambda v: max(0, v - 150) * 2).filter(ImageFilter.GaussianBlur(9))
    rgb = ImageChops.screen(frame.convert("RGB"), bright.resize((W, H), Image.BILINEAR))
    out = rgb.convert("RGBA")
    out.alpha_composite(vignette())
    return out


# ---------- Частицы ----------

def flare(size):
    """Четырёхлучевая звёздочка-блик."""
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = size / 2
    for w, a in [(size * 0.05, 90), (size * 0.02, 255)]:
        d.polygon([(c, 0), (c + w, c), (c, size), (c - w, c)], fill=(255, 236, 200, a))
        d.polygon([(0, c), (c, c - w), (size, c), (c, c + w)], fill=(255, 236, 200, a))
    im = im.filter(ImageFilter.GaussianBlur(size / 60))
    im.alpha_composite(glow(size, (255, 220, 160), 170).resize((size, size)))
    return im


class Sparks:
    """Золотая пыль, тлеющие искры снизу вверх и мерцающие блики."""

    def __init__(self, seed=11):
        rnd = random.Random(seed)
        self.dust = [(rnd.uniform(0, W), rnd.uniform(0, H), rnd.uniform(10, 30), rnd.uniform(1.0, 2.6), rnd.uniform(0, 6.28))
                     for _ in range(60)]
        self.embers = [(rnd.uniform(0, W), rnd.uniform(0, 1), rnd.uniform(60, 150), rnd.choice((10, 14, 20)), rnd.uniform(0, 6.28))
                       for _ in range(28)]
        self.glints = [(rnd.uniform(60, W - 60), rnd.uniform(150, H - 300), rnd.uniform(0, 8), rnd.choice((50, 70, 100)))
                       for _ in range(14)]
        self.ember_img = {r: glow(r * 4, EMBER, 255) for r in (10, 14, 20)}
        self.ember_core = {r: glow(r * 2, (255, 225, 170), 255) for r in (10, 14, 20)}
        self.flare_img = {s: flare(s) for s in (50, 70, 100)}

    def draw(self, frame, t):
        d = ImageDraw.Draw(frame)
        for x, y, speed, r, ph in self.dust:
            yy = (y - speed * t) % H
            xx = x + 12 * math.sin(t * 0.7 + ph)
            a = int(90 + 90 * math.sin(t * 2.3 + ph))
            d.ellipse([xx - r, yy - r, xx + r, yy + r], fill=GOLD_HI + (max(0, a),))
        for x, y0, speed, r, ph in self.embers:
            yy = H + 40 - ((y0 * (H + 80) + speed * t) % (H + 80))
            xx = x + 30 * math.sin(t * 0.9 + ph) + 10 * math.sin(t * 3.1 + ph * 2)
            life = 1 - yy / H  # гаснут, поднимаясь
            flick = 0.65 + 0.35 * math.sin(t * 9 + ph * 5)
            a = max(0.0, min(1.0, (1.1 - life) * flick))
            put(frame, with_alpha(self.ember_img[r], a * 0.8), xx - r * 2, yy - r * 2)
            put(frame, with_alpha(self.ember_core[r], a), xx - r, yy - r)
        for x, y, ph, size in self.glints:
            cyc = (t + ph) % 4.0  # блик вспыхивает раз в 4 секунды
            a = max(0.0, 1 - abs(cyc - 0.5) / 0.5)
            if a:
                im = with_alpha(scale(self.flare_img[size], 0.6 + 0.4 * a), a)
                put(frame, im, x - im.width / 2, y - im.height / 2)


class Burst:
    """Сноп искр из точки: для момента открытия карты."""

    def __init__(self, cx, cy, n=60, seed=5):
        rnd = random.Random(seed)
        self.c = (cx, cy)
        self.p = [(rnd.uniform(0, 6.28), rnd.uniform(250, 900), rnd.choice((8, 12, 16)), rnd.uniform(0.6, 1.3)) for _ in range(n)]
        self.img = {r: glow(r * 3, GOLD_HI, 255) for r in (8, 12, 16)}

    def draw(self, frame, t):
        if not 0 <= t < 1.4:
            return
        for ang, v, r, life in self.p:
            if t > life:
                continue
            x = self.c[0] + math.cos(ang) * v * t
            y = self.c[1] + math.sin(ang) * v * t + 380 * t * t
            put(frame, with_alpha(self.img[r], 1 - t / life), x - r * 1.5, y - r * 1.5)


@lru_cache(None)
def rays(size=1300):
    """Мягкие лучи света за открытой картой."""
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = size / 2
    for k in range(14):
        a = k * math.pi * 2 / 14
        d.polygon([(c, c), (c + c * math.cos(a - 0.07), c + c * math.sin(a - 0.07)),
                   (c + c * math.cos(a + 0.07), c + c * math.sin(a + 0.07))], fill=(255, 175, 95, 48))
    im = im.filter(ImageFilter.GaussianBlur(18))
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).ellipse([0, 0, size, size], fill=255)
    m = m.filter(ImageFilter.GaussianBlur(size // 5))
    im.putalpha(ImageChops.multiply(im.getchannel("A"), m))
    return im


# ---------- Карты ----------

@lru_cache(None)
def card_img(card_id, width):
    """Карта под цветокоррекцию ролика: контраст, тёплые света, золотой кант."""
    im = raw_card(card_id, width)
    rgb, a = im.convert("RGB"), im.getchannel("A")
    # Тени глубже, золото ярче, фиолет уходит в тёплый сливовый — как свет свечей на фоне
    rgb = rgb.point(lambda v: int(255 * (v / 255) ** 1.35))
    rgb = ImageEnhance.Contrast(rgb).enhance(1.5)
    warm = Image.new("RGB", rgb.size, (255, 170, 120))
    rgb = Image.blend(rgb, ImageChops.multiply(rgb, warm), 0.35)
    gold = rgb.convert("L").point(lambda v: 255 if v > 120 else 0).filter(ImageFilter.GaussianBlur(max(1, width // 160)))
    rgb = Image.composite(ImageEnhance.Brightness(rgb).enhance(1.35), rgb, gold)
    rgb = ImageEnhance.Color(rgb).enhance(1.2)
    out = rgb.convert("RGBA")
    out.putalpha(a)
    return out


@lru_cache(None)
def card_glow(width):
    h = int(width * 5 / 3)
    pad = width // 3
    g = Image.new("RGBA", (width + pad * 2, h + pad * 2), (0, 0, 0, 0))
    e = pad // 3
    ImageDraw.Draw(g).rounded_rectangle([pad - e, pad - e, pad + width + e, pad + h + e], radius=width // 10, fill=(255, 175, 90, 210))
    return g.filter(ImageFilter.GaussianBlur(pad // 2.5))


def sheen(im, t, period=4.5):
    """Блик, пробегающий по карте по диагонали раз в period секунд."""
    p = (t % period) / 1.1
    if p > 1:
        return im
    w, h = im.size
    cx = -w * 0.6 + p * w * 2.2
    band = Image.new("L", (w, h), 0)
    ImageDraw.Draw(band).polygon([(cx, 0), (cx + w * 0.1, 0), (cx + w * 0.1 - h * 0.35, h), (cx - h * 0.35, h)], fill=60)
    mask = band.filter(ImageFilter.GaussianBlur(w // 20))
    mask = ImageChops.multiply(mask, im.getchannel("A"))
    out = im.copy()
    out.alpha_composite(Image.merge("RGBA", (*Image.new("RGB", im.size, (255, 240, 210)).split(), mask)))
    return out


def place_card(frame, im, x, y, t=None, lit=0.0):
    """Карта с тенью, золотым ореолом (lit 0..1) и бегущим бликом."""
    sh = Image.new("RGBA", (im.width + 80, im.height + 80), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([40, 55, im.width + 40, im.height + 55], radius=30, fill=(0, 0, 0, 170))
    put(frame, sh.filter(ImageFilter.GaussianBlur(22)), x - 40, y - 40)
    if lit > 0 and im.width > 20:
        g = with_alpha(card_glow(im.width), lit)
        put(frame, g, x - im.width // 3, y - im.width // 3)
    put(frame, sheen(im, t) if t is not None else im, x, y)


def flip(card_id, width, p):
    face = card_img("back" if p < 0.5 else card_id, width)
    w = max(2, int(face.width * abs(math.cos(p * math.pi))))
    return face.resize((w, face.height))


# ---------- Текст ----------

@lru_cache(maxsize=512)
def text_layer(text, name, size, color, glow_color=None):
    """Надпись с мягкой тенью (и золотым сиянием для активного слова) — рендерится один раз."""
    f = font(name, size)
    x0, y0, x1, y1 = f.getbbox(text)
    pad = size // 2
    w, h = x1 - x0 + pad * 2, y1 - y0 + pad * 2
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((pad - x0, pad - y0), text, font=f, fill=255)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow = mask.filter(ImageFilter.GaussianBlur(size / 9)).point(lambda v: min(255, v * 2))
    out.alpha_composite(Image.merge("RGBA", (*Image.new("RGB", (w, h), SHADOW).split(), shadow)), (0, size // 24))
    if glow_color:
        g = mask.filter(ImageFilter.GaussianBlur(size / 5)).point(lambda v: min(255, int(v * 1.6)))
        out.alpha_composite(Image.merge("RGBA", (*Image.new("RGB", (w, h), glow_color).split(), g)))
    out.alpha_composite(Image.merge("RGBA", (*Image.new("RGB", (w, h), color).split(), mask)))
    out.offset = (pad - x0, pad - y0)  # где в слое начало текста
    return out


def text_width(text, name, size):
    return font(name, size).getlength(text)


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


def draw_subs(frame, groups, t):
    """Слова проявляются по одному в момент произнесения: всплывают и наводятся на резкость, фраза плавно гаснет."""
    size = 76
    for k, g in enumerate(groups):
        start = g[0][0] - 0.08
        end = groups[k + 1][0][0] - 0.08 if k + 1 < len(groups) else g[-1][1] + 0.8
        if not start <= t < end:
            continue
        out_a = min(1.0, (end - t) / 0.18)
        space = text_width(" ", TITLE_FONT, size)
        widths = [text_width(w[2], TITLE_FONT, size) for w in g]
        x = (W - sum(widths) - space * (len(g) - 1)) / 2
        for (s, e, word), wd in zip(g, widths):
            p = ease((t - s + 0.08) / 0.28)
            if p <= 0:
                x += wd + space
                continue
            active = s - 0.05 <= t < max(e, s + 0.3)
            im = text_layer(word, TITLE_FONT, size, GOLD_HI if active else CHAMPAGNE, GOLD if active else None)
            k_ = 0.92 + 0.08 * p
            if k_ < 1:
                im = scale(im, k_)
            im = with_alpha(im, p * out_a)
            ox, oy = im.offset
            put(frame, im, x - ox + wd * (1 - k_) / 2, SUB_Y - size / 2 - oy + (1 - p) * 26)
            x += wd + space
        return


# ---------- Сцены ----------

def title(frame, text, y=200, size=92, t=9.0):
    """Заголовок: ар-нуво капитель, мягкая тень, плавное появление снизу."""
    size = int(size * 0.82)
    a = ease(t / 0.45)
    lines = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            test = f"{cur} {word}".strip()
            if cur and text_width(test, TITLE_FONT, size) > W - 150:
                lines.append(cur)
                cur = word
            else:
                cur = test
        lines.append(cur)
    for i, line in enumerate(lines):
        im = with_alpha(text_layer(line, TITLE_FONT, size, (255, 222, 160), (190, 110, 40)), a)
        ox, oy = im.offset
        put(frame, im, (W - text_width(line, TITLE_FONT, size)) / 2 - ox, y + i * size * 1.22 - oy + (1 - a) * 30)
    return y + len(lines) * size * 1.22


def scene_hook(s, words):
    def draw(frame, t):
        title(frame, s["text"], 230, 100, t)
        cw = 290
        k = 1 + 0.35 * (1 - ease(t / 0.35))
        for i in range(3):
            im = scale(card_img("back", cw), k)
            x = W / 2 + (i - 1) * (cw + 40) - im.width / 2
            y = 760 + 14 * math.sin(t * 2 + i) - (im.height - cw * 5 / 3) / 2
            place_card(frame, im, x, y, t - 1 - i * 0.5, lit=0.35)
    return draw


def scene_pick3(s, words):
    marks = []  # (время, номер карты), когда диктор называет «первая/вторая/третья»
    for st, _, w in words:
        for i, stem in enumerate(("перв", "втор", "трет")):
            if w.lower().startswith(stem):
                marks.append((st, i))

    def draw(frame, t):
        title(frame, s["text"], 210, 84, t)
        cw = 300
        for i in range(3):
            lit = max([max(0.0, 1 - abs(t - st - 0.35) / 0.5) for st, n in marks if n == i] or [0.0])
            x = 60 + i * (cw + 30)
            y = 700 + 12 * math.sin(t * 2 + i) - 25 * lit
            place_card(frame, card_img("back", cw), x, y, t - 1.5 - i * 0.5, lit=0.3 + 0.7 * lit)
            num = str(i + 1)
            im = text_layer(num, ACCENT_FONT, 130, GOLD_HI if lit > 0.3 else GOLD, GOLD if lit > 0.3 else None)
            put(frame, im, x + cw / 2 - text_width(num, ACCENT_FONT, 130) / 2 - im.offset[0], y + cw * 5 / 3 + 30 - im.offset[1])
    return draw


def scene_reveal(s, words):
    cw, top = 560, 320
    burst = Burst(W / 2, top + cw * 5 / 6)

    def draw(frame, t):
        if s.get("title"):
            title(frame, s["title"], 160, 78, t)
        p = ease((t - 0.1) / 0.6)
        cy = top + cw * 5 / 6
        if p >= 0.5:
            r = rays().rotate(t * 6, resample=Image.BILINEAR)
            put(frame, with_alpha(r, min(1.0, (t - 0.4) / 0.5)), W / 2 - r.width / 2, cy - r.height / 2)
        im = flip(s["card"], cw, p)
        place_card(frame, im, (W - im.width) / 2, top + 8 * math.sin(t * 1.5), t - 0.9 if p >= 1 else None,
                   lit=(0.6 + 0.25 * math.sin(t * 3)) if p >= 0.5 else 0.2)
        burst.draw(frame, t - 0.4)
        flash = max(0.0, 1 - abs(t - 0.4) / 0.2)
        if flash:
            g = scale(GLOW, 2.6)
            put(frame, with_alpha(g, flash), W / 2 - g.width / 2, cy - g.height / 2)
    return draw


def scene_list(s, words):
    n = len(s["items"])

    def draw(frame, t):
        title(frame, s["text"], 190, 80, t)
        per = max(0.6, (s["duration"] - 0.8) / n)
        for i, item in enumerate(s["items"]):
            order = n - 1 - i if s.get("countdown") else i
            a = ease((t - 0.4 - order * per) / 0.35)
            if a <= 0:
                continue
            y = 520 + i * (760 / n)
            num = text_layer(f"{i + 1}.", ACCENT_FONT, 80, GOLD)
            put(frame, with_alpha(num, a), 120 - num.offset[0], y - num.offset[1])
            im = text_layer(item, TITLE_FONT, 56, CHAMPAGNE)
            put(frame, with_alpha(im, a), 220 - im.offset[0] + (1 - a) * 40, y + 10 - im.offset[1])
    return draw


def scene_cta(s, words):
    def draw(frame, t):
        title(frame, s.get("text", "Напиши в комментариях"), 250, 84, t)
        place_card(frame, card_img("back", 340), (W - 340) / 2, 520 + 10 * math.sin(t * 2), t, lit=0.7 + 0.3 * math.sin(t * 3))
        word = s.get("word", "КАРТА")
        size = 160
        pulse = 1 + 0.06 * math.sin(t * 6)
        im = scale(text_layer(word, TITLE_FONT, size, GOLD_HI, (255, 170, 80)), pulse)
        a = ease((t - 0.3) / 0.4)
        put(frame, with_alpha(im, a), (W - im.width) / 2, 1180 - im.height / 2)
    return draw


SCENES = {"hook": scene_hook, "pick3": scene_pick3, "reveal": scene_reveal, "list": scene_list, "cta": scene_cta}


# ---------- Сборка ----------

def build(script, out, engine="edge", handle="@mira.taro", audio_file=None, stills=None):
    global GLOW
    GLOW = glow(700, (255, 200, 130))
    tmp = Path(tempfile.mkdtemp())
    timeline, words_all, voice_parts, fx, t0 = [], [], [], [], 0.0
    parts = None
    if engine == "file":
        voiced = [s for s in script["scenes"] if s.get("say")]
        parts = iter(voice.split_by_pauses(audio_file, len(voiced), tmp))
    for i, s in enumerate(script["scenes"]):
        say = s.get("say", "")
        if say:
            wav = tmp / f"s{i}.wav"
            if parts:
                wav = next(parts)
                length, words = voice.timed(say, wav)
            else:
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
    sparks = Sparks()
    candle_glow = glow(260, (255, 160, 70), 120)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", "-i", str(track), "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "192k",
           "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
           "-shortest", str(out)]
    times = [n / FPS for n in range(int(total * FPS))] if stills is None else stills
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE) if stills is None else None
    for t in times:
        # Медленный наезд камеры на фон
        z = 1.0 + 0.08 * (t / total)
        cw, ch = int(W * 1.1 / z), int(H * 1.1 / z)
        ox, oy = (bg.width - cw) // 2, (bg.height - ch) // 2
        frame = bg.crop((ox, oy, ox + cw, oy + ch)).resize((W, H), Image.BILINEAR).convert("RGBA")
        for i, (cx, cy) in enumerate(bg.candles):
            flick = 0.55 + 0.25 * math.sin(t * 7.3 + i * 2.1) + 0.2 * math.sin(t * 13.7 + i)
            put(frame, with_alpha(candle_glow, flick), (cx - ox) * W / cw - 130, (cy - oy) * H / ch - 150)
        frame = grade(frame)
        sparks.draw(frame, t)
        for start, dur, draw in timeline:
            if t < start + dur:
                draw(frame, t - start)
                break
        draw_subs(frame, groups, t)
        # Полоса прогресса сверху и подпись канала
        d = ImageDraw.Draw(frame)
        d.rectangle([0, 0, int(W * t / total), 6], fill=GOLD_HI)
        tag = text_layer(handle, ACCENT_FONT, 44, GOLD)
        put(frame, tag, W - 50 - text_width(handle, ACCENT_FONT, 44) - tag.offset[0], 56 - tag.offset[1])
        if proc:
            proc.stdin.write(frame.convert("RGB").tobytes())
        else:
            frame.convert("RGB").save(Path(out).with_suffix(f".{t:05.2f}.jpg"), quality=90)
    if proc:
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
    ap.add_argument("--audio", help="готовая озвучка для --engine file")
    ap.add_argument("--stills", help="вместо видео — кадры в эти секунды, через запятую (быстрая проверка)")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    secs = build(json.loads(Path(a.script).read_text()), a.out, a.engine, a.handle, a.audio,
                 [float(x) for x in a.stills.split(",")] if a.stills else None)
    print(f"Готово: {a.out}, {secs:.1f} сек")
    if a.send:
        import asyncio
        asyncio.run(send_telegram(a.out, f"Пробный ролик: {Path(a.script).stem}"))
