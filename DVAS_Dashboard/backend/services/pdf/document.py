"""
Report PDF engine (evidence plan R3): fpdf2 with Noto Sans + Noto Sans Malayalam
(OFL, assets/fonts) and HarfBuzz text shaping, shared page furniture, tables
that never truncate silently, evidence image grids, bar charts and maps.
"""
import io
import logging
import os
from datetime import datetime, timezone
from typing import Callable, List, Optional, Sequence

from fpdf import FPDF
from fpdf.fonts import FontFace

from core.timeutil import fmt_ist

logger = logging.getLogger("dvas.pdf")

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        "assets", "fonts")
CLASSIFICATION = "CONFIDENTIAL — FOR OFFICIAL USE ONLY · KERALA POLICE"
INLINE_ROWS = 25                 # rows shown in the body; the rest goes to an annex

SEVERITY_RGB = {"critical": (190, 30, 45), "high": (220, 110, 20), "medium": (200, 160, 0),
                "low": (60, 130, 200)}


class ReportPDF(FPDF):
    def __init__(self, title: str, subtitle: str = "", report_id: str = "", watermark: str = ""):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.report_title, self.subtitle, self.report_id, self.watermark = title, subtitle, report_id, watermark
        self.generated = fmt_ist(datetime.now(timezone.utc))
        self._annexes: List[tuple] = []
        self.set_auto_page_break(True, margin=18)
        self.set_margins(14, 14, 14)
        self.add_font("Noto", "", os.path.join(FONT_DIR, "NotoSans-Regular.ttf"))
        self.add_font("Noto", "B", os.path.join(FONT_DIR, "NotoSans-Bold.ttf"))
        self.add_font("NotoMal", "", os.path.join(FONT_DIR, "NotoSansMalayalam-Regular.ttf"))
        self.add_font("NotoMal", "B", os.path.join(FONT_DIR, "NotoSansMalayalam-Bold.ttf"))
        self.set_fallback_fonts(["NotoMal"], exact_match=False)
        try:
            self.set_text_shaping(True)          # needs uharfbuzz: correct Malayalam conjuncts
        except Exception as exc:                  # pragma: no cover — dependency missing
            logger.warning("Text shaping unavailable (%s); Malayalam may render unshaped", exc)
        self.set_title(title)
        self.set_creator("DVAS")
        self.add_page()

    # ── page furniture ──
    def header(self):
        if self.watermark:
            with self.local_context(text_color=(225, 225, 225)):
                self.set_font("Noto", "B", 30)
                with self.rotation(35, x=105, y=150):
                    self.text(25, 160, self.watermark[:60])
        self.set_fill_color(25, 35, 60)
        self.rect(0, 0, 210, 7, style="F")
        self.set_xy(0, 1.2)
        self.set_font("Noto", "B", 7)
        self.set_text_color(255, 255, 255)
        self.cell(210, 4.5, CLASSIFICATION, align="C")
        self.set_text_color(0, 0, 0)
        self.set_xy(14, 10)
        self.set_font("Noto", "B", 13)
        self.cell(0, 6, self.report_title, new_x="LMARGIN", new_y="NEXT")
        self.set_font("Noto", "", 8)
        self.set_text_color(90, 90, 90)
        line = " · ".join(x for x in (self.subtitle, f"Generated {self.generated}") if x)
        self.cell(0, 4.5, line, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.set_draw_color(200, 200, 200)
        self.line(14, self.get_y() + 1, 196, self.get_y() + 1)
        self.ln(4)

    def footer(self):
        self.set_y(-12)
        self.set_font("Noto", "", 7)
        self.set_text_color(110, 110, 110)
        self.cell(0, 4, f"{self.report_id}  ·  Times in IST (UTC+05:30)  ·  {CLASSIFICATION}", align="L")
        self.set_x(14)
        self.cell(0, 4, f"Page {self.page_no()} of {{nb}}", align="R")
        self.set_text_color(0, 0, 0)

    # ── text ──
    def h1(self, text: str):
        if self.get_y() > 250:
            self.add_page()
        self.ln(2)
        self.set_font("Noto", "B", 11)
        self.set_text_color(25, 35, 60)
        self.multi_cell(0, 6, text, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(1)

    def para(self, text: str, size: float = 8.5, italic_note: bool = False):
        self.set_font("Noto", "", size)
        if italic_note:
            self.set_text_color(100, 100, 100)
        self.multi_cell(0, 4.5, text, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(1)

    def kv(self, pairs: Sequence[tuple], key_w: float = 45):
        for k, v in pairs:
            self.set_font("Noto", "B", 8)
            self.cell(key_w, 5, str(k))
            self.set_font("Noto", "", 8)
            self.multi_cell(0, 5, "—" if v in (None, "") else str(v), new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def stat_tiles(self, tiles: Sequence[tuple]):
        """[(label, value)] in boxes across the page."""
        if not tiles:
            return
        w = 182 / len(tiles)
        y = self.get_y()
        for i, (label, value) in enumerate(tiles):
            x = 14 + i * w
            self.set_draw_color(210, 210, 210)
            self.rect(x, y, w - 2, 14)
            self.set_xy(x + 2, y + 1.5)
            self.set_font("Noto", "B", 12)
            self.cell(w - 6, 6, "—" if value is None else str(value))
            self.set_xy(x + 2, y + 8)
            self.set_font("Noto", "", 6.5)
            self.set_text_color(90, 90, 90)
            self.cell(w - 6, 4, label)
            self.set_text_color(0, 0, 0)
        self.set_xy(14, y + 17)

    # ── tables ──
    def data_table(self, title: str, columns: Sequence[tuple], rows: Sequence[dict],
                   empty: str = "None in this period.", inline: int = INLINE_ROWS):
        """columns: [(header, key_or_fn, width_mm)]. More than `inline` rows → the rest in an annex."""
        self.h1(title)
        if not rows:
            self.para(empty, italic_note=True)
            return
        shown = rows[:inline]
        self._table(columns, shown)
        if len(rows) > inline:
            annex_no = len(self._annexes) + 1
            self._annexes.append((f"Annex {annex_no}: {title}", columns, rows))
            self.para(f"Showing {len(shown)} of {len(rows)} — full list in Annex {annex_no}.", 7.5, italic_note=True)
        self.ln(1)

    def _table(self, columns, rows):
        self.set_font("Noto", "", 7)
        widths = [c[2] for c in columns]
        with self.table(col_widths=widths, width=sum(widths), align="LEFT", line_height=4,
                        headings_style=FontFace(emphasis="BOLD", fill_color=(235, 238, 245)),
                        first_row_as_headings=True) as table:
            head = table.row()
            for c in columns:
                head.cell(c[0])
            for r in rows:
                row = table.row()
                for c in columns:
                    val = c[1](r) if callable(c[1]) else r.get(c[1])
                    row.cell("—" if val in (None, "") else str(val))

    def render_annexes(self):
        for title, columns, rows in self._annexes:
            self.add_page()
            self.h1(title)
            self._table(columns, rows)

    # ── charts ──
    def bar_chart(self, title: str, items: Sequence[tuple], unit: str = "", max_bars: int = 15):
        """Horizontal bars: [(label, value)]."""
        self.h1(title)
        items = [(str(k), float(v or 0)) for k, v in items][:max_bars]
        if not items:
            self.para("No data.", italic_note=True)
            return
        top = max(v for _, v in items) or 1
        for label, value in items:
            if self.get_y() > 270:
                self.add_page()
            y = self.get_y()
            self.set_font("Noto", "", 7)
            self.set_xy(14, y)
            self.cell(48, 4.5, label[:40])
            self.set_fill_color(70, 110, 170)
            self.rect(63, y + 0.8, 110 * value / top, 3, style="F")
            self.set_xy(175, y)
            self.cell(21, 4.5, f"{value:g}{unit}", align="R")
            self.set_xy(14, y + 4.8)
        self.ln(2)

    # ── images ──
    def image_grid(self, title: str, items: Sequence[tuple], per_row: int = 2, note: str = ""):
        """items: [(image_bytes_or_None, caption)]. Unreadable images show their caption only."""
        self.h1(title)
        if note:
            self.para(note, 7.5, italic_note=True)
        if not items:
            self.para("No images.", italic_note=True)
            return
        w = (182 - (per_row - 1) * 4) / per_row
        h = w * 0.62
        col = 0
        for data, caption in items:
            if col == 0 and self.get_y() + h + 12 > 280:
                self.add_page()
            x = 14 + col * (w + 4)
            y = self.get_y()
            self.set_draw_color(210, 210, 210)
            self.rect(x, y, w, h)
            if data:
                try:
                    self.image(io.BytesIO(data), x=x + 0.5, y=y + 0.5, w=w - 1, h=h - 1, keep_aspect_ratio=True)
                except Exception:
                    self.set_xy(x, y + h / 2 - 2)
                    self.set_font("Noto", "", 7)
                    self.cell(w, 4, "image could not be rendered", align="C")
            else:
                self.set_xy(x, y + h / 2 - 2)
                self.set_font("Noto", "", 7)
                self.cell(w, 4, "image unavailable", align="C")
            self.set_xy(x, y + h + 0.5)
            self.set_font("Noto", "", 6.5)
            self.multi_cell(w, 3.3, caption, new_x="RIGHT", new_y="TOP")
            col += 1
            if col == per_row:
                col = 0
                self.set_xy(14, y + h + 9)
        if col:
            self.set_xy(14, self.get_y() + h + 9)
        self.ln(1)

    def map_image(self, title: str, png: Optional[bytes], caption: str):
        self.h1(title)
        if not png:
            self.para("No located events in this report.", italic_note=True)
            return
        if self.get_y() > 160:
            self.add_page()
        self.image(io.BytesIO(png), x=14, w=182)
        self.para(caption, 7, italic_note=True)

    def output_bytes(self) -> bytes:
        self.render_annexes()
        return bytes(self.output())


def safe(fn: Callable, *a, default=None):
    try:
        return fn(*a)
    except Exception:
        return default
