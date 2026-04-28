"""Mini-graph для sensor history через PIL.

Рендерит компактный sparkline-стиль график (800x300) с min/max/avg/current
и временными метками. Тёмная тема.
"""

import io
from datetime import datetime
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

# Цвета — тёмная тема в стиле Telegram
BG = (30, 30, 46)         # #1e1e2e
GRID = (52, 52, 70)
LINE = (137, 180, 250)    # #89b4fa (синий)
FILL = (137, 180, 250, 50)
TEXT = (205, 214, 244)    # #cdd6f4
ACCENT = (166, 227, 161)  # #a6e3a1 (зелёный)
WARN = (250, 179, 135)    # #fab387 (оранжевый)


def _try_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Стараемся найти TTF шрифт в системе. Иначе — bitmap default."""
    candidates = [
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/lib/python3/dist-packages/PIL/fonts/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def render_sparkline(
    points: Sequence[tuple[float, float]],
    title: str,
    unit: str = "",
    period_label: str = "24ч",
    width: int = 800,
    height: int = 320,
) -> bytes:
    """Отрендерить sparkline по точкам (timestamp, value) → PNG bytes."""
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img, "RGBA")

    f_title = _try_font(20)
    f_label = _try_font(14)
    f_value = _try_font(28)
    f_small = _try_font(12)

    # Заголовок
    draw.text((20, 14), title, font=f_title, fill=TEXT)
    draw.text((20, 40), f"за {period_label}", font=f_small, fill=GRID)

    if not points or len(points) < 2:
        msg = "Недостаточно данных за период" if not points else "Только одна точка за период"
        draw.text((20, height // 2), msg, font=f_label, fill=WARN)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    values = [v for _, v in points]
    times = [t for t, _ in points]
    v_min = min(values)
    v_max = max(values)
    v_cur = values[-1]
    v_avg = sum(values) / len(values)

    # Зона графика
    plot_x0, plot_y0 = 60, 80
    plot_x1, plot_y1 = width - 20, height - 50
    plot_w = plot_x1 - plot_x0
    plot_h = plot_y1 - plot_y0

    # Сетка по Y (5 линий)
    for i in range(5):
        y = plot_y0 + plot_h * i / 4
        draw.line([(plot_x0, y), (plot_x1, y)], fill=GRID, width=1)

    # Лейблы Y (min/max и среднее)
    span = v_max - v_min if v_max != v_min else 1
    for i in range(5):
        y = plot_y1 - plot_h * i / 4
        v = v_min + span * i / 4
        draw.text((10, y - 7), _fmt_num(v), font=f_small, fill=TEXT)

    # Точки → координаты
    t_min, t_max = times[0], times[-1]
    t_span = t_max - t_min if t_max != t_min else 1

    coords: list[tuple[float, float]] = []
    for t, v in points:
        x = plot_x0 + plot_w * (t - t_min) / t_span
        y = plot_y1 - plot_h * (v - v_min) / span
        coords.append((x, y))

    # Заполнение области под кривой
    poly = [(plot_x0, plot_y1), *coords, (plot_x1, plot_y1)]
    draw.polygon(poly, fill=FILL)

    # Линия графика
    if len(coords) >= 2:
        draw.line(coords, fill=LINE, width=2, joint="curve")

    # Точки на концах
    cx, cy = coords[-1]
    draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=ACCENT)

    # Метки времени по X (4 точки)
    for i in range(4):
        x = plot_x0 + plot_w * i / 3
        t = t_min + t_span * i / 3
        label = datetime.fromtimestamp(t).strftime("%H:%M" if t_span < 86400 * 2 else "%d.%m")
        bbox = draw.textbbox((0, 0), label, font=f_small)
        tw = bbox[2] - bbox[0]
        draw.text((x - tw / 2, plot_y1 + 6), label, font=f_small, fill=TEXT)

    # Текущее значение справа
    cur_text = f"{_fmt_num(v_cur)}{unit}"
    bbox = draw.textbbox((0, 0), cur_text, font=f_value)
    tw = bbox[2] - bbox[0]
    draw.text((width - tw - 20, 12), cur_text, font=f_value, fill=ACCENT)

    # Min/Max/Avg внизу
    stats = f"мин {_fmt_num(v_min)}  макс {_fmt_num(v_max)}  ср {_fmt_num(v_avg)}{unit}"
    draw.text((20, height - 22), stats, font=f_small, fill=GRID)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _fmt_num(v: float) -> str:
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}".rstrip("0").rstrip(".")
