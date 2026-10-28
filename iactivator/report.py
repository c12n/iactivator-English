"""PDF report about one iPhone: identity, battery, locks and the fault check, with date and time."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from fpdf import FPDF

from . import __version__
from .core import ACTIVATION_RU, app_data_dir

INK = (46, 48, 53)
MUTED = (134, 134, 139)
LINE = (229, 229, 234)
SOFT = (246, 246, 243)
STATUS = {"ok": ((31, 157, 85), "Норма"), "warn": ((199, 119, 0), "Внимание"),
          "bad": ((224, 38, 47), "Неисправность"), "info": ((134, 134, 139), "Инфо")}

# Regular / bold fonts with Cyrillic, per platform.
FONTS = [
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
]


def reports_dir() -> Path:
    d = app_data_dir() / "Отчёты"
    d.mkdir(exist_ok=True)
    return d


class _Pdf(FPDF):
    def __init__(self, stamp: str):
        super().__init__(format="A4")
        self.stamp = stamp
        for regular, bold in FONTS:
            if Path(regular).exists() and Path(bold).exists():
                self.add_font("ui", "", regular)
                self.add_font("ui", "B", bold)
                break
        else:
            raise RuntimeError("Не найден шрифт с кириллицей для PDF")
        self.set_margins(18, 16, 18)
        self.set_auto_page_break(True, 18)

    def footer(self):
        self.set_y(-13)
        self.set_font("ui", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 5, f"Создано в iActivator {__version__} · {self.stamp}", align="L")
        self.cell(0, 5, f"стр. {self.page_no()}", align="R")


def _logo(pdf: FPDF, x: float, y: float, size: float) -> None:
    """The app logo (assets/logo.svg) drawn with vectors; 84 units = `size` mm."""
    k = size / 84
    pdf.set_fill_color(*SOFT)
    pdf.set_draw_color(226, 226, 220)
    pdf.set_line_width(0.2)
    pdf.rect(x, y, size, size, style="DF", round_corners=True, corner_radius=20 * k)
    pdf.set_fill_color(*INK)
    pdf.rect(x + 27 * k, y + 10 * k, 30 * k, 50 * k, style="F", round_corners=True, corner_radius=8 * k)
    pdf.set_fill_color(*SOFT)
    pdf.rect(x + 31 * k, y + 15 * k, 22 * k, 38 * k, style="F", round_corners=True, corner_radius=4 * k)
    pdf.set_fill_color(*INK)
    pdf.polygon([(x + a * k, y + b * k) for a, b in ((38, 30), (42, 24), (42, 32), (46, 32), (42, 40), (42, 34))], style="F")
    pdf.set_draw_color(154, 163, 173)
    pdf.set_line_width(4 * k)
    pdf.line(x + 42 * k, y + 61 * k, x + 42 * k, y + 73 * k)


def _section(pdf: FPDF, title: str) -> None:
    pdf.ln(4)
    pdf.set_font("ui", "B", 11)
    pdf.set_text_color(*INK)
    pdf.cell(0, 7, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*LINE)
    pdf.set_line_width(0.3)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(1.5)


def _pairs(pdf: FPDF, rows: list) -> None:
    """Two columns of label/value pairs."""
    col = (pdf.w - pdf.l_margin - pdf.r_margin) / 2
    for i in range(0, len(rows), 2):
        y = pdf.get_y()
        for j, (k, v) in enumerate(rows[i:i + 2]):
            pdf.set_xy(pdf.l_margin + j * col, y)
            pdf.set_font("ui", "", 8.5)
            pdf.set_text_color(*MUTED)
            pdf.cell(col, 4.5, k, new_x="LEFT", new_y="NEXT")
            pdf.set_font("ui", "B", 10.5)
            pdf.set_text_color(*INK)
            pdf.cell(col, 5.5, str(v if v not in (None, "") else "—"))
        pdf.set_xy(pdf.l_margin, y + 12)


def _checks(pdf: FPDF, results: list) -> None:
    width = pdf.w - pdf.l_margin - pdf.r_margin
    for r in results:
        color, word = STATUS.get(r["status"], STATUS["info"])
        if pdf.get_y() > pdf.h - 30:
            pdf.add_page()
        y = pdf.get_y() + 1.5
        pdf.set_fill_color(*color)
        pdf.ellipse(pdf.l_margin, y + 1, 2.6, 2.6, style="F")
        pdf.set_xy(pdf.l_margin + 5, y)
        pdf.set_font("ui", "B", 10)
        pdf.set_text_color(*INK)
        pdf.cell(width - 35, 4.8, r["name"])
        pdf.set_font("ui", "", 8.5)
        pdf.set_text_color(*color)
        pdf.cell(30, 4.8, word, align="R", new_x="LMARGIN", new_y="NEXT")
        if r.get("detail"):
            pdf.set_x(pdf.l_margin + 5)
            pdf.set_font("ui", "", 9)
            pdf.set_text_color(*MUTED)
            pdf.multi_cell(width - 5, 4.4, r["detail"], new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1.2)


def build_pdf(info: dict, results: list) -> Path:
    now = datetime.now()
    stamp = now.strftime("%d.%m.%Y, %H:%M")
    pdf = _Pdf(stamp)
    pdf.add_page()

    # Header: logo, title, date and time
    _logo(pdf, pdf.l_margin, 14, 14)
    pdf.set_xy(pdf.l_margin + 18, 14.5)
    pdf.set_font("ui", "B", 15)
    pdf.set_text_color(*INK)
    pdf.cell(90, 7, "Отчёт о проверке iPhone", new_x="LEFT", new_y="NEXT")
    pdf.set_font("ui", "", 9)
    pdf.set_text_color(*MUTED)
    pdf.cell(90, 5, "iActivator")
    pdf.set_xy(pdf.w - pdf.r_margin - 60, 14.5)
    pdf.set_font("ui", "", 8.5)
    pdf.cell(60, 5, "Дата и время проверки", align="R", new_x="LEFT", new_y="NEXT")
    pdf.set_font("ui", "B", 11)
    pdf.set_text_color(*INK)
    pdf.cell(60, 6, stamp, align="R")
    pdf.set_y(34)

    # Device
    title = info.get("model_name") or info.get("model") or "iPhone"
    if info.get("capacity"):
        title += f", {info['capacity']}"
    pdf.set_font("ui", "B", 20)
    pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")

    bad = sum(r["status"] == "bad" for r in results)
    warn = sum(r["status"] == "warn" for r in results)
    color, verdict = ((224, 38, 47), f"Найдены неисправности: {bad}") if bad else \
                     ((199, 119, 0), f"Есть замечания: {warn}") if warn else ((31, 157, 85), "Неисправностей не найдено")
    pdf.set_font("ui", "B", 11)
    pdf.set_text_color(*color)
    pdf.cell(0, 6, verdict, new_x="LMARGIN", new_y="NEXT")

    _section(pdf, "Устройство")
    rows = [("Серийный номер", info.get("serial")), ("IMEI", info.get("imei"))]
    if info.get("imei2"):
        rows.append(("IMEI 2", info["imei2"]))
    rows += [("Модель", f"{info.get('model_name')} ({info.get('model')})"),
             ("iOS", f"{info.get('ios')} ({info.get('build')})" if info.get("ios") else None),
             ("Память", info.get("capacity")),
             ("Активация", str(ACTIVATION_RU.get(info.get("activation"), info.get("activation") or "—")).capitalize()),
             ("Модем", info.get("baseband"))]
    _pairs(pdf, rows)

    b = info.get("battery") or {}
    if b:
        _section(pdf, "Батарея")
        brows = []
        if b.get("health") is not None:
            brows.append(("Ёмкость (здоровье)", f"{b['health']}%"))
        if b.get("full_mah"):
            brows.append(("Текущая / заводская", f"{b['full_mah']} / {b.get('design_mah')} мАч"))
        if b.get("cycles") is not None:
            brows.append(("Циклы зарядки", b["cycles"]))
        if b.get("level") is not None:
            brows.append(("Заряд сейчас", f"{b['level']}%"))
        _pairs(pdf, brows)

    _section(pdf, "Проверка")
    _checks(pdf, results)

    safe = "".join(c for c in f"{title}_{info.get('serial') or ''}" if c.isalnum() or c in " _-,").strip()
    path = reports_dir() / f"{safe}_{now.strftime('%Y-%m-%d_%H-%M')}.pdf"
    pdf.output(str(path))
    return path


def open_file(path: Path) -> None:
    import subprocess
    if sys.platform == "win32":
        import os
        os.startfile(str(path))
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
