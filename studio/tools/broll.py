"""Видео-«воспоминание» за картой: чёрно-белый стоковый клип, полупрозрачный,
растворяется к краям радиальным градиентом, без видимых границ.
Главный объект клипа ставится в просвет рядом с картой (карта reveal занимает x 305–775, y 350–1133).

В сцене JSON (обычно reveal):
  "broll": {"src": "assets/broll/mixkit_25251.mp4", "start": 2, "speed": 0.8,
            "height": 760, "focus": 0.33, "at": 170, "fy": 0.4, "sy": 640,
            "rx": 330, "ry": 420, "opacity": 0.8}
  src      — путь к клипу (от корня репозитория)
  start    — с какой секунды исходника брать
  speed    — скорость (<1 — замедление); если клипа не хватает на сцену, замедляется сильнее,
             последний кадр никогда не «замирает»
  height   — высота клипа в кадре, px
  focus/fy — точка главного объекта в исходнике (доли ширины/высоты)
  at/sy    — куда эта точка встаёт в кадре, px
  rx/ry    — радиусы видимого пятна вокруг объекта; дальше клип растворяется в ноль
  opacity  — сила проявления (смешивание «экран»: светлое проступает, тёмное исчезает)
"""
import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

W, H = 1080, 1920
S = 2  # слой считается в половинном разрешении — мягче и быстрее
LW, LH = W // S, H // S
FPS = 30


def _probe(src):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:format=duration", "-of", "json", str(src)],
                         capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    st = j["streams"][0]
    return st["width"], st["height"], float(j["format"]["duration"])


def _smooth(x):
    x = np.clip(x, 0, 1)
    return x * x * (3 - 2 * x)


class Broll:
    def __init__(self, cfg, root, dur):
        src = Path(root) / cfg["src"]
        sw, sh, sdur = _probe(src)
        start = float(cfg.get("start", 0.0))
        speed = float(cfg.get("speed", 0.8))
        avail = max(0.5, sdur - start - 0.1)
        speed = min(speed, avail / dur)  # не хватает исходника — замедляем, а не стопорим
        self.opacity = float(cfg.get("opacity", 0.8))
        self.dur = dur

        vh = int(cfg.get("height", 900)) // S
        vw = int(round(sw * vh / sh))
        at, sy = float(cfg.get("at", W / 2)) / S, float(cfg.get("sy", 740)) / S
        x0 = int(round(at - float(cfg.get("focus", 0.5)) * vw))
        y0 = int(round(sy - float(cfg.get("fy", 0.5)) * vh))
        # видимая часть клипа в пределах кадра
        vx, vy = max(0, x0), max(0, y0)
        vx2, vy2 = min(LW, x0 + vw), min(LH, y0 + vh)
        cw, ch, cx, cy = vx2 - vx, vy2 - vy, vx - x0, vy - y0
        self.box = (vx, vy, cw, ch)

        n = int(dur * FPS) + 2
        vf = (f"setpts=(PTS-STARTPTS)/{speed},fps={FPS},scale={vw}:{vh}:flags=bicubic,"
              f"crop={cw}:{ch}:{cx}:{cy},format=gray")
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start), "-i", str(src), "-an", "-vf", vf,
                              "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                             capture_output=True, check=True).stdout
        self.frames = np.frombuffer(raw, np.uint8).reshape(-1, ch, cw)

        # пятно вокруг объекта × мягкие края самого клипа → нигде нет резких границ
        rx, ry = float(cfg.get("rx", 380)) / S, float(cfg.get("ry", 460)) / S
        yy, xx = np.mgrid[vy:vy2, vx:vx2].astype(np.float32)
        d = np.sqrt(((xx - at) / rx) ** 2 + ((yy - sy) / ry) ** 2)
        m = _smooth((1.0 - d) / 0.6)
        f = 40.0 / S
        edge = _smooth(np.minimum(xx - x0, x0 + vw - xx) / f) * _smooth(np.minimum(yy - y0, y0 + vh - yy) / f)
        self.mask = (m * edge).astype(np.float32)

    def layer(self, t):
        """Слой яркости (W×H, L) для смешивания «экран», уже с прозрачностью и затуханием."""
        i = min(len(self.frames) - 1, max(0, int(t * FPS)))
        g = self.frames[i].astype(np.float32) / 255.0
        g = np.clip((g - 0.06) * 1.25, 0, 1)  # чуть контраста, чтобы силуэт читался
        fade = min(1.0, t / 0.7, max(0.0, (self.dur - t) / 0.45))
        g = g * self.mask * (self.opacity * fade)
        vx, vy, cw, ch = self.box
        canvas = np.zeros((LH, LW), np.float32)
        canvas[vy:vy + ch, vx:vx + cw] = g
        return Image.fromarray((canvas * 255).astype(np.uint8), "L").resize((W, H), Image.BILINEAR)


def screen(frame, lum):
    """frame (RGBA) ⊕ серый слой в режиме «экран» — чёрно-белое проступает поверх тёмного фона."""
    a = np.asarray(frame, np.float32) / 255.0
    l = np.asarray(lum, np.float32)[..., None] / 255.0
    a[..., :3] = 1 - (1 - a[..., :3]) * (1 - l)
    return Image.fromarray((a * 255 + 0.5).astype(np.uint8), "RGBA")
