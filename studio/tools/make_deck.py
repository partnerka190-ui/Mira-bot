"""Рисует фирменную колоду «Мира»: 78 карт + рубашка. Запуск: python -m tools.make_deck [id ...]

Если в assets/art/ есть иллюстрация карты (её рисует tools/gen_art.py через GigaChat),
она вставляется в арочное окно ар-нуво; иначе в окне рисуется золотая эмблема."""
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bot.cards import DECK  # noqa: E402

W, H, S = 900, 1500, 2  # итоговый размер и множитель сглаживания
GOLD = (217, 178, 111)
GOLD_SOFT = (217, 178, 111, 90)
TOP_C, BOTTOM_C = (16, 14, 38), (52, 28, 74)
FONT = ROOT / "assets" / "fonts" / "Cormorant-600.ttf"
OUT = ROOT / "assets" / "cards"
ART = ROOT / "assets" / "art"
# Арочное окно под иллюстрацию: x0, y0, x1, y1 в итоговых пикселях
WIN = (96, 178, 804, 1178)


def font(size):
    return ImageFont.truetype(str(FONT), size * S)


def background(seed):
    img = Image.new("RGB", (W * S, H * S))
    d = ImageDraw.Draw(img)
    for y in range(H * S):
        t = y / (H * S)
        d.line([(0, y), (W * S, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(TOP_C, BOTTOM_C)))
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([W * S * 0.1, H * S * 0.22, W * S * 0.9, H * S * 0.72], fill=(140, 90, 200, 70))
    img.paste(glow.filter(ImageFilter.GaussianBlur(160)), (0, 0), glow.filter(ImageFilter.GaussianBlur(160)))
    rnd = random.Random(seed)
    for _ in range(140):
        x, y, r = rnd.uniform(0, W * S), rnd.uniform(0, H * S), rnd.choice([1, 1, 2, 3])
        a = rnd.randint(60, 200)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 240, 220) if a > 150 else (180, 170, 200))
    return img


def frame(d):
    m1, m2 = 34 * S, 50 * S
    d.rounded_rectangle([m1, m1, W * S - m1, H * S - m1], radius=28 * S, outline=GOLD, width=3 * S)
    d.rounded_rectangle([m2, m2, W * S - m2, H * S - m2], radius=20 * S, outline=GOLD, width=1 * S)
    for cx, cy in [(m2, m2), (W * S - m2, m2), (m2, H * S - m2), (W * S - m2, H * S - m2)]:
        r = 9 * S
        d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=GOLD)


def star(d, cx, cy, r_out, r_in, points, width=3, fill=None):
    pts = []
    for i in range(points * 2):
        r = r_out if i % 2 == 0 else r_in
        a = math.pi * i / points - math.pi / 2
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    d.polygon(pts, outline=GOLD, width=width * S, fill=fill)


def crescent(img, cx, cy, r, shift):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.ellipse([cx - r, cy - r, cx + r, cy + r], fill=GOLD + (255,))
    ld.ellipse([cx - r + shift, cy - r - shift * 0.3, cx + r + shift, cy + r - shift * 0.3], fill=(0, 0, 0, 0))
    img.paste(layer, (0, 0), layer)


def circle(d, cx, cy, r, width=3):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=GOLD, width=width * S)


def emblem_major(d, idx, name, cx, cy, R):
    circle(d, cx, cy, R, 2)
    circle(d, cx, cy, R * 0.92, 1)
    if name == "Солнце":
        circle(d, cx, cy, R * 0.38, 4)
        for i in range(24):
            a = 2 * math.pi * i / 24
            r1, r2 = R * 0.48, R * (0.8 if i % 2 == 0 else 0.66)
            d.line([(cx + r1 * math.cos(a), cy + r1 * math.sin(a)), (cx + r2 * math.cos(a), cy + r2 * math.sin(a))], fill=GOLD, width=3 * S)
    elif name == "Луна":
        crescent(d._image, cx, cy, R * 0.55, R * 0.3)
    elif name == "Звезда":
        star(d, cx, cy, R * 0.72, R * 0.28, 8, 3)
        star(d, cx, cy, R * 0.3, R * 0.12, 8, 2)
    elif name == "Колесо Фортуны":
        circle(d, cx, cy, R * 0.62, 3)
        circle(d, cx, cy, R * 0.18, 3)
        for i in range(8):
            a = 2 * math.pi * i / 8
            d.line([(cx + R * 0.18 * math.cos(a), cy + R * 0.18 * math.sin(a)), (cx + R * 0.62 * math.cos(a), cy + R * 0.62 * math.sin(a))], fill=GOLD, width=3 * S)
    elif name == "Башня":
        d.rectangle([cx - R * 0.18, cy - R * 0.5, cx + R * 0.18, cy + R * 0.62], outline=GOLD, width=3 * S)
        d.line([(cx + R * 0.5, cy - R * 0.7), (cx + R * 0.1, cy - R * 0.15), (cx + R * 0.35, cy - R * 0.1), (cx - R * 0.15, cy + R * 0.4)], fill=GOLD, width=4 * S)
    elif name == "Мир":
        d.ellipse([cx - R * 0.42, cy - R * 0.66, cx + R * 0.42, cy + R * 0.66], outline=GOLD, width=3 * S)
        star(d, cx, cy, R * 0.26, R * 0.1, 5, 2)
    elif name == "Верховная Жрица":
        d.line([(cx - R * 0.4, cy - R * 0.6), (cx - R * 0.4, cy + R * 0.6)], fill=GOLD, width=4 * S)
        d.line([(cx + R * 0.4, cy - R * 0.6), (cx + R * 0.4, cy + R * 0.6)], fill=GOLD, width=4 * S)
        circle(d, cx, cy, R * 0.22, 3)
    else:
        points = 3 + idx % 6
        star(d, cx, cy, R * 0.7, R * (0.28 + 0.04 * (idx % 3)), points, 3)
        circle(d, cx, cy, R * (0.16 + 0.03 * (idx % 4)), 3)
        if idx % 2:
            circle(d, cx, cy, R * 0.8, 1)


def suit_symbol(d, suit, cx, cy, r):
    w = max(2, int(r / 18)) * S
    if suit == 0:  # жезлы
        d.line([(cx, cy - r), (cx, cy + r)], fill=GOLD, width=w)
        d.polygon([(cx, cy - r * 1.2), (cx + r * 0.25, cy - r * 0.9), (cx, cy - r * 0.6), (cx - r * 0.25, cy - r * 0.9)], outline=GOLD, width=w)
    elif suit == 1:  # кубки
        d.arc([cx - r * 0.6, cy - r * 0.9, cx + r * 0.6, cy + r * 0.1], 0, 180, fill=GOLD, width=w)
        d.line([(cx - r * 0.6, cy - r * 0.4), (cx + r * 0.6, cy - r * 0.4)], fill=GOLD, width=w)
        d.line([(cx, cy + r * 0.1), (cx, cy + r * 0.7)], fill=GOLD, width=w)
        d.line([(cx - r * 0.35, cy + r * 0.75), (cx + r * 0.35, cy + r * 0.75)], fill=GOLD, width=w)
    elif suit == 2:  # мечи: клинок вверх, гарда, рукоять и навершие
        bw = r * 0.09
        d.polygon([(cx, cy - r * 1.2), (cx + bw, cy - r * 1.0), (cx + bw, cy + r * 0.3), (cx - bw, cy + r * 0.3), (cx - bw, cy - r * 1.0)], outline=GOLD, width=w)
        d.line([(cx - r * 0.32, cy + r * 0.32), (cx + r * 0.32, cy + r * 0.32)], fill=GOLD, width=w)
        d.line([(cx, cy + r * 0.34), (cx, cy + r * 0.75)], fill=GOLD, width=w)
        pr = r * 0.09
        d.ellipse([cx - pr, cy + r * 0.75, cx + pr, cy + r * 0.75 + pr * 2], fill=GOLD)
    else:  # пентакли
        circle(d, cx, cy, r * 0.75, max(1, int(r / 30)))
        star(d, cx, cy, r * 0.62, r * 0.24, 5, max(1, int(r / 30)))


def emblem_minor(d, card, cx, cy, R):
    suit, rank = card["suit"], int(card["id"][2:])
    circle(d, cx, cy, R, 1)
    if rank == 0 or rank >= 10:  # туз и придворные — один крупный знак
        suit_symbol(d, suit, cx, cy, R * 0.55)
        if rank >= 10:
            star(d, cx, cy - R * 0.82, R * 0.1, R * 0.04, 5, 2, fill=GOLD)
        return
    n = rank + 1
    cols = 1 if n == 1 else 2 if n <= 4 else 3
    rows = math.ceil(n / cols)
    sp_x, sp_y = R * 0.55, R * 1.3 / max(rows, 2)
    r = min(R * 0.24, sp_y * 0.42)
    k = 0
    for row in range(rows):
        in_row = min(cols, n - k)
        for col in range(in_row):
            x = cx + (col - (in_row - 1) / 2) * sp_x
            y = cy + (row - (rows - 1) / 2) * sp_y
            suit_symbol(d, suit, x, y, r)
            k += 1


def centered(d, text, y, size):
    f = font(size)
    w = d.textlength(text, font=f)
    d.text(((W * S - w) / 2, y), text, font=f, fill=GOLD)


def arch_mask(box, inset=0):
    x0, y0, x1, y1 = [v * S for v in box]
    x0, y0, x1, y1 = x0 + inset, y0 + inset, x1 - inset, y1 - inset
    r = (x1 - x0) / 2
    m = Image.new("L", (W * S, H * S), 0)
    d = ImageDraw.Draw(m)
    d.ellipse([x0, y0, x1, y0 + 2 * r], fill=255)
    d.rectangle([x0, y0 + r, x1, y1], fill=255)
    return m


def arch_outline(d, box, inset, width):
    x0, y0, x1, y1 = [v * S for v in box]
    x0, y0, x1, y1 = x0 + inset, y0 + inset, x1 - inset, y1 - inset
    r = (x1 - x0) / 2
    d.arc([x0, y0, x1, y0 + 2 * r], 180, 360, fill=GOLD, width=width * S)
    d.line([(x0, y0 + r), (x0, y1)], fill=GOLD, width=width * S)
    d.line([(x1, y0 + r), (x1, y1)], fill=GOLD, width=width * S)
    d.line([(x0, y1), (x1, y1)], fill=GOLD, width=width * S)


def grade(art, size):
    """Подгоняет иллюстрацию под палитру колоды: кадрирование, лёгкий сине-фиолетовый тон и виньетка."""
    art = ImageOps.fit(art.convert("RGB"), size, Image.LANCZOS, centering=(0.5, 0.4))
    art = ImageEnhance.Color(art).enhance(0.9)
    tone = Image.new("RGB", size, (40, 28, 90))
    art = Image.blend(art, Image.composite(art, tone, ImageOps.grayscale(art)), 0.25)
    vignette = Image.new("L", size, 0)
    w, h = size
    ImageDraw.Draw(vignette).ellipse([-w * 0.15, -h * 0.05, w * 1.15, h * 1.1], fill=255)
    vignette = vignette.filter(ImageFilter.GaussianBlur(w // 8))
    return Image.composite(art, Image.new("RGB", size, (12, 10, 30)), vignette)


def ornament(d, cx, y, half):
    """Золотая линия с ромбом посередине — разделитель под названием."""
    d.line([(cx - half, y), (cx - 14 * S, y)], fill=GOLD, width=2 * S)
    d.line([(cx + 14 * S, y), (cx + half, y)], fill=GOLD, width=2 * S)
    r = 8 * S
    d.polygon([(cx, y - r), (cx + r, y), (cx, y + r), (cx - r, y)], fill=GOLD)


def make_card(i, card):
    img = background(i)
    d = ImageDraw.Draw(img)
    frame(d)
    x0, y0, x1, y1 = [v * S for v in WIN]
    art_path = next((p for p in (ART / f"{card['id']}.jpg", ART / f"{card['id']}.png") if p.exists()), None)
    if art_path:
        art = grade(Image.open(art_path), (int(x1 - x0), int(y1 - y0)))
        layer = Image.new("RGB", img.size)
        layer.paste(art, (int(x0), int(y0)))
        img.paste(layer, (0, 0), arch_mask(WIN))
        d = ImageDraw.Draw(img)
    else:
        cx, cy, R = (x0 + x1) / 2, y0 + (y1 - y0) * 0.55, (x1 - x0) * 0.36
        if card["suit"] is None:
            emblem_major(d, i, card["name"], cx, cy, R)
        else:
            emblem_minor(d, card, cx, cy, R)
    arch_outline(d, WIN, 0, 3)
    arch_outline(d, WIN, 14 * S, 1)
    numeral = card["numeral"] if card["suit"] is None else "✦"
    if card["suit"] is not None:
        star(d, W * S / 2, 112 * S, 22 * S, 9 * S, 4, 2, fill=GOLD)
    else:
        centered(d, numeral, 78 * S, 60)
    name = card["name"].upper()
    size = 62 if len(name) <= 14 else 50 if len(name) <= 18 else 42
    centered(d, name, 1232 * S + (62 - size) * S // 2, size)
    ornament(d, W * S / 2, 1352 * S, 150 * S)
    return img.resize((W, H), Image.LANCZOS)


def make_back():
    img = background(999)
    d = ImageDraw.Draw(img)
    frame(d)
    cx, cy = W * S / 2, H * S / 2
    for k, r in enumerate([0.36, 0.3, 0.12]):
        circle(d, cx, cy, W * S * r, 2 if k < 2 else 3)
    star(d, cx, cy, W * S * 0.28, W * S * 0.11, 8, 2)
    crescent(img, cx, cy, W * S * 0.08, W * S * 0.04)
    centered(d, "МИРА", H * S - 230 * S, 72)
    return img.resize((W, H), Image.LANCZOS)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    make_back().save(OUT / "back.png", optimize=True)
    only = sys.argv[1:]
    for i, card in enumerate(DECK):
        if only and card["id"] not in only:
            continue
        make_card(i, card).save(OUT / f"{card['id']}.png", optimize=True)
    print("deck ready:", OUT)
