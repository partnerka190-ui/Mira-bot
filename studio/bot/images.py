"""Склейка карт в одну картинку: расклад целиком приходит одним фото, без обрезки и размытия."""
import io
import random
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config

GOLD = (217, 178, 111)
CREAM = (246, 236, 220)
FONT = config.BASE_DIR / "assets" / "fonts"


def font(size, weight=600):
    return ImageFont.truetype(str(FONT / f"Cormorant-{weight}.ttf"), size)


@lru_cache(maxsize=8)
def background(w, h):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    top, bottom = (13, 12, 32), (44, 24, 66)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    glow = Image.new("L", (w, h), 0)
    ImageDraw.Draw(glow).ellipse([w * 0.1, h * 0.15, w * 0.9, h * 0.85], fill=80)
    img = Image.composite(Image.new("RGB", (w, h), (110, 70, 170)), img, glow.filter(ImageFilter.GaussianBlur(min(w, h) // 5)))
    d = ImageDraw.Draw(img)
    rnd = random.Random(w * h)
    for _ in range(w * h // 9000):
        x, y, r = rnd.uniform(0, w), rnd.uniform(0, h), rnd.choice([1, 1, 2, 2, 3])
        d.ellipse([x - r, y - r, x + r, y + r], fill=rnd.choice([(255, 244, 225), (200, 190, 230), (230, 210, 170)]))
    return img


@lru_cache(maxsize=256)
def card(card_id, width):
    path = config.CARDS_DIR / f"{card_id}.png"
    im = Image.open(path).convert("RGBA")
    return im.resize((width, round(width * im.height / im.width)), Image.LANCZOS)


def paste_shadowed(img, im, x, y):
    pad = 40
    sh = Image.new("RGBA", (im.width + pad * 2, im.height + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([pad, pad + 14, im.width + pad, im.height + pad + 14], radius=24, fill=(0, 0, 0, 150))
    sh = sh.filter(ImageFilter.GaussianBlur(18))
    img.paste(sh, (x - pad, y - pad), sh)
    img.paste(im, (x, y), im)


def centered(d, text, cx, y, size, color=GOLD, weight=600):
    f = font(size, weight)
    d.text((cx - d.textlength(text, font=f) / 2, y), text, font=f, fill=color)


def spread(card_ids, labels=None, hidden=(), cols=None, card_w=None):
    """Карты в ряд (или в несколько рядов), подписи под каждой. hidden — индексы карт рубашкой вверх."""
    n = len(card_ids)
    cols = cols or n
    rows = -(-n // cols)
    card_w = card_w or {1: 620, 2: 600, 3: 540, 4: 400}.get(cols, 380)
    card_h = round(card_w * 5 / 3)
    gap, margin, label_h = 44, 60, 90 if labels else 20
    w = cols * card_w + (cols - 1) * gap + margin * 2
    h = rows * (card_h + label_h) + (rows - 1) * 40 + margin * 2
    img = background(w, h).copy().convert("RGBA")
    d = ImageDraw.Draw(img)
    for i, cid in enumerate(card_ids):
        r, c = divmod(i, cols)
        in_row = min(cols, n - r * cols)
        x0 = (w - (in_row * card_w + (in_row - 1) * gap)) // 2
        x = x0 + c * (card_w + gap)
        y = margin + r * (card_h + label_h + 40)
        paste_shadowed(img, card("back" if i in hidden else cid, card_w), x, y)
        if labels:
            centered(d, labels[i], x + card_w / 2, y + card_h + 22, 44 if card_w >= 500 else 36)
    return to_jpeg(img)


def fan(count=7):
    """Веер карт рубашкой вверх с номерами — из него выбирают карты."""
    card_w, gap, margin = 220, 22, 50
    card_h = round(card_w * 5 / 3)
    w = count * card_w + (count - 1) * gap + margin * 2
    h = card_h + margin * 2 + 90
    img = background(w, h).copy().convert("RGBA")
    d = ImageDraw.Draw(img)
    for i in range(count):
        x = margin + i * (card_w + gap)
        y = margin + abs(i - (count - 1) / 2) * 10
        paste_shadowed(img, card("back", card_w), x, int(y))
        centered(d, str(i + 1), x + card_w / 2, y + card_h + 18, 60, weight=700)
    return to_jpeg(img)


def to_jpeg(img):
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=93, subsampling=0)
    return buf.getvalue()
