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
from tools import audio, broll, voice  # noqa: E402
from tools.make_reel import card_img as raw_card, ease  # noqa: E402

W, H, FPS = 1080, 1920, 30
GOLD = (222, 180, 112)
GOLD_HI = (250, 208, 128)
CHAMPAGNE = (222, 204, 176)  # основной цвет субтитров — приглушённое шампанское, не белый
TITLE_GOLD = (226, 190, 136)
EMBER = (255, 150, 70)
SHADOW = (14, 6, 20)
FONTS = ROOT / "assets" / "fonts"
BG_FILE = ROOT / "assets" / "video_bg.png"
TITLE_FONT = "Lighthaus"   # ар-нуво капитель, есть вся кириллица
ACCENT_FONT = "Garamond"   # курсив для мелких подписей (в нём нет «ё»)
MIN_DUR = {"hook": 2.2, "pick3": 3.0, "reveal": 3.0, "list": 4.0, "cta": 3.2, "stop": 8.0}
# Безопасная зона Reels: сверху шапка (~250 px), снизу подпись и профиль (от ~1480),
# справа колонка лайков/комментариев (x > ~930 при y > ~1000). Всё важное — внутри.
SUB_Y = 1300
SUB_CX = W // 2  # фразы короткие (до ~600 px), так что по центру до кнопок справа не достают
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
    bg = ImageEnhance.Brightness(ImageEnhance.Color(bg).enhance(0.85)).enhance(0.68)
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
    v.putalpha(m.point(lambda a: int((255 - a) * 0.9)))
    # Затемнение под заголовками (верх) и субтитрами — текст читается поверх свечей
    zones = Image.new("L", (W // 4, H // 4), 0)
    zd = ImageDraw.Draw(zones)
    zd.ellipse([-20, -40, W // 4 + 20, 120], fill=120)
    zd.ellipse([0, SUB_Y // 4 - 35, W // 4, SUB_Y // 4 + 35], fill=120)
    zd.rectangle([0, 370, W // 4, H // 4], fill=170)  # низ кадра под интерфейсом уходит в темноту
    zones = zones.filter(ImageFilter.GaussianBlur(22)).resize((W, H), Image.BILINEAR)
    v.putalpha(ImageChops.lighter(v.getchannel("A"), zones))
    return v


def grade(frame):
    """Свечение светлых участков (bloom) + виньетка."""
    small = frame.convert("RGB").resize((W // 6, H // 6), Image.BILINEAR)
    bright = small.point(lambda v: int(max(0, v - 185) * 1.4)).filter(ImageFilter.GaussianBlur(9))
    rgb = ImageChops.screen(frame.convert("RGB"), bright.resize((W, H), Image.BILINEAR))
    out = rgb.convert("RGBA")
    out.alpha_composite(vignette())
    return out


class Fog:
    """Медленно плывущий дым: два слоя навстречу друг другу, добавляет глубины и тайны."""

    def __init__(self, seed=21):
        rnd = random.Random(seed)
        self.layers = []
        for k in range(2):
            small = Image.new("L", (36, 32))
            small.putdata([int(rnd.random() ** 2.2 * 255) for _ in range(36 * 32)])
            big = small.resize((W * 2, H), Image.BICUBIC).filter(ImageFilter.GaussianBlur(70))
            tint = Image.new("RGBA", big.size, (70, 50, 90, 0) if k == 0 else (20, 10, 30, 0))
            tint.putalpha(big.point(lambda v: int(v * (0.35 if k == 0 else 0.5))))
            self.layers.append(tint)

    def draw(self, frame, t):
        for k, layer in enumerate(self.layers):
            x = int((t * (14 if k == 0 else -9)) % W)
            frame.alpha_composite(layer.crop((x, 0, x + W, H)))


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
                     for _ in range(35)]
        self.embers = [(rnd.uniform(0, W), rnd.uniform(0, 1), rnd.uniform(60, 150), rnd.choice((10, 14, 20)), rnd.uniform(0, 6.28))
                       for _ in range(14)]
        self.glints = [(rnd.uniform(60, W - 60), rnd.uniform(150, H - 300), rnd.uniform(0, 8), rnd.choice((50, 70, 100)))
                       for _ in range(6)]
        self.ember_img = {r: glow(r * 4, EMBER, 255) for r in (10, 14, 20)}
        self.ember_core = {r: glow(r * 2, (255, 225, 170), 255) for r in (10, 14, 20)}
        self.flare_img = {s: flare(s) for s in (50, 70, 100)}

    def draw(self, frame, t):
        d = ImageDraw.Draw(frame)
        for x, y, speed, r, ph in self.dust:
            yy = (y - speed * t) % H
            xx = x + 12 * math.sin(t * 0.7 + ph)
            a = int(50 + 60 * math.sin(t * 2.3 + ph))
            d.ellipse([xx - r, yy - r, xx + r, yy + r], fill=GOLD_HI + (max(0, a),))
        for x, y0, speed, r, ph in self.embers:
            yy = H + 40 - ((y0 * (H + 80) + speed * t) % (H + 80))
            xx = x + 30 * math.sin(t * 0.9 + ph) + 10 * math.sin(t * 3.1 + ph * 2)
            life = 1 - yy / H  # гаснут, поднимаясь
            flick = 0.65 + 0.35 * math.sin(t * 9 + ph * 5)
            a = max(0.0, min(1.0, (1.1 - life) * flick)) * 0.55
            put(frame, with_alpha(self.ember_img[r], a * 0.8), xx - r * 2, yy - r * 2)
            put(frame, with_alpha(self.ember_core[r], a), xx - r, yy - r)
        for x, y, ph, size in self.glints:
            cyc = (t + ph) % 4.0  # блик вспыхивает раз в 4 секунды
            a = max(0.0, 1 - abs(cyc - 0.5) / 0.5) * 0.6
            if a:
                im = with_alpha(scale(self.flare_img[size], 0.6 + 0.4 * a), a)
                put(frame, im, x - im.width / 2, y - im.height / 2)


class Burst:
    """Сноп искр из точки: для момента открытия карты."""

    def __init__(self, cx, cy, n=34, seed=5):
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
            put(frame, with_alpha(self.img[r], 0.7 * (1 - t / life)), x - r * 1.5, y - r * 1.5)


@lru_cache(None)
def rays(size=1300):
    """Мягкие лучи света за открытой картой."""
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = size / 2
    for k in range(14):
        a = k * math.pi * 2 / 14
        d.polygon([(c, c), (c + c * math.cos(a - 0.07), c + c * math.sin(a - 0.07)),
                   (c + c * math.cos(a + 0.07), c + c * math.sin(a + 0.07))], fill=(255, 175, 95, 26))
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
    rgb = Image.composite(ImageEnhance.Brightness(rgb).enhance(1.2), rgb, gold)
    rgb = ImageEnhance.Brightness(rgb).enhance(0.88)
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
    ImageDraw.Draw(band).polygon([(cx, 0), (cx + w * 0.1, 0), (cx + w * 0.1 - h * 0.35, h), (cx - h * 0.35, h)], fill=40)
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
        g = with_alpha(card_glow(im.width), lit * 0.55)
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


# ---------- Переносы ----------

# Предлоги, союзы и частицы не остаются в конце строки — переносятся вместе со следующим словом
GLUE = set("""в во на с со к ко о об обо у из за по от до для без под над при про через между перед
и а но или да либо то что чтобы как если когда ведь не ни же ли бы вот лишь""".split())


def glue_words(words):
    """Склеивает служебные слова со следующим, а тире — с предыдущим: список «неразрывных» кусков."""
    out = []
    for w in words:
        if out and (out[-1].split()[-1].lower() in GLUE or w in ("—", "–")):
            out[-1] += " " + w
        else:
            out.append(w)
    return out


# ---------- Субтитры ----------

def chunks(words, max_words=3, max_chars=20):
    """Группирует слова в короткие фразы субтитров."""
    out, cur = [], []
    for w in words:
        text = " ".join(x[2] for x in cur + [w])
        glued = cur and (cur[-1][2].lower() in GLUE or w[2] in ("—", "–"))
        if cur and not glued and (len(cur) >= max_words or len(text) > max_chars or re.search(r"[.!?…,]$", cur[-1][2])):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def draw_subs(frame, groups, t):
    """Слова проявляются по одному в момент произнесения: всплывают и наводятся на резкость, фраза плавно гаснет."""
    size = 56
    for k, g in enumerate(groups):
        start = g[0][0] - 0.08
        end = groups[k + 1][0][0] - 0.08 if k + 1 < len(groups) else g[-1][1] + 0.8
        if not start <= t < end:
            continue
        out_a = min(1.0, (end - t) / 0.18)
        space = text_width(" ", TITLE_FONT, size)
        widths = [text_width(w[2], TITLE_FONT, size) for w in g]
        x = SUB_CX - (sum(widths) + space * (len(g) - 1)) / 2
        for (s, e, word), wd in zip(g, widths):
            p = ease((t - s + 0.08) / 0.28)
            if p <= 0:
                x += wd + space
                continue
            active = s - 0.05 <= t < max(e, s + 0.3)
            im = text_layer(word, TITLE_FONT, size, (232, 192, 128) if active else CHAMPAGNE, (120, 72, 30) if active else None)
            k_ = 0.92 + 0.08 * p
            if k_ < 1:
                im = scale(im, k_)
            im = with_alpha(im, p * out_a)
            ox, oy = im.offset
            put(frame, im, x - ox + wd * (1 - k_) / 2, SUB_Y - size / 2 - oy + (1 - p) * 18)
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
        for word in glue_words(para.split()):
            test = f"{cur} {word}".strip()
            if cur and text_width(test, TITLE_FONT, size) > W - 200:
                lines.append(cur)
                cur = word
            else:
                cur = test
        lines.append(cur)
    for i, line in enumerate(lines):
        im = with_alpha(text_layer(line, TITLE_FONT, size, TITLE_GOLD, (110, 60, 25)), a)
        ox, oy = im.offset
        put(frame, im, (W - text_width(line, TITLE_FONT, size)) / 2 - ox, y + i * size * 1.22 - oy + (1 - a) * 30)
    return y + len(lines) * size * 1.22


def scene_hook(s, words):
    def draw(frame, t):
        title(frame, s["text"], 270, 92, t)
        cw = 250
        k = 1 + 0.35 * (1 - ease(t / 0.35))
        for i in range(3):
            im = scale(card_img("back", cw), k)
            x = W / 2 + (i - 1) * (cw + 34) - im.width / 2
            y = 630 + 12 * math.sin(t * 2 + i) - (im.height - cw * 5 / 3) / 2
            place_card(frame, im, x, y, t - 1 - i * 0.5, lit=0.35)
    return draw


def scene_pick3(s, words):
    marks = []  # (время, номер карты), когда диктор называет «первая/вторая/третья»
    for st, _, w in words:
        for i, stem in enumerate(("перв", "втор", "трет")):
            if w.lower().startswith(stem):
                marks.append((st, i))

    def draw(frame, t):
        title(frame, s["text"], 270, 80, t)
        cw = 250
        for i in range(3):
            lit = max([max(0.0, 1 - abs(t - st - 0.35) / 0.5) for st, n in marks if n == i] or [0.0])
            x = W / 2 + (i - 1) * (cw + 34) - cw / 2
            y = 600 + 10 * math.sin(t * 2 + i) - 20 * lit
            place_card(frame, card_img("back", cw), x, y, t - 1.5 - i * 0.5, lit=0.3 + 0.7 * lit)
            num = str(i + 1)
            im = text_layer(num, ACCENT_FONT, 100, (232, 192, 128) if lit > 0.3 else (170, 138, 96), (120, 72, 30) if lit > 0.3 else None)
            put(frame, im, x + cw / 2 - text_width(num, ACCENT_FONT, 100) / 2 - im.offset[0], y + cw * 5 / 3 + 24 - im.offset[1])
    return draw


def scene_reveal(s, words):
    cw, top = 470, 350
    burst = Burst(W / 2, top + cw * 5 / 6)

    def draw(frame, t):
        if s.get("title"):
            title(frame, s["title"], 255, 70, t)
        p = ease((t - 0.1) / 0.6)
        cy = top + cw * 5 / 6
        if p >= 0.5:
            r = rays().rotate(t * 6, resample=Image.BILINEAR)
            put(frame, with_alpha(r, min(1.0, (t - 0.4) / 0.5)), W / 2 - r.width / 2, cy - r.height / 2)
        im = flip(s["card"], cw, p)
        place_card(frame, im, (W - im.width) / 2, top + 8 * math.sin(t * 1.5), t - 0.9 if p >= 1 else None,
                   lit=(0.55 + 0.2 * math.sin(t * 3)) if p >= 0.5 else 0.2)
        burst.draw(frame, t - 0.4)
        flash = max(0.0, 1 - abs(t - 0.4) / 0.2)
        if flash:
            g = scale(GLOW, 2.6)
            put(frame, with_alpha(g, flash * 0.45), W / 2 - g.width / 2, cy - g.height / 2)
    return draw


def scene_list(s, words):
    n = len(s["items"])

    per = max(0.6, (s["duration"] - 0.8) / n)
    # Пункт появляется в момент, когда голос его называет; если не нашли — равномерно
    starts = {}
    for i, item in enumerate(s["items"]):
        key = re.sub(r"[^а-яё]", "", item.split()[0].lower())[:5]
        hit = [a for a, b, w in words if re.sub(r"[^а-яё]", "", w.lower()).startswith(key)]
        order = n - 1 - i if s.get("countdown") else i
        starts[i] = hit[0] - 0.1 if hit and key else 0.4 + order * per

    def draw(frame, t):
        title(frame, s["text"], 270, 76, t)
        for i, item in enumerate(s["items"]):
            a = ease((t - starts[i]) / 0.35)
            if a <= 0:
                continue
            y = 480 + i * (680 / n)
            num = text_layer(f"{i + 1}.", ACCENT_FONT, 80, GOLD)
            put(frame, with_alpha(num, a), 120 - num.offset[0], y - num.offset[1])
            im = text_layer(item, TITLE_FONT, 56, CHAMPAGNE)
            put(frame, with_alpha(im, a), 220 - im.offset[0] + (1 - a) * 40, y + 10 - im.offset[1])
    return draw


def scene_cta(s, words):
    def draw(frame, t):
        title(frame, s.get("text", "Напиши в комментариях"), 270, 80, t)
        place_card(frame, card_img("back", 290), (W - 290) / 2, 470 + 10 * math.sin(t * 2), t, lit=0.6 + 0.25 * math.sin(t * 3))
        word = s.get("word", "КАРТА")
        size = 130
        pulse = 1 + 0.04 * math.sin(t * 4)
        im = scale(text_layer(word, TITLE_FONT, size, (236, 196, 130), (150, 85, 30)), pulse)
        a = ease((t - 0.3) / 0.4)
        put(frame, with_alpha(im, a), SUB_CX - im.width / 2, 1090 - im.height / 2)
    return draw


def scene_stop(s, words):
    """«Останови видео»: лица карт быстро сменяют друг друга; на любом стоп-кадре видна целая карта."""
    cw, top = 470, 350
    cards = s["cards"]
    period = float(s.get("period", 0.6))

    def draw(frame, t):
        if s.get("title"):
            title(frame, s["title"], 255, 70, t)
        cy = top + cw * 5 / 6
        r = rays().rotate(t * 6, resample=Image.BILINEAR)
        put(frame, with_alpha(r, min(1.0, t / 0.5)), W / 2 - r.width / 2, cy - r.height / 2)
        if t < 0.3:  # первая карта переворачивается из рубашки
            im = flip(cards[0], cw, ease(t / 0.3))
        else:
            im = card_img(cards[int((t - 0.3) / period) % len(cards)], cw)
        place_card(frame, im, (W - im.width) / 2, top, None, lit=0.55)
    return draw


SCENES = {"stop": scene_stop, "hook": scene_hook, "pick3": scene_pick3, "reveal": scene_reveal, "list": scene_list, "cta": scene_cta}


def draw_safe_guides(frame):
    """Для проверки: красным — где Reels рисует свой интерфейс."""
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.rectangle([0, 0, W, 230], fill=(255, 0, 0, 70))            # шапка «Reels», камера
    d.rectangle([0, 1480, W, H], fill=(255, 0, 0, 70))           # ник, подпись, музыка
    d.rectangle([940, 1000, W, 1480], fill=(255, 0, 0, 70))      # лайк, комменты, репост
    frame.alpha_composite(ov)


# ---------- Сборка ----------

def build(script, out, engine="edge", handle="@mira.taro", audio_file=None, stills=None, safe_guides=False):
    global GLOW
    GLOW = glow(700, (255, 200, 130))
    tmp = Path(tempfile.mkdtemp())
    timeline, words_all, voice_parts, fx, t0 = [], [], [], [], 0.0
    parts = None
    if engine == "file":
        voiced = [s for s in script["scenes"] if s.get("say")]
        parts = iter(voice.split_by_pauses(audio_file, len(voiced), tmp, script.get("voice_cuts")))
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
        timeline.append((t0, dur, SCENES[s["type"]](s, local),
                         broll.Broll(s["broll"], ROOT, dur) if s.get("broll") else None))
        words_all += [(t0 + a, t0 + b, w) for a, b, w in local]
        if s["type"] == "reveal":
            fx.append((t0 + 0.28, "card"))
        elif s["type"] in ("hook", "cta"):
            fx.append((t0, "whoosh"))
        t0 += dur
    total = t0
    groups = chunks(words_all)

    track = tmp / "mix.wav"
    audio.write_wav(track, audio.mix(total, voice_parts, fx))

    bg = make_background()
    sparks = Sparks()
    candle_glow = glow(260, (255, 150, 60), 70)
    fog = Fog()
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
        fog.draw(frame, t)
        frame = grade(frame)
        for start, dur, _, br in timeline:
            if t < start + dur:
                if br:  # видео-«воспоминание» рядом с картой
                    frame = broll.screen(frame, br.layer(t - start))
                break
        sparks.draw(frame, t)
        for start, dur, draw, _ in timeline:
            if t < start + dur:
                draw(frame, t - start)
                break
        draw_subs(frame, groups, t)
        if safe_guides:
            draw_safe_guides(frame)
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
    ap.add_argument("--guides", action="store_true", help="показать зоны интерфейса Reels")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    secs = build(json.loads(Path(a.script).read_text()), a.out, a.engine, a.handle, a.audio,
                 [float(x) for x in a.stills.split(",")] if a.stills else None, a.guides)
    print(f"Готово: {a.out}, {secs:.1f} сек")
    if a.send:
        import asyncio
        asyncio.run(send_telegram(a.out, f"Пробный ролик: {Path(a.script).stem}"))
