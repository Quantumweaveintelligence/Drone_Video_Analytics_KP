"""
DVAS Progress Presentation Generator
Generates a 30-slide PowerPoint presentation for the Drone Video Analytics System
Kerala Police Department × Quantumweave Intelligence
"""

import sys
sys.stdout.reconfigure(encoding='utf-8')

from pptx import Presentation
from pptx.util import Inches, Pt, Emu, Cm
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.enum.dml import MSO_THEME_COLOR
from pptx.oxml.ns import qn
from pptx.util import Pt
import copy
from lxml import etree

# ── Color Palette (Kerala Police / DVAS Brand) ──────────────────────────────
# Dark navy background like the template
BG_DARK       = RGBColor(0x0A, 0x0E, 0x2A)   # Deep navy
BG_CARD       = RGBColor(0x0D, 0x1B, 0x4B)   # Card navy
ACCENT_BLUE   = RGBColor(0x00, 0x9B, 0xFF)   # Electric blue
ACCENT_CYAN   = RGBColor(0x00, 0xE5, 0xFF)   # Cyan
ACCENT_GREEN  = RGBColor(0x00, 0xE6, 0x76)   # Success green
ACCENT_GOLD   = RGBColor(0xFF, 0xB3, 0x00)   # Kerala gold
ACCENT_RED    = RGBColor(0xFF, 0x3D, 0x3D)   # Alert red
ACCENT_PURPLE = RGBColor(0x8A, 0x2B, 0xE2)   # Purple
WHITE         = RGBColor(0xFF, 0xFF, 0xFF)
LIGHT_GRAY    = RGBColor(0xCC, 0xD6, 0xF1)
MID_GRAY      = RGBColor(0x8A, 0x95, 0xB4)
GRID_LINE     = RGBColor(0x1E, 0x30, 0x6E)

# ── Slide dimensions: widescreen 16:9 ──────────────────────────────────────
W = Inches(13.33)
H = Inches(7.5)

prs = Presentation()
prs.slide_width  = W
prs.slide_height = H

BLANK_LAYOUT = prs.slide_layouts[6]  # Blank layout

# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def add_rect(slide, x, y, w, h, fill_color=None, line_color=None, line_width=0):
    """Add a filled rectangle shape."""
    shape = slide.shapes.add_shape(
        1,  # MSO_SHAPE_TYPE.RECTANGLE
        x, y, w, h
    )
    fill = shape.fill
    if fill_color:
        fill.solid()
        fill.fore_color.rgb = fill_color
    else:
        fill.background()
    
    line = shape.line
    if line_color:
        line.color.rgb = line_color
        line.width = Pt(line_width)
    else:
        line.fill.background()
    return shape


def set_bg(slide, color):
    """Set slide background color."""
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = color


def add_text(slide, text, x, y, w, h, font_size=18, bold=False,
             color=WHITE, align=PP_ALIGN.LEFT, font_name="Calibri",
             italic=False, wrap=True):
    """Add a textbox with given properties."""
    txBox = slide.shapes.add_textbox(x, y, w, h)
    tf = txBox.text_frame
    tf.word_wrap = wrap
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.size = Pt(font_size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = color
    run.font.name = font_name
    return txBox


def add_multiline_text(slide, lines, x, y, w, h, base_size=16, color=WHITE,
                        font_name="Calibri", line_spacing=1.2):
    """Add textbox with multiple paragraphs."""
    txBox = slide.shapes.add_textbox(x, y, w, h)
    tf = txBox.text_frame
    tf.word_wrap = True
    
    first = True
    for line_data in lines:
        if first:
            p = tf.paragraphs[0]
            first = False
        else:
            p = tf.add_paragraph()
        
        if isinstance(line_data, dict):
            text = line_data.get('text', '')
            size = line_data.get('size', base_size)
            bold = line_data.get('bold', False)
            clr  = line_data.get('color', color)
            align = line_data.get('align', PP_ALIGN.LEFT)
            italic = line_data.get('italic', False)
        else:
            text = line_data
            size = base_size
            bold = False
            clr  = color
            align = PP_ALIGN.LEFT
            italic = False
        
        p.alignment = align
        run = p.add_run()
        run.text = text
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.italic = italic
        run.font.color.rgb = clr
        run.font.name = font_name
    
    return txBox


def add_header_bar(slide, title, subtitle=None):
    """Add standard slide header with dark bar."""
    # Top accent bar
    add_rect(slide, 0, 0, W, Inches(0.08), fill_color=ACCENT_BLUE)
    
    # Header background
    add_rect(slide, 0, Inches(0.08), W, Inches(1.1), fill_color=BG_CARD)
    
    # Title text
    add_text(slide, title, Inches(0.4), Inches(0.12), Inches(11), Inches(0.7),
             font_size=28, bold=True, color=WHITE, align=PP_ALIGN.LEFT)
    
    if subtitle:
        add_text(slide, subtitle, Inches(0.4), Inches(0.82), Inches(11), Inches(0.32),
                 font_size=13, bold=False, color=ACCENT_CYAN, align=PP_ALIGN.LEFT)
    
    # Logo area on right
    add_text(slide, "DVAS", Inches(11.8), Inches(0.12), Inches(1.3), Inches(0.5),
             font_size=22, bold=True, color=ACCENT_GOLD, align=PP_ALIGN.RIGHT)
    add_text(slide, "Kerala Police", Inches(11.4), Inches(0.62), Inches(1.7), Inches(0.3),
             font_size=10, color=LIGHT_GRAY, align=PP_ALIGN.RIGHT)
    
    # Separator line
    add_rect(slide, Inches(0.3), Inches(1.18), W - Inches(0.6), Pt(1.5),
             fill_color=ACCENT_BLUE)


def add_footer(slide, slide_num, total=30):
    """Add footer with slide number and branding."""
    add_rect(slide, 0, H - Inches(0.35), W, Inches(0.35), fill_color=BG_CARD)
    add_rect(slide, 0, H - Inches(0.35), W, Pt(1), fill_color=ACCENT_BLUE)
    
    add_text(slide, "Quantumweave Intelligence × Kerala Police Department",
             Inches(0.3), H - Inches(0.32), Inches(7), Inches(0.3),
             font_size=9, color=MID_GRAY, align=PP_ALIGN.LEFT)
    add_text(slide, f"{slide_num} / {total}",
             Inches(10), H - Inches(0.32), Inches(3), Inches(0.3),
             font_size=9, color=MID_GRAY, align=PP_ALIGN.RIGHT)
    add_text(slide, "CONFIDENTIAL — RESTRICTED",
             Inches(5), H - Inches(0.32), Inches(3), Inches(0.3),
             font_size=9, color=ACCENT_RED, align=PP_ALIGN.CENTER)


def bullet_card(slide, x, y, w, h, title, bullets, title_color=ACCENT_CYAN,
                icon=None, card_color=BG_CARD):
    """Draw a card with title and bullet list."""
    add_rect(slide, x, y, w, h, fill_color=card_color, line_color=ACCENT_BLUE, line_width=0.5)
    
    # Card top accent
    add_rect(slide, x, y, w, Inches(0.04), fill_color=title_color)
    
    ty = y + Inches(0.08)
    prefix = f"{icon}  " if icon else ""
    add_text(slide, prefix + title, x + Inches(0.12), ty, w - Inches(0.24), Inches(0.36),
             font_size=13, bold=True, color=title_color)
    
    by = ty + Inches(0.38)
    bh = h - Inches(0.5)
    lines = []
    for b in bullets:
        lines.append({'text': f"▸  {b}", 'size': 11, 'color': LIGHT_GRAY})
    add_multiline_text(slide, lines, x + Inches(0.14), by, w - Inches(0.28), bh)


def status_badge(slide, x, y, label, status, color):
    """Draw a status indicator badge."""
    add_rect(slide, x, y, Inches(1.4), Inches(0.32), fill_color=color,
             line_color=None)
    add_text(slide, label, x, y, Inches(1.4), Inches(0.32),
             font_size=9, bold=True, color=WHITE, align=PP_ALIGN.CENTER)
    

def progress_bar(slide, x, y, w, h, pct, bar_color=ACCENT_BLUE, bg_color=GRID_LINE,
                 label=""):
    """Draw a progress bar."""
    add_rect(slide, x, y, w, h, fill_color=bg_color)
    if pct > 0:
        add_rect(slide, x, y, int(w * pct / 100), h, fill_color=bar_color)
    if label:
        add_text(slide, label, x + w + Inches(0.1), y - Pt(2), Inches(1.2), h + Pt(4),
                 font_size=10, color=LIGHT_GRAY)


# ═══════════════════════════════════════════════════════════════════════════════
# SLIDE CONTENT
# ═══════════════════════════════════════════════════════════════════════════════

# ── SLIDE 1: Title / Cover ─────────────────────────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)

# Top thin accent
add_rect(slide, 0, 0, W, Inches(0.06), fill_color=ACCENT_BLUE)

# Diagonal hero gradient rect (simulated with overlapping rects)
add_rect(slide, 0, 0, W, H, fill_color=BG_DARK)
add_rect(slide, 0, Inches(1.5), Inches(6), H - Inches(1.5), fill_color=BG_CARD)

# Gold accent stripe
add_rect(slide, 0, Inches(1.5), Inches(0.06), H - Inches(1.5), fill_color=ACCENT_GOLD)

# Kerala Police emblem area (text placeholder)
add_rect(slide, Inches(0.4), Inches(1.8), Inches(1.5), Inches(1.5),
         fill_color=RGBColor(0x00, 0x3A, 0x7D), line_color=ACCENT_GOLD, line_width=1.5)
add_text(slide, "KP", Inches(0.4), Inches(1.9), Inches(1.5), Inches(1.2),
         font_size=40, bold=True, color=ACCENT_GOLD, align=PP_ALIGN.CENTER)
add_text(slide, "POLICE", Inches(0.4), Inches(2.9), Inches(1.5), Inches(0.3),
         font_size=8, bold=True, color=ACCENT_GOLD, align=PP_ALIGN.CENTER)

# Main title
add_text(slide, "DRONE VIDEO", Inches(2.2), Inches(1.65), Inches(8), Inches(0.8),
         font_size=48, bold=True, color=WHITE, align=PP_ALIGN.LEFT)
add_text(slide, "ANALYTICS SYSTEM", Inches(2.2), Inches(2.35), Inches(8.5), Inches(0.8),
         font_size=48, bold=True, color=ACCENT_BLUE, align=PP_ALIGN.LEFT)
add_text(slide, "DVAS — PROJECT PROGRESS PRESENTATION", Inches(2.2), Inches(3.1),
         Inches(9), Inches(0.45), font_size=18, bold=False, color=ACCENT_CYAN,
         align=PP_ALIGN.LEFT)

# Subtitle
add_rect(slide, Inches(2.2), Inches(3.65), Inches(0.06), Inches(1.1), fill_color=ACCENT_GOLD)
add_text(slide, "Real-Time Drone Surveillance & Intelligence Platform",
         Inches(2.5), Inches(3.7), Inches(8), Inches(0.42),
         font_size=15, color=LIGHT_GRAY, align=PP_ALIGN.LEFT)
add_text(slide, "Phase 1 & Phase 2 — Modules 1–5 Complete",
         Inches(2.5), Inches(4.12), Inches(8), Inches(0.38),
         font_size=15, bold=True, color=ACCENT_GREEN, align=PP_ALIGN.LEFT)
add_text(slide, "22-Month Production Roadmap | 2-Engineer Team | Kerala Police Department",
         Inches(2.5), Inches(4.5), Inches(8), Inches(0.35),
         font_size=12, color=MID_GRAY, align=PP_ALIGN.LEFT)

# Bottom info strip
add_rect(slide, 0, H - Inches(1.2), W, Inches(1.2), fill_color=BG_CARD)
add_rect(slide, 0, H - Inches(1.2), W, Pt(1.5), fill_color=ACCENT_BLUE)
add_text(slide, "Quantumweave Intelligence", Inches(0.4), H - Inches(1.1), Inches(4), Inches(0.35),
         font_size=14, bold=True, color=ACCENT_BLUE, align=PP_ALIGN.LEFT)
add_text(slide, "×  Kerala Police Department",
         Inches(3.8), H - Inches(1.1), Inches(5), Inches(0.35),
         font_size=14, color=LIGHT_GRAY, align=PP_ALIGN.LEFT)
add_text(slide, "CONFIDENTIAL — RESTRICTED DISTRIBUTION",
         Inches(0.4), H - Inches(0.7), Inches(6), Inches(0.3),
         font_size=10, color=ACCENT_RED, align=PP_ALIGN.LEFT)
add_text(slide, "June 2026",
         Inches(0.4), H - Inches(0.4), Inches(3), Inches(0.3),
         font_size=10, color=MID_GRAY, align=PP_ALIGN.LEFT)
add_text(slide, "Version 1.0",
         Inches(11), H - Inches(0.4), Inches(2), Inches(0.3),
         font_size=10, color=MID_GRAY, align=PP_ALIGN.RIGHT)

# Right side – tech stack visual
for i, tech in enumerate(["Python 3.11", "FastAPI", "RabbitMQ", "Keycloak", "Docker", "OpenCV", "PyTorch"]):
    add_rect(slide, Inches(8.8) + (i % 2) * Inches(2.1), Inches(2.1) + (i // 2) * Inches(0.55),
             Inches(1.9), Inches(0.42), fill_color=GRID_LINE, line_color=ACCENT_BLUE, line_width=0.3)
    add_text(slide, tech,
             Inches(8.8) + (i % 2) * Inches(2.1), Inches(2.1) + (i // 2) * Inches(0.55),
             Inches(1.9), Inches(0.42), font_size=10, bold=True, color=ACCENT_CYAN,
             align=PP_ALIGN.CENTER)

add_footer(slide, 1)

# ── SLIDE 2: Table of Contents ──────────────────────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "PRESENTATION OVERVIEW", "30-Slide Structure | System Progress Report")

sections = [
    ("01–03", "Executive Summary & Vision",          ACCENT_BLUE),
    ("04–06", "System Architecture Overview",        ACCENT_CYAN),
    ("07–08", "Phase 0 – Foundation & Environment", ACCENT_GREEN),
    ("09–10", "Module 1 – Video Source & Input Abstraction", ACCENT_BLUE),
    ("11–12", "Module 2 – Rolling Frame Buffer",     ACCENT_CYAN),
    ("13–14", "Module 3 – Change Detection Engine", ACCENT_GREEN),
    ("15–16", "Module 4 – Cognitive Frame Selector", ACCENT_GOLD),
    ("17–18", "Module 5 – Central Ingest API",       ACCENT_BLUE),
    ("19–20", "Security: Keycloak + NGINX + JWT",    ACCENT_PURPLE),
    ("21–22", "RabbitMQ Streaming Architecture",     ACCENT_CYAN),
    ("23–24", "GPU Analytics Pipeline (Planned)",    ACCENT_GOLD),
    ("25–26", "Evidence Storage & Legal Compliance", ACCENT_RED),
    ("27–28", "Dashboards & RBAC",                   ACCENT_BLUE),
    ("29",    "Roadmap & Milestones",                ACCENT_GREEN),
    ("30",    "Summary & Next Steps",                ACCENT_GOLD),
]

cols = 3
rows_per_col = 5
for i, (slides_ref, title, color) in enumerate(sections):
    col = i // rows_per_col
    row = i % rows_per_col
    cx = Inches(0.35) + col * Inches(4.3)
    cy = Inches(1.45) + row * Inches(1.08)
    add_rect(slide, cx, cy, Inches(4.1), Inches(0.95),
             fill_color=BG_CARD, line_color=color, line_width=0.6)
    add_rect(slide, cx, cy, Inches(0.06), Inches(0.95), fill_color=color)
    add_text(slide, f"Slides {slides_ref}",
             cx + Inches(0.14), cy + Inches(0.06), Inches(3.8), Inches(0.3),
             font_size=10, color=color, bold=True)
    add_text(slide, title,
             cx + Inches(0.14), cy + Inches(0.42), Inches(3.8), Inches(0.5),
             font_size=11, color=LIGHT_GRAY)

add_footer(slide, 2)

# ── SLIDE 3: Executive Summary ───────────────────────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "EXECUTIVE SUMMARY", "DVAS — Drone Video Analytics System")

add_text(slide,
    "The Drone Video Analytics System (DVAS) is a hybrid Edge-Cloud surveillance platform "
    "engineered for the Kerala Police Department. It solves the critical challenge of real-time "
    "drone video operations — bandwidth constraints and operator cognitive overload.",
    Inches(0.4), Inches(1.35), Inches(12.5), Inches(0.75),
    font_size=14, color=LIGHT_GRAY, wrap=True)

cards = [
    ("🎯", "Mission", ["Reduce bandwidth by >80% via intelligent edge filtering",
                        "Enable real-time surveillance intelligence",
                        "Ensure legal compliance & chain of custody",
                        "Support 20+ concurrent drone feeds"], ACCENT_BLUE),
    ("✅", "Completed", ["Phase 1 — Edge Processing (Modules 1–4)",
                          "Phase 2 — Central Ingest Server (Module 5)",
                          "8-service Docker Compose deployment",
                          "Security: TLS + JWT + NGINX rate limiting"], ACCENT_GREEN),
    ("🚀", "Upcoming", ["Phase 3 — GPU Analytics Cluster (Modules 6–8)",
                         "Phase 4 — Evidence Object Storage (MinIO)",
                         "Phase 5 — Databases & Audit Systems",
                         "Phase 6 — Authentication & RBAC"], ACCENT_GOLD),
]

for i, (icon, title, bullets, color) in enumerate(cards):
    cx = Inches(0.3) + i * Inches(4.3)
    bullet_card(slide, cx, Inches(2.15), Inches(4.15), Inches(4.5),
                title, bullets, title_color=color, icon=icon)

add_footer(slide, 3)

# ── SLIDE 4: Project Vision & Goals ──────────────────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "PROJECT VISION & GOALS", "Quantumweave Intelligence × Kerala Police Department")

add_text(slide, "22-Month Production Roadmap | 2-Engineer Team",
         Inches(0.4), Inches(1.35), Inches(12), Inches(0.38),
         font_size=14, bold=True, color=ACCENT_CYAN)

# Team roles
add_rect(slide, Inches(0.3), Inches(1.85), Inches(6), Inches(0.9), fill_color=BG_CARD, line_color=ACCENT_BLUE, line_width=0.5)
add_text(slide, "Engineer A — BE/Infra", Inches(0.45), Inches(1.92), Inches(5.7), Inches(0.35),
         font_size=13, bold=True, color=ACCENT_BLUE)
add_text(slide, "Backend services, infrastructure, databases, auth, streaming, DevOps",
         Inches(0.45), Inches(2.27), Inches(5.7), Inches(0.4),
         font_size=11, color=LIGHT_GRAY)

add_rect(slide, Inches(6.7), Inches(1.85), Inches(6), Inches(0.9), fill_color=BG_CARD, line_color=ACCENT_CYAN, line_width=0.5)
add_text(slide, "Engineer B — ML/FE", Inches(6.85), Inches(1.92), Inches(5.7), Inches(0.35),
         font_size=13, bold=True, color=ACCENT_CYAN)
add_text(slide, "ML models, GPU analytics, edge algorithms, dashboards, frontend",
         Inches(6.85), Inches(2.27), Inches(5.7), Inches(0.4),
         font_size=11, color=LIGHT_GRAY)

# 9 Modules
add_text(slide, "9 PRODUCTION MODULES", Inches(0.3), Inches(2.9), Inches(12), Inches(0.35),
         font_size=14, bold=True, color=ACCENT_GOLD)

modules = [
    ("[M1]", "Drone Communication Interface"),
    ("[M2]", "Edge Processing Layer"),
    ("[M3]", "Central Ingest & Streaming"),
    ("[M4]", "GPU Analytics Cluster"),
    ("[M5]", "Evidence Object Storage"),
    ("[M6]", "Databases & Audit Systems"),
    ("[M7]", "Authentication & RBAC"),
    ("[M8]", "Dashboards"),
    ("[M9]", "Legal Compliance Engine"),
]

for i, (code, name) in enumerate(modules):
    col = i % 3
    row = i // 3
    cx = Inches(0.3) + col * Inches(4.3)
    cy = Inches(3.3) + row * Inches(0.88)
    done = i < 5  # M1-M5 done
    color = ACCENT_GREEN if done else GRID_LINE
    add_rect(slide, cx, cy, Inches(4.1), Inches(0.75), fill_color=BG_CARD,
             line_color=color, line_width=0.7)
    add_text(slide, code, cx + Inches(0.12), cy + Inches(0.08),
             Inches(0.7), Inches(0.6), font_size=12, bold=True, color=color)
    add_text(slide, name, cx + Inches(0.8), cy + Inches(0.08),
             Inches(3.2), Inches(0.6), font_size=11, color=LIGHT_GRAY)
    if done:
        add_text(slide, "✓ DONE", cx + Inches(2.8), cy + Inches(0.44),
                 Inches(1.2), Inches(0.28), font_size=9, bold=True, color=ACCENT_GREEN)

add_footer(slide, 4)

# ── SLIDE 5: Current Progress Dashboard ──────────────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "CURRENT PROGRESS — AT A GLANCE", "Phases 1 & 2 Complete | Phase 3+ Planned")

metrics = [
    ("5", "Modules\nCompleted", ACCENT_GREEN),
    ("8", "Docker\nServices", ACCENT_BLUE),
    ("9", "API\nEndpoints", ACCENT_CYAN),
    ("2", "Engineers\nActive", ACCENT_GOLD),
]
for i, (val, lbl, color) in enumerate(metrics):
    cx = Inches(0.3) + i * Inches(3.25)
    add_rect(slide, cx, Inches(1.4), Inches(3.0), Inches(1.5), fill_color=BG_CARD,
             line_color=color, line_width=1.5)
    add_text(slide, val, cx, Inches(1.45), Inches(3.0), Inches(0.95),
             font_size=52, bold=True, color=color, align=PP_ALIGN.CENTER)
    add_text(slide, lbl, cx, Inches(2.38), Inches(3.0), Inches(0.5),
             font_size=11, color=LIGHT_GRAY, align=PP_ALIGN.CENTER)

# Phase progress bars
add_text(slide, "PHASE COMPLETION STATUS", Inches(0.3), Inches(3.1), Inches(12), Inches(0.38),
         font_size=14, bold=True, color=ACCENT_GOLD)

phases = [
    ("Phase 0 — Foundation & Environment",   100, ACCENT_GREEN),
    ("Phase 1 — Edge Processing Layer (M2)",  100, ACCENT_GREEN),
    ("Phase 2 — Central Ingest Server (M3)",  100, ACCENT_GREEN),
    ("Phase 3 — GPU Analytics Cluster (M4)",    5, ACCENT_GOLD),
    ("Phase 4 — Evidence Object Storage (M5)",  0, ACCENT_BLUE),
    ("Phase 5-9 — Advanced Modules",            0, MID_GRAY),
]

for i, (label, pct, color) in enumerate(phases):
    cy = Inches(3.55) + i * Inches(0.56)
    add_text(slide, label, Inches(0.35), cy, Inches(5.2), Inches(0.42),
             font_size=11, color=LIGHT_GRAY)
    progress_bar(slide, Inches(5.6), cy + Inches(0.08), Inches(6.5), Inches(0.28),
                 pct, bar_color=color, label=f"{pct}%")

add_footer(slide, 5)

# ── SLIDE 6: Technology Stack ─────────────────────────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "TECHNOLOGY STACK", "Finalized Production Architecture")

stack = [
    ("Edge Runtime",       "Python 3.11, OpenCV, PyTorch",          ACCENT_BLUE),
    ("Video Processing",   "cv2.VideoCapture, GStreamer, NumPy",     ACCENT_CYAN),
    ("Ingest Streaming",   "RabbitMQ 3.13 (→ Kafka 3.x in Phase 3)", ACCENT_GREEN),
    ("REST Backend",       "FastAPI + Uvicorn (4 workers)",          ACCENT_BLUE),
    ("Authentication",     "Keycloak 24 — OAuth2/OIDC — JWT RS256", ACCENT_PURPLE),
    ("Reverse Proxy",      "NGINX 1.26 — TLS + Rate Limiting",       ACCENT_GOLD),
    ("Containerization",   "Docker Compose — 8 services",            ACCENT_BLUE),
    ("GPU Analytics",      "NVIDIA Triton Inference Server (Phase 3)",ACCENT_CYAN),
    ("Object Storage",     "MinIO WORM (Phase 4)",                   ACCENT_GOLD),
    ("Relational DB",      "PostgreSQL 16 + TimescaleDB",            ACCENT_BLUE),
    ("Search Index",       "OpenSearch 2.x",                         ACCENT_CYAN),
    ("Dashboards",         "React 18 + TypeScript + Leaflet.js",     ACCENT_GREEN),
]

cols = 3
for i, (layer, tech, color) in enumerate(stack):
    col = i % cols
    row = i // cols
    cx = Inches(0.3) + col * Inches(4.33)
    cy = Inches(1.45) + row * Inches(1.08)
    add_rect(slide, cx, cy, Inches(4.1), Inches(0.95), fill_color=BG_CARD,
             line_color=color, line_width=0.5)
    add_rect(slide, cx, cy, Inches(4.1), Inches(0.04), fill_color=color)
    add_text(slide, layer, cx + Inches(0.12), cy + Inches(0.08), Inches(3.8), Inches(0.32),
             font_size=11, bold=True, color=color)
    add_text(slide, tech, cx + Inches(0.12), cy + Inches(0.44), Inches(3.8), Inches(0.46),
             font_size=11, color=LIGHT_GRAY)

add_footer(slide, 6)

# ── SLIDE 7: System Architecture Diagram ─────────────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "SYSTEM ARCHITECTURE", "Edge-Cloud Hybrid Pipeline Overview")

# Architecture boxes
def arch_box(x, y, w, h, label, sublabel="", color=ACCENT_BLUE):
    add_rect(slide, x, y, w, h, fill_color=BG_CARD, line_color=color, line_width=1.2)
    add_text(slide, label, x, y + Inches(0.08), w, Inches(0.35),
             font_size=12, bold=True, color=color, align=PP_ALIGN.CENTER)
    if sublabel:
        add_text(slide, sublabel, x, y + Inches(0.43), w, Inches(0.4),
                 font_size=10, color=LIGHT_GRAY, align=PP_ALIGN.CENTER)

def arrow(x1, y, x2):
    add_rect(slide, x1, y + Inches(0.2), x2 - x1, Pt(2), fill_color=ACCENT_BLUE)
    add_text(slide, "▶", x2 - Inches(0.12), y + Inches(0.07), Inches(0.15), Inches(0.28),
             font_size=11, color=ACCENT_BLUE)

# Edge layer
add_rect(slide, Inches(0.15), Inches(1.4), Inches(4.1), Inches(5.6),
         fill_color=RGBColor(0x05, 0x0F, 0x2A), line_color=ACCENT_GREEN, line_width=0.8)
add_text(slide, "EDGE LAYER (Field Laptop)", Inches(0.15), Inches(1.42),
         Inches(4.1), Inches(0.35), font_size=11, bold=True, color=ACCENT_GREEN, align=PP_ALIGN.CENTER)

arch_box(Inches(0.3), Inches(1.85), Inches(3.8), Inches(0.75), "Drone Video Source", "MP4 / RTSP / USB Capture", ACCENT_CYAN)
arch_box(Inches(0.3), Inches(2.75), Inches(3.8), Inches(0.75), "Rolling Frame Buffer", "60s circular buffer | SSD | JPEG 85%", ACCENT_CYAN)
arch_box(Inches(0.3), Inches(3.65), Inches(3.8), Inches(0.75), "Change Detection Engine", "MOG2 + LK Optical Flow | CPU-only", ACCENT_CYAN)
arch_box(Inches(0.3), Inches(4.55), Inches(3.8), Inches(0.75), "Cognitive Frame Selector", "SSIM + Saliency | 15-20 FPM output", ACCENT_CYAN)

# Arrows inside edge
for ay in [Inches(2.6), Inches(3.5), Inches(4.4)]:
    add_rect(slide, Inches(2.05), ay, Pt(2), Inches(0.15), fill_color=ACCENT_CYAN)
    add_text(slide, "▼", Inches(1.98), ay + Inches(0.12), Inches(0.2), Inches(0.2),
             font_size=9, color=ACCENT_CYAN)

# Central ingest
add_rect(slide, Inches(4.8), Inches(1.4), Inches(3.8), Inches(5.6),
         fill_color=RGBColor(0x05, 0x0F, 0x2A), line_color=ACCENT_BLUE, line_width=0.8)
add_text(slide, "INGEST LAYER (HQ Server)", Inches(4.8), Inches(1.42),
         Inches(3.8), Inches(0.35), font_size=11, bold=True, color=ACCENT_BLUE, align=PP_ALIGN.CENTER)

arch_box(Inches(4.95), Inches(1.85), Inches(3.5), Inches(0.75), "NGINX Reverse Proxy", ":443 | TLS | Rate Limit 30r/s", ACCENT_BLUE)
arch_box(Inches(4.95), Inches(2.75), Inches(3.5), Inches(0.75), "Keycloak 24", "OAuth2/OIDC | JWT RS256 | 5min TTL", ACCENT_PURPLE)
arch_box(Inches(4.95), Inches(3.65), Inches(3.5), Inches(0.75), "FastAPI Ingest API", "POST /ingest/frame | Pydantic validation", ACCENT_BLUE)
arch_box(Inches(4.95), Inches(4.55), Inches(3.5), Inches(0.75), "RabbitMQ Queue", "Per-drone queues | 1000 max | drop-head", ACCENT_BLUE)

# GPU layer
add_rect(slide, Inches(9.1), Inches(1.4), Inches(3.8), Inches(5.6),
         fill_color=RGBColor(0x05, 0x0F, 0x2A), line_color=ACCENT_GOLD, line_width=0.8)
add_text(slide, "GPU ANALYTICS (Phase 3)", Inches(9.1), Inches(1.42),
         Inches(3.8), Inches(0.35), font_size=11, bold=True, color=ACCENT_GOLD, align=PP_ALIGN.CENTER)

arch_box(Inches(9.25), Inches(1.85), Inches(3.5), Inches(0.75), "GPU Batch Consumer", "aio-pika | Batch=16 | Pre-fetch", ACCENT_GOLD)
arch_box(Inches(9.25), Inches(2.75), Inches(3.5), Inches(0.75), "NVIDIA Triton Server", "YOLOv9 | ReID | Crowd | LPR", ACCENT_GOLD)
arch_box(Inches(9.25), Inches(3.65), Inches(3.5), Inches(0.75), "Event Confirmation", "Fusion + Temporal consistency", ACCENT_GOLD)
arch_box(Inches(9.25), Inches(4.55), Inches(3.5), Inches(0.75), "Evidence Storage", "MinIO WORM | S3 | SHA-256", ACCENT_RED)

# Horizontal arrows
add_text(slide, "HTTPS JWT", Inches(4.15), Inches(3.05), Inches(0.65), Inches(0.25),
         font_size=7, color=ACCENT_CYAN)
add_rect(slide, Inches(4.2), Inches(3.3), Inches(0.6), Pt(2), fill_color=ACCENT_CYAN)
add_text(slide, "Batches", Inches(8.65), Inches(3.05), Inches(0.6), Inches(0.25),
         font_size=7, color=ACCENT_BLUE)
add_rect(slide, Inches(8.65), Inches(3.3), Inches(0.6), Pt(2), fill_color=ACCENT_BLUE)
add_text(slide, "▶", Inches(4.75), Inches(3.17), Inches(0.15), Inches(0.22), font_size=10, color=ACCENT_CYAN)
add_text(slide, "▶", Inches(9.08), Inches(3.17), Inches(0.15), Inches(0.22), font_size=10, color=ACCENT_BLUE)

add_footer(slide, 7)

# ── SLIDE 8: Phase 0 – Foundation Setup ──────────────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "PHASE 0 — FOUNDATION & ENVIRONMENT", "Weeks 1–3 | Infrastructure Scaffold")

add_text(slide, "✅  COMPLETED — All foundation tasks delivered",
         Inches(0.4), Inches(1.38), Inches(12), Inches(0.38),
         font_size=14, bold=True, color=ACCENT_GREEN)

cols_data = [
    ("BE/Infra", [
        "Ubuntu Server 22.04 on all nodes",
        "Docker Engine + Compose installed",
        "WireGuard VPN (Field → HQ tunnel)",
        "VLAN segmentation: 10/20/30/40/50",
        "Prometheus + Grafana observability",
        "Loki for centralized log aggregation",
        "CI/CD with lint → test → build pipeline",
    ], ACCENT_BLUE),
    ("ML/FE", [
        "CUDA Toolkit 12.x + cuDNN installed",
        "Python 3.11 virtualenvs (pyproject.toml)",
        "Node.js 20 LTS for dashboard development",
        "Base models evaluated: YOLOv9, ByteTrack",
        "InsightFace, CSRNet, LPRNet benchmarked",
        "pre-commit hooks: black, ruff, mypy",
        "VisDrone synthetic test dataset ready",
    ], ACCENT_CYAN),
    ("Joint", [
        "API contract: OpenAPI 3.1 spec defined",
        "Monorepo structure established",
        "Git branching strategy: main/dev/feature",
        "ADR (Architecture Decision Records)",
        "Secrets management: .env convention",
        "Tech stack finalized (no revisits)",
        "Phase 0 gate review: all items ✓",
    ], ACCENT_GREEN),
]

for i, (title, bullets, color) in enumerate(cols_data):
    cx = Inches(0.3) + i * Inches(4.33)
    bullet_card(slide, cx, Inches(1.88), Inches(4.1), Inches(4.8),
                title, bullets, title_color=color)

add_footer(slide, 8)

# ── SLIDE 9: Module 1 – Video Source ─────────────────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "MODULE 1 — VIDEO SOURCE & INPUT ABSTRACTION",
               "Phase 1 | Edge Processing Layer | Status: ✅ Complete")

add_text(slide, "The system abstracts drone camera inputs to provide a unified interface "
         "for multiple video sources, using a producer-consumer thread architecture.",
         Inches(0.4), Inches(1.38), Inches(12.5), Inches(0.6),
         font_size=13, color=LIGHT_GRAY, wrap=True)

# DroneFeedSimulator card
add_rect(slide, Inches(0.3), Inches(2.05), Inches(6.0), Inches(4.5),
         fill_color=BG_CARD, line_color=ACCENT_CYAN, line_width=0.8)
add_rect(slide, Inches(0.3), Inches(2.05), Inches(6.0), Inches(0.04), fill_color=ACCENT_CYAN)
add_text(slide, "DroneFeedSimulator", Inches(0.42), Inches(2.1), Inches(5.8), Inches(0.38),
         font_size=15, bold=True, color=ACCENT_CYAN)

sim_lines = [
    {"text": "• Video source: MP4 / RTSP / USB-HDMI capture card", "size": 11, "color": LIGHT_GRAY},
    {"text": "• Background daemon thread via queue.Queue (maxsize=300)", "size": 11, "color": LIGHT_GRAY},
    {"text": "• Frame rate: native video FPS (default 30 if unreadable)", "size": 11, "color": LIGHT_GRAY},
    {"text": "• Auto-loops video on stream end (indefinite playback)", "size": 11, "color": LIGHT_GRAY},
    {"text": "• Back-pressure: 'Queue Full' warning if consumer lags", "size": 11, "color": LIGHT_GRAY},
    {"text": "• Producer-consumer arch: simulator (daemon) → detector (main)", "size": 11, "color": LIGHT_GRAY},
    {"text": "• Clean shutdown: stop() joins daemon thread, closes capture", "size": 11, "color": LIGHT_GRAY},
]
add_multiline_text(slide, sim_lines, Inches(0.44), Inches(2.55), Inches(5.7), Inches(3.8))

# Telemetry card
add_rect(slide, Inches(6.7), Inches(2.05), Inches(6.0), Inches(4.5),
         fill_color=BG_CARD, line_color=ACCENT_BLUE, line_width=0.8)
add_rect(slide, Inches(6.7), Inches(2.05), Inches(6.0), Inches(0.04), fill_color=ACCENT_BLUE)
add_text(slide, "Simulated Telemetry Metadata", Inches(6.82), Inches(2.1), Inches(5.8), Inches(0.38),
         font_size=15, bold=True, color=ACCENT_BLUE)

telem = [
    ("drone_id",   "\"DRONE_01\" (fixed)"),
    ("mission_id", "\"MISSION_001\" (fixed)"),
    ("timestamp",  "UTC ISO 8601 per frame"),
    ("gps_lat",    "8.5241 ± 0.001° jitter"),
    ("gps_lon",    "76.9366 ± 0.001° jitter"),
    ("altitude",   "Random 100–130 m"),
    ("speed",      "Random 10–20 m/s"),
    ("heading",    "Random 0–360°"),
]
for i, (key, val) in enumerate(telem):
    cy = Inches(2.56) + i * Inches(0.48)
    add_text(slide, key, Inches(6.84), cy, Inches(1.8), Inches(0.42),
             font_size=11, bold=True, color=ACCENT_CYAN)
    add_text(slide, val, Inches(8.7), cy, Inches(3.8), Inches(0.42),
             font_size=11, color=LIGHT_GRAY)

add_footer(slide, 9)

# ── SLIDE 10: Module 1 – Implementation Details ───────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "MODULE 1 — IMPLEMENTATION DETAILS",
               "Producer-Consumer Architecture | Thread-Safe Frame Queue")

# Code snippet
add_rect(slide, Inches(0.3), Inches(1.42), Inches(6.5), Inches(5.2),
         fill_color=RGBColor(0x05, 0x0B, 0x20), line_color=ACCENT_BLUE, line_width=0.5)
add_text(slide, "▌ Code Reference — combined_change_stream.py",
         Inches(0.42), Inches(1.48), Inches(6.3), Inches(0.35),
         font_size=11, bold=True, color=ACCENT_CYAN)

code_lines = [
    {"text": "class DroneFeedSimulator:", "size": 10, "color": ACCENT_CYAN, "bold": True},
    {"text": "  def __init__(self, video_path):", "size": 10, "color": WHITE},
    {"text": "    self.output_queue = queue.Queue(maxsize=300)", "size": 10, "color": LIGHT_GRAY},
    {"text": "    self.running = False", "size": 10, "color": LIGHT_GRAY},
    {"text": "", "size": 10, "color": LIGHT_GRAY},
    {"text": "  def stream_video(self):  # daemon thread", "size": 10, "color": ACCENT_CYAN},
    {"text": "    cap = cv2.VideoCapture(self.video_path)", "size": 10, "color": LIGHT_GRAY},
    {"text": "    fps = cap.get(cv2.CAP_PROP_FPS) or 30", "size": 10, "color": LIGHT_GRAY},
    {"text": "    while self.running:", "size": 10, "color": LIGHT_GRAY},
    {"text": "      ret, frame = cap.read()", "size": 10, "color": LIGHT_GRAY},
    {"text": "      packet = {'frame': frame,", "size": 10, "color": LIGHT_GRAY},
    {"text": "               'metadata': self.generate_metadata()}", "size": 10, "color": LIGHT_GRAY},
    {"text": "      self.output_queue.put(packet, timeout=1)", "size": 10, "color": LIGHT_GRAY},
    {"text": "      time.sleep(1.0 / fps)", "size": 10, "color": LIGHT_GRAY},
    {"text": "", "size": 10, "color": LIGHT_GRAY},
    {"text": "  def get_frame_packet(self) -> dict | None:", "size": 10, "color": ACCENT_CYAN},
    {"text": "    # Blocking dequeue, 1s timeout", "size": 10, "color": MID_GRAY},
    {"text": "    return self.output_queue.get(timeout=1)", "size": 10, "color": LIGHT_GRAY},
]
add_multiline_text(slide, code_lines, Inches(0.44), Inches(1.88), Inches(6.2), Inches(4.5))

# Key design decisions
bullet_card(slide, Inches(7.0), Inches(1.42), Inches(5.9), Inches(2.4),
            "Design Decisions", [
                "Daemon thread: auto-exits when main process ends",
                "maxsize=300: prevents OOM on slow consumer",
                "1-second put timeout: graceful back-pressure",
                "thread.join() on stop(): clean shutdown",
            ], title_color=ACCENT_GREEN)

bullet_card(slide, Inches(7.0), Inches(4.0), Inches(5.9), Inches(2.6),
            "Known Limitations (Documented)", [
                "VIDEO_PATH hardcoded in __main__ block",
                "GPS/telemetry are simulated (not real SDK)",
                "No homography stabilisation — camera shake = FP",
                "min_changed_points reserved (not active in v1)",
                "Queue drop-head if GPU consumer lags badly",
            ], title_color=ACCENT_GOLD)

add_footer(slide, 10)

# ── SLIDE 11: Module 2 – Rolling Frame Buffer ─────────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "MODULE 2 — ROLLING FRAME BUFFER",
               "Phase 1 | Edge Memory Management | Status: ✅ Complete")

add_text(slide,
    "Prevents Out of Memory (OOM) errors during long 3+ hour drone missions. "
    "Uses circular buffer backed by memory-mapped SSD files — not volatile RAM.",
    Inches(0.4), Inches(1.38), Inches(12.5), Inches(0.6),
    font_size=13, color=LIGHT_GRAY, wrap=True)

bullet_card(slide, Inches(0.3), Inches(2.05), Inches(4.1), Inches(4.5),
            "Circular Buffer Design", [
                "Fixed 60-second retention window (configurable)",
                "Memory-mapped files on local SSD partition",
                "Each entry: (frame_index, utc_ts_ms, frame_bytes)",
                "Thread-safe: separate producer & consumer threads",
                "JPEG 85% compression — balances size vs. fidelity",
                "Avoids RAM growth on extended operations",
            ], title_color=ACCENT_CYAN)

bullet_card(slide, Inches(4.7), Inches(2.05), Inches(4.1), Inches(4.5),
            "Evidence Clip Extraction", [
                "extract_segment(start_ts, end_ts) → List[Frame]",
                "Triggered by central server confirmed event",
                "Window: event_ts ± 30 seconds context",
                "Transcodes → MP4 (H.264, libx264, CRF 22)",
                "Computes SHA-256 hash (chain of custody)",
                "Uploads to central storage, deletes local copy",
                "Verifies hash on server response before deletion",
            ], title_color=ACCENT_GREEN)

bullet_card(slide, Inches(9.1), Inches(2.05), Inches(4.1), Inches(4.5),
            "Performance Specs", [
                "Retention: 60 seconds (2+ hours tested ✓)",
                "Compression: JPEG @ 85% quality",
                "Storage: SSD memory-mapped (no RAM growth)",
                "Concurrent R/W: thread-safe locking",
                "Unit tests: overflow, timestamp, concurrency",
                "Evidence clip: H.264 | CRF 22 | libx264",
                "Hash algorithm: SHA-256 (legal compliance)",
            ], title_color=ACCENT_GOLD)

add_footer(slide, 11)

# ── SLIDE 12: Module 2 – Buffer Architecture ──────────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "MODULE 2 — BUFFER ARCHITECTURE DETAIL",
               "Circular Buffer | Evidence Pipeline | SSD-Backed Storage")

# Buffer diagram
add_rect(slide, Inches(0.5), Inches(1.7), Inches(5.5), Inches(3.0),
         fill_color=BG_CARD, line_color=ACCENT_CYAN, line_width=0.8)
add_text(slide, "Rolling Frame Buffer (60 sec window)", Inches(0.62), Inches(1.76),
         Inches(5.3), Inches(0.38), font_size=13, bold=True, color=ACCENT_CYAN)

# Circular buffer segments
for j in range(12):
    angle_x = Inches(0.75) + j * Inches(0.41)
    clr = ACCENT_GREEN if j < 8 else GRID_LINE
    add_rect(slide, angle_x, Inches(2.25), Inches(0.36), Inches(0.7),
             fill_color=clr, line_color=BG_DARK, line_width=0.5)
    if j < 8:
        add_text(slide, f"F{j*7+1}", angle_x, Inches(2.3), Inches(0.36), Inches(0.6),
                 font_size=7, color=BG_DARK, bold=True, align=PP_ALIGN.CENTER)

add_text(slide, "←── 60 seconds of frames ──→",
         Inches(0.75), Inches(3.05), Inches(5.1), Inches(0.35),
         font_size=10, color=ACCENT_CYAN, align=PP_ALIGN.CENTER)
add_text(slide, "Write head →", Inches(3.75), Inches(2.2), Inches(2.2), Inches(0.35),
         font_size=9, color=ACCENT_GOLD)

# Storage representation
add_rect(slide, Inches(0.62), Inches(3.5), Inches(5.1), Inches(0.8),
         fill_color=GRID_LINE, line_color=ACCENT_BLUE, line_width=0.5)
add_text(slide, "SSD Memory-Mapped File (mmap)", Inches(0.62), Inches(3.55),
         Inches(5.1), Inches(0.7), font_size=11, color=LIGHT_GRAY, align=PP_ALIGN.CENTER)
add_text(slide, "frame_index | utc_ts_ms | compressed_bytes",
         Inches(0.62), Inches(3.8), Inches(5.1), Inches(0.4),
         font_size=9, color=MID_GRAY, align=PP_ALIGN.CENTER)

# Evidence flow
add_rect(slide, Inches(6.5), Inches(1.7), Inches(6.4), Inches(5.0),
         fill_color=BG_CARD, line_color=ACCENT_GREEN, line_width=0.8)
add_text(slide, "Evidence Clip Extraction Pipeline", Inches(6.62), Inches(1.76),
         Inches(6.2), Inches(0.38), font_size=13, bold=True, color=ACCENT_GREEN)

ev_steps = [
    ("1", "Central server sends 'event.confirmed' signal",         ACCENT_GOLD),
    ("2", "Extract frames: event_ts ± 30 seconds from buffer",     ACCENT_CYAN),
    ("3", "Encode to MP4: H.264, libx264, CRF 22",               ACCENT_BLUE),
    ("4", "Compute SHA-256 hash of MP4 file",                     ACCENT_BLUE),
    ("5", "Write sidecar JSON: event_id, drone_id, hashes",       ACCENT_BLUE),
    ("6", "Upload MP4 + sidecar to central evidence storage",     ACCENT_GREEN),
    ("7", "Verify server hash matches → delete local copy",        ACCENT_GREEN),
]
for i, (num, step, color) in enumerate(ev_steps):
    cy = Inches(2.2) + i * Inches(0.6)
    add_rect(slide, Inches(6.62), cy, Inches(0.35), Inches(0.45),
             fill_color=color)
    add_text(slide, num, Inches(6.62), cy, Inches(0.35), Inches(0.45),
             font_size=11, bold=True, color=BG_DARK, align=PP_ALIGN.CENTER)
    add_text(slide, step, Inches(7.05), cy + Inches(0.04), Inches(5.7), Inches(0.5),
             font_size=11, color=LIGHT_GRAY)

add_footer(slide, 12)

# ── SLIDE 13: Module 3 – Change Detection Engine ──────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "MODULE 3 — CHANGE DETECTION ENGINE",
               "Phase 1 | CPU-Efficient Edge Analytics | Status: ✅ Complete")

add_text(slide,
    "Lightweight CPU-efficient filter that eliminates static frames and isolates active motion. "
    "Reduces data sent for deep AI analysis by >80% without requiring a GPU.",
    Inches(0.4), Inches(1.38), Inches(12.5), Inches(0.6),
    font_size=13, color=LIGHT_GRAY, wrap=True)

# Two stage pipeline
add_rect(slide, Inches(0.3), Inches(2.05), Inches(12.7), Inches(1.05),
         fill_color=BG_CARD, line_color=ACCENT_GREEN, line_width=0.6)
add_text(slide, "TWO-STAGE DETECTION PIPELINE", Inches(0.42), Inches(2.1),
         Inches(12.5), Inches(0.38), font_size=13, bold=True, color=ACCENT_GREEN)

add_rect(slide, Inches(0.42), Inches(2.55), Inches(5.9), Inches(0.45),
         fill_color=GRID_LINE)
add_text(slide, "Stage 1: Frame Differencing (MOG2 Background Subtractor)",
         Inches(0.54), Inches(2.58), Inches(5.8), Inches(0.4),
         font_size=11, bold=True, color=ACCENT_BLUE)

add_rect(slide, Inches(6.75), Inches(2.55), Inches(6.0), Inches(0.45),
         fill_color=GRID_LINE)
add_text(slide, "Stage 2: Region-of-Change Scoring & Optical Flow",
         Inches(6.87), Inches(2.58), Inches(5.8), Inches(0.4),
         font_size=11, bold=True, color=ACCENT_CYAN)

bullet_card(slide, Inches(0.3), Inches(3.18), Inches(4.1), Inches(3.4),
            "Stage 1 — Fast Differencing", [
                "Resize frames to 960×540 → convert to grayscale",
                "MOG2 Gaussian Mixture Model background subtractor",
                "history, varThreshold, detectShadows tuned",
                "Binary motion mask → threshold at diff_threshold=10",
                "GaussianBlur 5×5 → MORPH_OPEN 3×3 → Dilate x2",
                "Suppresses sensor noise, compression artifacts, wind",
            ], title_color=ACCENT_BLUE)

bullet_card(slide, Inches(4.7), Inches(3.18), Inches(4.1), Inches(3.4),
            "Stage 2 — Region Scoring", [
                "cv2.findContours on cleaned binary mask",
                "Filter blobs < motion_area_threshold (80 px²)",
                "Score each region: area, magnitude, persistence",
                "change_detected = changed_regions > 0",
                "motion_score = mean optical flow vector magnitude",
                "Output: ChangeEvent(timestamp, regions, score)",
            ], title_color=ACCENT_CYAN)

bullet_card(slide, Inches(9.1), Inches(3.18), Inches(4.1), Inches(3.4),
            "Lucas-Kanade Optical Flow", [
                "Shi-Tomasi corners: goodFeaturesToTrack (300 pts)",
                "calcOpticalFlowPyrLK: winSize=(21,21), maxLevel=3",
                "Differentiates real movement from lighting changes",
                "Stability check: < 10 tracked pts → skip frame",
                "Feature refresh when tracked pts fall below 10",
                "Validates structural movement vs. panning/shadow",
            ], title_color=ACCENT_GREEN)

add_footer(slide, 13)

# ── SLIDE 14: Module 3 – Detection Parameters & Performance ───────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "MODULE 3 — DETECTION PARAMETERS & PERFORMANCE",
               "DroneChangeDetector | Configurable Thresholds | Output Format")

# Parameters table
add_text(slide, "CONFIGURABLE PARAMETERS", Inches(0.4), Inches(1.42),
         Inches(12), Inches(0.35), font_size=14, bold=True, color=ACCENT_GOLD)

headers = ["Parameter", "Default", "Increase Effect", "Decrease Effect"]
col_widths = [Inches(2.0), Inches(0.8), Inches(4.5), Inches(4.5)]
col_starts = [Inches(0.3), Inches(2.35), Inches(3.2), Inches(7.75)]

# Table header
for j, (hdr, cw, cx) in enumerate(zip(headers, col_widths, col_starts)):
    add_rect(slide, cx, Inches(1.85), cw, Inches(0.38), fill_color=ACCENT_BLUE)
    add_text(slide, hdr, cx, Inches(1.88), cw, Inches(0.34),
             font_size=11, bold=True, color=WHITE, align=PP_ALIGN.CENTER)

params = [
    ("diff_threshold", "10",  "Fewer pixels as motion (less noise)", "More sensitive, more false positives"),
    ("motion_area_threshold", "80 px²", "Only larger objects detected", "Small noise triggers events"),
    ("optical_flow_threshold", "0.5", "Only fast objects counted", "Slow drift also counted"),
    ("max_corners (feature_params)", "300", "Higher CPU cost", "May lose tracking coverage"),
    ("resize_width / height", "960×540", "Higher CPU + memory", "Faster, coarser detection"),
    ("min_changed_points", "3", "Reserved / future use", "Reserved / future use"),
]

for i, (param, default, inc, dec) in enumerate(params):
    cy = Inches(2.27) + i * Inches(0.52)
    bg = BG_CARD if i % 2 == 0 else GRID_LINE
    for j, (text, cw, cx) in enumerate(zip([param, default, inc, dec], col_widths, col_starts)):
        add_rect(slide, cx, cy, cw, Inches(0.5), fill_color=bg)
        clr = ACCENT_CYAN if j == 0 else (ACCENT_GOLD if j == 1 else LIGHT_GRAY)
        add_text(slide, text, cx + Inches(0.05), cy + Inches(0.04),
                 cw - Inches(0.1), Inches(0.44), font_size=10, color=clr)

# Output format
add_text(slide, "DETECTION OUTPUT (per frame)", Inches(0.4), Inches(5.55),
         Inches(12), Inches(0.35), font_size=14, bold=True, color=ACCENT_GREEN)

outputs = [
    ("change_detected", "bool", "True if ≥1 significant motion contour found"),
    ("motion_score",    "float", "Mean optical flow vector magnitude (2 dp)"),
    ("changed_regions", "int",   "Count of contours > motion_area_threshold"),
    ("mask",            "ndarray", "Binary (0/255) debug motion mask image"),
]
for i, (key, dtype, desc) in enumerate(outputs):
    cx = Inches(0.3) + i * Inches(3.2)
    add_rect(slide, cx, Inches(5.95), Inches(3.0), Inches(0.85),
             fill_color=BG_CARD, line_color=ACCENT_GREEN, line_width=0.5)
    add_text(slide, key, cx + Inches(0.1), Inches(6.0), Inches(2.8), Inches(0.3),
             font_size=11, bold=True, color=ACCENT_GREEN)
    add_text(slide, f"({dtype})", cx + Inches(0.1), Inches(6.3), Inches(2.8), Inches(0.22),
             font_size=9, color=ACCENT_GOLD, italic=True)
    add_text(slide, desc, cx + Inches(0.1), Inches(6.52), Inches(2.8), Inches(0.25),
             font_size=9, color=LIGHT_GRAY)

add_footer(slide, 14)

# ── SLIDE 15: Module 4 – Cognitive Frame Selector ─────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "MODULE 4 — COGNITIVE FRAME SELECTOR",
               "Phase 1 | Intelligent Bandwidth Gatekeeper | Status: ✅ Complete")

add_text(slide,
    "Determines which motion-detected frames are worth transmitting to HQ. "
    "Acts as the final bandwidth optimizer — targeting 15–20 FPM under normal conditions.",
    Inches(0.4), Inches(1.38), Inches(12.5), Inches(0.6),
    font_size=13, color=LIGHT_GRAY, wrap=True)

# 4 scoring axes
scores = [
    ("Score 1", "Structural Change (SSIM)",
     "Structural Similarity Index vs. last transmitted frame. High difference = new visual information worth sending.",
     ACCENT_BLUE),
    ("Score 2", "Motion Quality Score",
     "Farneback optical flow magnitude in changed regions. Distinguishes real motion from lighting/shadow changes.",
     ACCENT_CYAN),
    ("Score 3", "Saliency Score",
     "Lightweight edge-density heuristic (BING saliency). Scores how 'interesting' or distinct the frame content is.",
     ACCENT_GREEN),
    ("Score 4", "Temporal Diversity",
     "Penalizes frames too close in time to a recently selected frame. Anti-burst logic prevents near-duplicate transmissions.",
     ACCENT_GOLD),
]

for i, (num, name, desc, color) in enumerate(scores):
    row = i // 2
    col = i % 2
    cx = Inches(0.3) + col * Inches(6.5)
    cy = Inches(2.05) + row * Inches(1.8)
    add_rect(slide, cx, cy, Inches(6.2), Inches(1.65), fill_color=BG_CARD,
             line_color=color, line_width=0.8)
    add_rect(slide, cx, cy, Inches(6.2), Inches(0.04), fill_color=color)
    add_text(slide, num, cx + Inches(0.14), cy + Inches(0.08),
             Inches(1.2), Inches(0.38), font_size=12, bold=True, color=color)
    add_text(slide, name, cx + Inches(1.3), cy + Inches(0.08),
             Inches(4.8), Inches(0.38), font_size=13, bold=True, color=WHITE)
    add_text(slide, desc, cx + Inches(0.14), cy + Inches(0.52),
             Inches(5.9), Inches(0.9), font_size=11, color=LIGHT_GRAY, wrap=True)

# Output rates
add_rect(slide, Inches(0.3), Inches(5.72), Inches(12.7), Inches(1.0),
         fill_color=BG_CARD, line_color=ACCENT_GOLD, line_width=0.8)
add_text(slide, "OUTPUT TRANSMISSION RATES",
         Inches(0.42), Inches(5.78), Inches(4), Inches(0.35),
         font_size=13, bold=True, color=ACCENT_GOLD)
add_text(slide, "Normal:  15–20 FPM per drone",
         Inches(4.5), Inches(5.78), Inches(3.5), Inches(0.35),
         font_size=13, color=ACCENT_GREEN, bold=True)
add_text(slide, "High Activity:  up to 40 FPM burst",
         Inches(8.5), Inches(5.78), Inches(4.3), Inches(0.35),
         font_size=13, color=ACCENT_GOLD, bold=True)
add_text(slide, "Frames compressed: Base64 JPEG + GZIP payload | Metadata: drone_id, mission_id, utc_ts_ms, GPS, change_score, cognitive_score",
         Inches(0.42), Inches(6.18), Inches(12.5), Inches(0.42),
         font_size=10, color=LIGHT_GRAY)

add_footer(slide, 15)

# ── SLIDE 16: Module 4 – Frame Packaging ──────────────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "MODULE 4 — FRAME PACKAGING & PAYLOAD FORMAT",
               "JSON Payload Structure | GZIP Compression | Metadata Schema")

add_text(slide, "FRAME PAYLOAD SCHEMA (per transmitted frame)",
         Inches(0.4), Inches(1.42), Inches(12), Inches(0.38),
         font_size=14, bold=True, color=ACCENT_CYAN)

# JSON schema card
add_rect(slide, Inches(0.3), Inches(1.88), Inches(5.5), Inches(4.8),
         fill_color=RGBColor(0x05, 0x0B, 0x20), line_color=ACCENT_CYAN, line_width=0.6)

json_lines = [
    {"text": "{", "size": 11, "color": WHITE},
    {"text": "  \"frame_id\":        \"uuid-v4\",", "size": 11, "color": LIGHT_GRAY},
    {"text": "  \"drone_id\":        \"DRONE_KL_001\",", "size": 11, "color": ACCENT_CYAN},
    {"text": "  \"mission_id\":      \"MISSION_20260612\",", "size": 11, "color": ACCENT_CYAN},
    {"text": "  \"utc_timestamp_ms\": 1750000000000,", "size": 11, "color": LIGHT_GRAY},
    {"text": "  \"location\": {", "size": 11, "color": LIGHT_GRAY},
    {"text": "    \"lat\": 8.5241, \"lon\": 76.9366,", "size": 11, "color": ACCENT_GREEN},
    {"text": "    \"altitude_m\": 50", "size": 11, "color": ACCENT_GREEN},
    {"text": "  },", "size": 11, "color": LIGHT_GRAY},
    {"text": "  \"frame_jpeg_b64\": \"...\",   // base64", "size": 11, "color": MID_GRAY},
    {"text": "  \"change_score\":    0.72,     // 0.0–1.0", "size": 11, "color": ACCENT_GOLD},
    {"text": "  \"cognitive_score\": 0.85,     // 0.0–1.0", "size": 11, "color": ACCENT_GOLD},
    {"text": "  \"motion_regions\":  [...]", "size": 11, "color": LIGHT_GRAY},
    {"text": "}", "size": 11, "color": WHITE},
    {"text": "// Entire payload GZIP-compressed before TX", "size": 10, "color": MID_GRAY, "italic": True},
]
add_multiline_text(slide, json_lines, Inches(0.44), Inches(1.96), Inches(5.3), Inches(4.6))

bullet_card(slide, Inches(6.1), Inches(1.88), Inches(6.5), Inches(2.2),
            "FrameMetadata Pydantic Model", [
                "drone_id: str  (e.g. DRONE_KL_001)",
                "mission_id: str  (e.g. MISSION_20260612)",
                "timestamp: str  (ISO 8601)",
                "frame_sequence: int  (per-session counter)",
                "change_score: float  (0.0–1.0)",
                "latitude, longitude, altitude_m: Optional[float]",
            ], title_color=ACCENT_BLUE)

bullet_card(slide, Inches(6.1), Inches(4.25), Inches(6.5), Inches(2.4),
            "Transmission Pipeline", [
                "Select frame → encode JPEG → Base64 encode",
                "Package with full metadata → JSON serialize",
                "GZIP compress → HTTPS POST to /ingest/frame",
                "Authorization: Bearer <JWT> header required",
                "On success: HTTP 202 with queued frame_id",
                "Token auto-refresh 30s before 5-min expiry",
            ], title_color=ACCENT_GREEN)

add_footer(slide, 16)

# ── SLIDE 17: Module 5 – Central Ingest API ───────────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "MODULE 5 — CENTRAL INGEST API",
               "Phase 2 | FastAPI + NGINX + Keycloak + RabbitMQ | Status: ✅ Complete")

add_text(slide,
    "The Central Ingest Server is the authenticated cloud gateway that safely receives "
    "optimized frames from all field laptops. 8 Docker services deployed via Docker Compose.",
    Inches(0.4), Inches(1.38), Inches(12.5), Inches(0.6),
    font_size=13, color=LIGHT_GRAY, wrap=True)

# API Endpoints
add_text(slide, "9 ACTIVE API ENDPOINTS", Inches(0.4), Inches(2.08),
         Inches(12), Inches(0.35), font_size=13, bold=True, color=ACCENT_GOLD)

endpoints = [
    ("POST", "/ingest/frame",             "Upload frame + metadata (Bearer JWT)",         ACCENT_GREEN),
    ("GET",  "/ingest/status",            "All-drone queue status",                        ACCENT_BLUE),
    ("GET",  "/ingest/status/{id}",       "Per-drone queue stats",                        ACCENT_BLUE),
    ("POST", "/ingest/mission/start",     "Register new mission",                          ACCENT_CYAN),
    ("POST", "/ingest/mission/end",       "Close mission",                                 ACCENT_CYAN),
    ("GET",  "/health",                   "API liveness check (no auth)",                  ACCENT_GREEN),
    ("GET",  "/health/ready",             "Readiness: RabbitMQ + Keycloak",               ACCENT_GREEN),
    ("GET",  "/metrics",                  "Prometheus metrics",                            ACCENT_PURPLE),
    ("WS",   "/ws/status",               "Live WebSocket status feed",                    ACCENT_GOLD),
]

for i, (method, path, desc, color) in enumerate(endpoints):
    row = i // 3
    col = i % 3
    cx = Inches(0.3) + col * Inches(4.33)
    cy = Inches(2.5) + row * Inches(0.68)
    add_rect(slide, cx, cy, Inches(4.1), Inches(0.6),
             fill_color=BG_CARD, line_color=color, line_width=0.4)
    add_rect(slide, cx, cy, Inches(0.6), Inches(0.6), fill_color=color)
    add_text(slide, method, cx, cy, Inches(0.6), Inches(0.6),
             font_size=10, bold=True, color=BG_DARK, align=PP_ALIGN.CENTER)
    add_text(slide, path, cx + Inches(0.65), cy + Inches(0.04),
             Inches(3.35), Inches(0.3), font_size=10, bold=True, color=WHITE)
    add_text(slide, desc, cx + Inches(0.65), cy + Inches(0.34),
             Inches(3.35), Inches(0.24), font_size=9, color=MID_GRAY)

# Docker services
add_text(slide, "8 DOCKER COMPOSE SERVICES", Inches(0.4), Inches(5.55),
         Inches(12), Inches(0.35), font_size=13, bold=True, color=ACCENT_BLUE)

services = [
    ("drone_postgres",      "Keycloak DB",           ACCENT_BLUE),
    ("drone_keycloak",      "JWT Auth :8080",         ACCENT_PURPLE),
    ("drone_rabbitmq",      "Message Queue :15672",   ACCENT_GOLD),
    ("drone_ingest_api",    "FastAPI :8000",           ACCENT_CYAN),
    ("drone_nginx",         "HTTPS :443",             ACCENT_GREEN),
    ("drone_analytics",     "Change Detection :9090", ACCENT_BLUE),
    ("drone_gpu_consumer",  "Batch Consumer",         ACCENT_GOLD),
    ("drone_setup",         "One-time init",          ACCENT_GREEN),
]
for i, (name, role, color) in enumerate(services):
    cx = Inches(0.3) + i * Inches(1.58)
    add_rect(slide, cx, Inches(5.95), Inches(1.52), Inches(0.78),
             fill_color=BG_CARD, line_color=color, line_width=0.6)
    add_text(slide, name, cx + Inches(0.05), Inches(5.97), Inches(1.42), Inches(0.35),
             font_size=8, bold=True, color=color)
    add_text(slide, role, cx + Inches(0.05), Inches(6.32), Inches(1.42), Inches(0.38),
             font_size=9, color=LIGHT_GRAY)

add_footer(slide, 17)

# ── SLIDE 18: Module 5 – FastAPI Implementation ──────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "MODULE 5 — FASTAPI IMPLEMENTATION DETAIL",
               "Frame Ingestion Endpoint | Pydantic Validation | Error Handling")

# Code
add_rect(slide, Inches(0.3), Inches(1.42), Inches(6.5), Inches(5.25),
         fill_color=RGBColor(0x05, 0x0B, 0x20), line_color=ACCENT_BLUE, line_width=0.5)
add_text(slide, "▌ ingest/api/main.py — POST /ingest/frame",
         Inches(0.42), Inches(1.48), Inches(6.3), Inches(0.35),
         font_size=11, bold=True, color=ACCENT_CYAN)

fastapi_code = [
    {"text": "@app.post('/ingest/frame', response_model=IngestResponse)", "size": 9, "color": ACCENT_CYAN},
    {"text": "async def ingest_frame(", "size": 9, "color": WHITE},
    {"text": "    frame:         UploadFile = File(...),", "size": 9, "color": LIGHT_GRAY},
    {"text": "    metadata_json: str        = Form(...),", "size": 9, "color": LIGHT_GRAY},
    {"text": "    claims:        dict       = Depends(validate_token)", "size": 9, "color": LIGHT_GRAY},
    {"text": "):", "size": 9, "color": WHITE},
    {"text": "    # Parse and validate metadata", "size": 9, "color": MID_GRAY},
    {"text": "    meta = FrameMetadata(**json.loads(metadata_json))", "size": 9, "color": LIGHT_GRAY},
    {"text": "", "size": 9, "color": LIGHT_GRAY},
    {"text": "    # MIME type check (JPEG/PNG only)", "size": 9, "color": MID_GRAY},
    {"text": "    if frame.content_type not in ALLOWED_TYPES:", "size": 9, "color": LIGHT_GRAY},
    {"text": "        raise HTTPException(415, 'Only JPEG/PNG')", "size": 9, "color": ACCENT_RED},
    {"text": "", "size": 9, "color": LIGHT_GRAY},
    {"text": "    # Size check (max 5MB)", "size": 9, "color": MID_GRAY},
    {"text": "    data = await frame.read()", "size": 9, "color": LIGHT_GRAY},
    {"text": "    if len(data) > MAX_FRAME_BYTES:", "size": 9, "color": LIGHT_GRAY},
    {"text": "        raise HTTPException(413, 'Frame exceeds limit')", "size": 9, "color": ACCENT_RED},
    {"text": "", "size": 9, "color": LIGHT_GRAY},
    {"text": "    # Publish to drone-specific RabbitMQ queue", "size": 9, "color": MID_GRAY},
    {"text": "    await publish_frame(meta.drone_id, meta.model_dump(), data)", "size": 9, "color": ACCENT_GREEN},
    {"text": "    return IngestResponse(status='queued', ...)", "size": 9, "color": LIGHT_GRAY},
]
add_multiline_text(slide, fastapi_code, Inches(0.44), Inches(1.88), Inches(6.2), Inches(4.7))

# Validation rules
bullet_card(slide, Inches(7.0), Inches(1.42), Inches(5.9), Inches(2.4),
            "Validation Rules", [
                "JWT: RS256 signature + drone-operator role check",
                "MIME type: image/jpeg or image/png only",
                "Max payload: 5MB per frame",
                "Pydantic schema: all required fields present",
                "Drone ID must match JWT claims (no cross-drone injection)",
            ], title_color=ACCENT_BLUE)

bullet_card(slide, Inches(7.0), Inches(4.0), Inches(5.9), Inches(2.65),
            "HTTP Status Codes", [
                "202 Accepted — Frame queued successfully",
                "401 Unauthorized — Missing/invalid JWT",
                "403 Forbidden — Insufficient role",
                "413 Payload Too Large — Frame > 5MB",
                "415 Unsupported Media Type — Not JPEG/PNG",
                "422 Unprocessable — Bad metadata JSON",
                "429 Too Many Requests — Rate limit exceeded",
            ], title_color=ACCENT_RED)

add_footer(slide, 18)

# ── SLIDE 19: Security – Keycloak + NGINX ─────────────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "SECURITY LAYER — KEYCLOAK + NGINX + JWT",
               "OAuth2/OIDC Authentication | TLS Termination | Rate Limiting")

# Keycloak config
bullet_card(slide, Inches(0.3), Inches(1.42), Inches(4.1), Inches(5.2),
            "Keycloak 24 Configuration", [
                "Realm: kerala-police-drone",
                "Client: drone-ingest-client",
                "Auth method: Direct Access Grants (password flow)",
                "Token: JWT (RS256 asymmetric signature)",
                "Access token TTL: 5 minutes",
                "Roles: drone-operator, control-room, supervisor, sys-admin",
                "Users: operator_001 – operator_005 pre-configured",
                "Auto-refresh: 30s before expiry in edge_sender.py",
                "MFA: TOTP enforced in production",
            ], title_color=ACCENT_PURPLE)

bullet_card(slide, Inches(4.7), Inches(1.42), Inches(4.1), Inches(5.2),
            "NGINX Reverse Proxy", [
                "TLS termination: TLSv1.2 + TLSv1.3",
                "Self-signed cert (RSA 2048) via OpenSSL/Docker",
                "Rate limit zone: 30 req/sec per source IP",
                "Burst: 60 requests (nodelay)",
                "Pre-flight: 401 if Authorization header missing",
                "Protects FastAPI from unauthenticated DoS",
                "Upstream: proxy_pass to FastAPI :8000",
                "client_max_body_size: 10MB",
                "proxy_read_timeout: 30 seconds",
            ], title_color=ACCENT_BLUE)

bullet_card(slide, Inches(9.1), Inches(1.42), Inches(4.1), Inches(5.2),
            "JWT Validation Flow", [
                "1. Edge laptop → POST /ingest/frame + Bearer JWT",
                "2. NGINX: check Authorization header present",
                "3. FastAPI: validate_token() dependency injection",
                "4. Fetch Keycloak JWKS (cached) → RS256 verify",
                "5. Extract realm_access.roles from payload",
                "6. Check 'drone-operator' role present → proceed",
                "7. Extract drone_id from metadata + claims match",
                "8. On failure: 401 Unauthorized / 403 Forbidden",
                "9. All events logged for audit trail",
            ], title_color=ACCENT_GREEN)

add_footer(slide, 19)

# ── SLIDE 20: Security Testing Checklist ────────────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "SECURITY TESTING — VALIDATED CHECKLIST",
               "Infrastructure | Authentication | Frame Ingestion | Resilience Tests")

test_groups = [
    ("Infrastructure", [
        ("✓", "Keycloak health: /health/live → 200 OK", ACCENT_GREEN),
        ("✓", "RabbitMQ Management UI accessible", ACCENT_GREEN),
        ("✓", "FastAPI health: /health → {status: ok}", ACCENT_GREEN),
        ("✓", "HTTPS /health with self-signed cert", ACCENT_GREEN),
    ]),
    ("Authentication", [
        ("✓", "Keycloak issues JWT on valid credentials", ACCENT_GREEN),
        ("✓", "No Authorization header → 401 Unauthorized", ACCENT_GREEN),
        ("✓", "Wrong role (control-room) → 403 Forbidden", ACCENT_GREEN),
        ("✓", "Expired token → 401, refresh works correctly", ACCENT_GREEN),
    ]),
    ("Frame Ingestion", [
        ("✓", "Valid frame → HTTP 202 {status: queued}", ACCENT_GREEN),
        ("✓", "RabbitMQ queue shows message after send", ACCENT_GREEN),
        ("✓", "GPU consumer prints batch on receive", ACCENT_GREEN),
        ("✓", "6MB frame → HTTP 413", ACCENT_GREEN),
        ("✓", "PDF as frame → HTTP 415", ACCENT_GREEN),
    ]),
    ("Resilience", [
        ("✓", "RabbitMQ restart → FastAPI auto-reconnects", ACCENT_GREEN),
        ("✓", "API restart → health check recovers", ACCENT_GREEN),
        ("✓", "1100 frames burst → 100 dropped, no crash", ACCENT_GREEN),
        ("✓", "Queue back-pressure: drop-head on overflow", ACCENT_GREEN),
    ]),
]

for i, (group, tests) in enumerate(test_groups):
    cx = Inches(0.3) + i * Inches(3.25)
    add_rect(slide, cx, Inches(1.42), Inches(3.0), Inches(5.25),
             fill_color=BG_CARD, line_color=ACCENT_GREEN, line_width=0.6)
    add_rect(slide, cx, Inches(1.42), Inches(3.0), Inches(0.04), fill_color=ACCENT_GREEN)
    add_text(slide, group, cx + Inches(0.12), Inches(1.48), Inches(2.8), Inches(0.36),
             font_size=13, bold=True, color=ACCENT_GREEN)
    for j, (status, test, color) in enumerate(tests):
        cy = Inches(1.92) + j * Inches(0.85)
        add_rect(slide, cx + Inches(0.12), cy, Inches(0.3), Inches(0.3),
                 fill_color=color)
        add_text(slide, status, cx + Inches(0.12), cy, Inches(0.3), Inches(0.3),
                 font_size=11, bold=True, color=BG_DARK, align=PP_ALIGN.CENTER)
        add_text(slide, test, cx + Inches(0.5), cy, Inches(2.45), Inches(0.7),
                 font_size=10, color=LIGHT_GRAY, wrap=True)

add_footer(slide, 20)

# ── SLIDE 21: RabbitMQ Architecture ──────────────────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "RABBITMQ STREAMING ARCHITECTURE",
               "Per-Drone Queue Isolation | Back-Pressure | GPU Consumer Integration")

add_text(slide,
    "Per-drone isolated durable queues ensure that failures or congestion "
    "from one drone do not affect other active missions.",
    Inches(0.4), Inches(1.38), Inches(12.5), Inches(0.5),
    font_size=13, color=LIGHT_GRAY, wrap=True)

# Queue config
add_rect(slide, Inches(0.3), Inches(1.96), Inches(5.5), Inches(2.5),
         fill_color=RGBColor(0x05, 0x0B, 0x20), line_color=ACCENT_GOLD, line_width=0.6)
add_text(slide, "Queue Configuration (per drone)", Inches(0.42), Inches(2.02),
         Inches(5.3), Inches(0.35), font_size=12, bold=True, color=ACCENT_GOLD)

queue_code = [
    {"text": "await ch.declare_queue(", "size": 10, "color": WHITE},
    {"text": "    'drone.frames.DRONE_KL_001',", "size": 10, "color": ACCENT_CYAN},
    {"text": "    durable=True,", "size": 10, "color": LIGHT_GRAY},
    {"text": "    arguments={", "size": 10, "color": LIGHT_GRAY},
    {"text": "        'x-max-length':  1000,    # Max queued frames", "size": 10, "color": MID_GRAY},
    {"text": "        'x-overflow':    'drop-head',  # Drop oldest", "size": 10, "color": MID_GRAY},
    {"text": "        'x-message-ttl': 30000    # 30s TTL", "size": 10, "color": MID_GRAY},
    {"text": "    }", "size": 10, "color": LIGHT_GRAY},
    {"text": ")", "size": 10, "color": WHITE},
]
add_multiline_text(slide, queue_code, Inches(0.44), Inches(2.42), Inches(5.3), Inches(1.9))

bullet_card(slide, Inches(6.1), Inches(1.96), Inches(6.5), Inches(2.5),
            "Queue Architecture Features", [
                "durable=True: survives RabbitMQ restart",
                "1000 max frames per drone — prevents memory bloat",
                "drop-head: discards oldest on overflow (back-pressure)",
                "30s TTL: frames auto-expire to prevent stale analytics",
                "GPU consumer always gets real-time intelligence",
                "DLQ: dead.frames.{drone_id} for rejected payloads",
            ], title_color=ACCENT_GOLD)

# GPU Consumer
add_rect(slide, Inches(0.3), Inches(4.6), Inches(12.4), Inches(2.05),
         fill_color=BG_CARD, line_color=ACCENT_BLUE, line_width=0.6)
add_text(slide, "GPU Batch Consumer Architecture", Inches(0.42), Inches(4.66),
         Inches(12.2), Inches(0.38), font_size=13, bold=True, color=ACCENT_BLUE)

consumer_points = [
    "aio-pika async consumer (non-blocking)",
    "prefetch_count = BATCH_SIZE = 16 frames",
    "Consumes from all drone.frames.* queues simultaneously",
    "Assembles batch → submits to Triton Inference Server (Phase 3)",
]
for i, pt in enumerate(consumer_points):
    cx = Inches(0.45) + i * Inches(3.1)
    add_rect(slide, cx, Inches(5.1), Inches(2.9), Inches(1.38),
             fill_color=GRID_LINE, line_color=ACCENT_BLUE, line_width=0.3)
    add_text(slide, pt, cx + Inches(0.1), Inches(5.15), Inches(2.7), Inches(1.25),
             font_size=11, color=LIGHT_GRAY, wrap=True)

add_footer(slide, 21)

# ── SLIDE 22: Docker Compose Deployment ──────────────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "DOCKER COMPOSE DEPLOYMENT",
               "Single-Command Startup | 8 Services | Health-Check Ordering")

add_text(slide, "ONE COMMAND DEPLOYMENT: powershell -ExecutionPolicy Bypass -File start-docker.ps1",
         Inches(0.4), Inches(1.42), Inches(12.5), Inches(0.38),
         font_size=13, bold=True, color=ACCENT_GREEN)

# Service table
headers = ["Service", "Image", "Role", "Port(s)", "Status"]
col_x   = [Inches(0.3), Inches(2.6), Inches(4.5), Inches(8.3), Inches(10.4), Inches(12.2)]
col_w   = [Inches(2.2), Inches(1.8), Inches(3.7), Inches(2.0), Inches(1.7)]

# Header row
for j, (hdr, cx, cw) in enumerate(zip(headers, col_x, col_w)):
    add_rect(slide, cx, Inches(1.9), cw, Inches(0.38), fill_color=ACCENT_BLUE)
    add_text(slide, hdr, cx, Inches(1.93), cw, Inches(0.34),
             font_size=11, bold=True, color=WHITE, align=PP_ALIGN.CENTER)

services = [
    ("drone_postgres",     "postgres:15-alpine",          "Keycloak database",                   "internal",        "✅ Healthy"),
    ("drone_keycloak",     "keycloak:24.0",                "JWT auth + user mgmt",                ":8080",           "✅ Healthy"),
    ("drone_rabbitmq",     "rabbitmq:3.13-management",     "Per-drone message queues",             ":5672, :15672",   "✅ Healthy"),
    ("drone_ingest_api",   "Built locally (FastAPI)",      "All 9 API endpoints",                 ":8000 (internal)","✅ Healthy"),
    ("drone_nginx",        "nginx:1.26-alpine",            "HTTPS + Rate limiting",                ":80, :443",       "✅ Healthy"),
    ("drone_analytics",    "Built locally",                "Headless change detection pipeline",  ":9090/metrics",   "✅ Healthy"),
    ("drone_gpu_consumer", "Built locally",                "GPU batch frame consumer",             "—",               "✅ Healthy"),
    ("drone_setup",        "python:3.11-slim",             "One-time init (exits after setup)",   "—",               "✅ Completed"),
]

for i, (svc, image, role, ports, status) in enumerate(services):
    cy = Inches(2.32) + i * Inches(0.58)
    bg = BG_CARD if i % 2 == 0 else RGBColor(0x0B, 0x18, 0x42)
    for j, (text, cx, cw) in enumerate(zip([svc, image, role, ports, status], col_x, col_w)):
        add_rect(slide, cx, cy, cw, Inches(0.55), fill_color=bg)
        clr = ACCENT_CYAN if j == 0 else (ACCENT_GREEN if j == 4 else LIGHT_GRAY)
        add_text(slide, text, cx + Inches(0.05), cy + Inches(0.05),
                 cw - Inches(0.1), Inches(0.48), font_size=10, color=clr)

add_footer(slide, 22)

# ── SLIDE 23: Phase 3 – GPU Analytics Cluster ─────────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "PHASE 3 — GPU ANALYTICS CLUSTER (PLANNED)",
               "Module 4 | Weeks 16–30 | NVIDIA Triton + AI Pipeline | Status: 🔄 In Planning")

add_text(slide,
    "The longest and most technically complex phase. Multi-model deep learning pipeline "
    "running on NVIDIA Triton Inference Server with dynamic batching and model parallelism.",
    Inches(0.4), Inches(1.38), Inches(12.5), Inches(0.6),
    font_size=13, color=LIGHT_GRAY, wrap=True)

# Models
models = [
    ("YOLOv9", "Person & Vehicle Detection", "COCO classes + custom", ACCENT_BLUE),
    ("ByteTrack", "Multi-Object Tracking", "Track IDs across frames", ACCENT_CYAN),
    ("ArcFace", "Face Embedding (512-dim)", "InsightFace / RetinaFace", ACCENT_PURPLE),
    ("CSRNet", "Crowd Density Estimation", "Density map → zone counts", ACCENT_GREEN),
    ("LPRNet", "License Plate OCR", "YOLO plate detect + Tesseract", ACCENT_GOLD),
    ("MobileNetV3", "Re-Identification (128d)", "FAISS cosine similarity", ACCENT_BLUE),
    ("YOLOv9-Vessel", "Vessel Detection", "SeaDronesSee fine-tuned", ACCENT_CYAN),
    ("FusionEngine", "Event Confirmation", "Temporal consistency + YAML thresholds", ACCENT_RED),
]

for i, (model, task, detail, color) in enumerate(models):
    col = i % 4
    row = i // 4
    cx = Inches(0.3) + col * Inches(3.25)
    cy = Inches(2.08) + row * Inches(1.7)
    add_rect(slide, cx, cy, Inches(3.1), Inches(1.55),
             fill_color=BG_CARD, line_color=color, line_width=0.8)
    add_rect(slide, cx, cy, Inches(3.1), Inches(0.04), fill_color=color)
    add_text(slide, model, cx + Inches(0.12), cy + Inches(0.08),
             Inches(2.9), Inches(0.38), font_size=14, bold=True, color=color)
    add_text(slide, task, cx + Inches(0.12), cy + Inches(0.5),
             Inches(2.9), Inches(0.36), font_size=11, color=WHITE)
    add_text(slide, detail, cx + Inches(0.12), cy + Inches(0.88),
             Inches(2.9), Inches(0.6), font_size=10, color=LIGHT_GRAY)

# Triton config
add_rect(slide, Inches(0.3), Inches(5.55), Inches(12.7), Inches(0.98),
         fill_color=BG_CARD, line_color=ACCENT_GOLD, line_width=0.6)
add_text(slide, "NVIDIA Triton Inference Server Config",
         Inches(0.42), Inches(5.61), Inches(4), Inches(0.35),
         font_size=12, bold=True, color=ACCENT_GOLD)
add_text(slide, "Dynamic batching: max=16 | queue delay=100ms | FP16 precision | GPU memory sharing | Concurrent model execution",
         Inches(4.5), Inches(5.61), Inches(8.4), Inches(0.35),
         font_size=11, color=LIGHT_GRAY)
add_text(slide, "Target: GPU util >70% at 20 drones | p95 latency <3s per batch | Tracker state in Redis (mission-scoped TTL)",
         Inches(0.42), Inches(6.0), Inches(12.5), Inches(0.42),
         font_size=11, color=LIGHT_GRAY)

add_footer(slide, 23)

# ── SLIDE 24: Phase 3 – AI Detection Capabilities ────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "PHASE 3 — AI DETECTION CAPABILITIES",
               "7 Analytics Modules | Event Confirmation Engine | Multi-Drone Fusion")

capabilities = [
    ("Person Detection &\nTracking", [
        "YOLOv9 + ByteTrack pipeline",
        "Track IDs: tentative → confirmed → lost",
        "Only confirmed tracks (≥3 frames) emitted",
        "PersonDetectionEvent: track_id, bbox, confidence",
    ], ACCENT_BLUE),
    ("Re-Identification", [
        "MobileNetV3 (128-dim appearance embeddings)",
        "FAISS session-level index per mission",
        "Cosine similarity > 0.7 = same person",
        "Associates persons across disappearance windows",
    ], ACCENT_PURPLE),
    ("Crowd Density", [
        "CSRNet / DM-Count density maps",
        "Zone-based count segmentation (GeoJSON)",
        "CROWDED: >2 persons/m² | CRITICAL: >4 persons/m²",
        "Consistent across ≥5 consecutive frames",
    ], ACCENT_GREEN),
    ("Vehicle Detection &\nLicense Plate", [
        "YOLOv9: car, bus, truck, motorcycle, auto",
        "Speed: pixel displacement × GSD calibration",
        "Colour: K-means on vehicle ROI (HSV)",
        "LPR: YOLO plate + Tesseract/PaddleOCR OCR",
    ], ACCENT_GOLD),
    ("Intrusion & Perimeter\nBreach", [
        "GeoJSON zone polygons per mission",
        "Homography: frame → real-world coordinates",
        "Dwell threshold: >5 seconds in restricted zone",
        "IntrusionEvent: entity, zone_id, entry_ts, dwell",
    ], ACCENT_RED),
    ("Incident Detection", [
        "Sudden stop: speed drops >20→0 km/h in 2 frames",
        "Scatter/cluster pattern detection (fight/incident)",
        "Vehicle overlap: bbox intersection detection",
        "2+ heuristics in 5s window → IncidentEvent emitted",
    ], ACCENT_GOLD),
]

for i, (title, bullets, color) in enumerate(capabilities):
    col = i % 3
    row = i // 3
    cx = Inches(0.3) + col * Inches(4.33)
    cy = Inches(1.42) + row * Inches(2.8)
    bullet_card(slide, cx, cy, Inches(4.1), Inches(2.6),
                title, bullets, title_color=color)

add_footer(slide, 24)

# ── SLIDE 25: Phase 4 – Evidence Object Storage ───────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "PHASE 4 — EVIDENCE OBJECT STORAGE",
               "Module 5 | Weeks 31–34 | MinIO WORM | Legal Compliance")

add_text(slide,
    "Tamper-evident, legally compliant evidence storage using MinIO with "
    "Write Once Read Many (WORM) locking for forensic chain of custody.",
    Inches(0.4), Inches(1.38), Inches(12.5), Inches(0.6),
    font_size=13, color=LIGHT_GRAY, wrap=True)

bullet_card(slide, Inches(0.3), Inches(2.05), Inches(4.1), Inches(4.5),
            "MinIO Deployment", [
                "Distributed mode: 4 drives (scalable)",
                "Bucket: dvas-evidence (WORM locked)",
                "Object Locking: GOVERNANCE mode (90-day default)",
                "Versioning: all versions retained, no silent overwrite",
                "Encryption: SSE-S3 with keys from HashiCorp Vault",
                "Audit logging → forwarded to Loki",
                "Key format: {year}/{month}/{day}/{mission_id}/{evidence_id}.mp4",
            ], title_color=ACCENT_GOLD)

bullet_card(slide, Inches(4.7), Inches(2.05), Inches(4.1), Inches(4.5),
            "Evidence API Endpoints", [
                "POST /v1/evidence/clips — upload MP4 + sidecar JSON",
                "Validate JWT + verify SHA-256 hash on upload",
                "Generate UUID v7 evidence_id (time-sortable)",
                "Apply MinIO WORM lock immediately on upload",
                "GET /v1/evidence/{id}/stream — 15-min presigned URL",
                "GET /v1/evidence/{id}/integrity — re-verify hash",
                "GET /v1/evidence/{id}/metadata — sidecar JSON",
            ], title_color=ACCENT_RED)

bullet_card(slide, Inches(9.1), Inches(2.05), Inches(4.1), Inches(4.5),
            "Retention & Legal Hold", [
                "incident: 180 days (max 3650 = 10 years)",
                "crowd_monitoring: 30 days (max 180)",
                "routine_traffic: 14 days (max 90)",
                "audit_logs: 1825 days (5 years, never delete)",
                "Legal hold → COMPLIANCE mode (admin-proof lock)",
                "Dual SUPERVISOR authorization required to release",
                "All deletions logged with authorizing officer ID",
            ], title_color=ACCENT_GREEN)

add_footer(slide, 25)

# ── SLIDE 26: Databases & Audit Systems ──────────────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "PHASE 5 — DATABASES & AUDIT SYSTEMS",
               "PostgreSQL 16 | TimescaleDB | OpenSearch | Tamper-Evident Audit Log")

bullet_card(slide, Inches(0.3), Inches(1.42), Inches(3.1), Inches(5.2),
            "PostgreSQL Schema", [
                "missions — UUID, status, location_geojson",
                "drones — hardware_serial, last_seen, status",
                "confirmed_events — type, ts, confidence, GPS",
                "entity_tracks — trajectory JSONB, attributes",
                "evidence_items — clip_path, hash, retention",
                "retention_policies — event_type, days, hold",
                "Row-Level Security (RLS) for all tables",
                "Indexes: event_ts, drone_id, mission_id",
            ], title_color=ACCENT_BLUE)

bullet_card(slide, Inches(3.6), Inches(1.42), Inches(3.1), Inches(5.2),
            "TimescaleDB Time-Series", [
                "crowd_density_ts: zone-level density over time",
                "traffic_state_ts: speed, vehicle count per zone",
                "Hypertables for high-performance time queries",
                "Compression: 7-day old chunks → 80%+ space saving",
                "Grafana datasource for live dashboard charts",
                "Chunk-based automatic partitioning",
            ], title_color=ACCENT_CYAN)

bullet_card(slide, Inches(6.9), Inches(1.42), Inches(3.1), Inches(5.2),
            "OpenSearch Full-Text", [
                "Event search by: type, drone, time, GPS radius",
                "geo_point mapping for location-radius queries",
                "RBAC-enforced: users see only authorized missions",
                "Every query logged to audit system",
                "Index mapping: keyword, date, float, geo_point",
                "Single-node Phase 1 → replicas in Phase 3",
            ], title_color=ACCENT_GREEN)

bullet_card(slide, Inches(10.2), Inches(1.42), Inches(3.0), Inches(5.2),
            "Tamper-Evident Audit Log", [
                "append-only table (no UPDATE/DELETE ever)",
                "Fields: actor_id, action, outcome, ip_address",
                "row_hash: MD5 chain (prev_hash + current row)",
                "Modifying any row breaks all subsequent hashes",
                "INSERT-only service account privileges",
                "Dual storage: PostgreSQL + Loki forwarding",
                "Covers: LOGIN, VIEW, EXPORT, DELETE, LEGAL_HOLD",
            ], title_color=ACCENT_GOLD)

add_footer(slide, 26)

# ── SLIDE 27: Authentication & RBAC ──────────────────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "PHASE 6 — AUTHENTICATION & ACCESS CONTROL",
               "Keycloak RBAC + OPA ABAC | 7 Roles | mTLS | Token Revocation")

add_text(slide, "ROLE HIERARCHY", Inches(0.4), Inches(1.42), Inches(12), Inches(0.35),
         font_size=14, bold=True, color=ACCENT_PURPLE)

roles = [
    ("DRONE_OPERATOR",       "Edge dashboard only",                           ACCENT_CYAN),
    ("FIELD_SUPERVISOR",     "Edge + limited ingest ops view",                ACCENT_BLUE),
    ("CONTROL_ROOM_OFFICER", "Central dashboard: active missions own district", ACCENT_BLUE),
    ("SUPERVISOR",           "All missions in jurisdiction + evidence access", ACCENT_GOLD),
    ("SENIOR_OFFICER",       "State-level aggregated view, read-only",        ACCENT_GOLD),
    ("SYSTEM_ADMIN",         "Infra management only — no evidence access",    ACCENT_RED),
    ("COMPLIANCE_OFFICER",   "Audit log + evidence integrity checks",          ACCENT_PURPLE),
]
for i, (role, desc, color) in enumerate(roles):
    cx = Inches(0.3) + (i % 4) * Inches(3.25)
    cy = Inches(1.88) + (i // 4) * Inches(0.6)
    add_rect(slide, cx, cy, Inches(3.1), Inches(0.52),
             fill_color=BG_CARD, line_color=color, line_width=0.6)
    add_text(slide, role, cx + Inches(0.1), cy + Inches(0.04),
             Inches(2.1), Inches(0.44), font_size=10, bold=True, color=color)
    add_text(slide, desc, cx + Inches(2.2), cy + Inches(0.04),
             Inches(0.85), Inches(0.44), font_size=8, color=LIGHT_GRAY)

# RBAC permission matrix
add_text(slide, "PERMISSION MATRIX (summary)", Inches(0.4), Inches(3.22),
         Inches(12), Inches(0.35), font_size=14, bold=True, color=ACCENT_BLUE)

perm_headers = ["Resource", "OPERATOR", "CTRL_ROOM", "SUPERVISOR", "SR_OFFICER", "SYSADMIN"]
perm_col_x   = [Inches(0.3), Inches(3.4), Inches(5.2), Inches(6.9), Inches(8.6), Inches(10.3), Inches(12.0)]
perm_col_w   = [Inches(3.0), Inches(1.7), Inches(1.6), Inches(1.6), Inches(1.6), Inches(1.6)]

for j, (hdr, cx, cw) in enumerate(zip(perm_headers, perm_col_x, perm_col_w)):
    add_rect(slide, cx, Inches(3.62), cw, Inches(0.35), fill_color=ACCENT_BLUE)
    add_text(slide, hdr, cx, Inches(3.65), cw, Inches(0.3),
             font_size=9, bold=True, color=WHITE, align=PP_ALIGN.CENTER)

perms = [
    ("Edge Dashboard",    "✅", "❌", "❌", "❌", "❌"),
    ("Live Alerts",       "❌", "✅ own", "✅ own", "✅ all", "❌"),
    ("View Evidence",     "❌", "✅ active", "✅ all", "✅ read", "❌"),
    ("Export Evidence",   "❌", "❌", "✅ approved", "✅", "❌"),
    ("Apply Legal Hold",  "❌", "❌", "✅", "✅", "❌"),
    ("Audit Logs",        "❌", "❌", "✅ own unit", "✅ all", "❌"),
    ("User Management",   "❌", "❌", "❌", "❌", "✅"),
]
for i, (res, *vals) in enumerate(perms):
    cy = Inches(4.0) + i * Inches(0.42)
    bg = BG_CARD if i % 2 == 0 else GRID_LINE
    row_data = [res] + vals
    for j, (text, cx, cw) in enumerate(zip(row_data, perm_col_x, perm_col_w)):
        add_rect(slide, cx, cy, cw, Inches(0.4), fill_color=bg)
        clr = ACCENT_GREEN if "✅" in text else (LIGHT_GRAY if j == 0 else ACCENT_RED)
        add_text(slide, text, cx + Inches(0.04), cy + Inches(0.04),
                 cw - Inches(0.08), Inches(0.34), font_size=9,
                 color=clr, align=PP_ALIGN.CENTER if j > 0 else PP_ALIGN.LEFT)

add_footer(slide, 27)

# ── SLIDE 28: Dashboards ──────────────────────────────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "PHASE 7 — DASHBOARDS & COMMAND INTERFACES",
               "React 18 + TypeScript | Leaflet Maps | WebSocket Real-Time | Role-Based Views")

bullet_card(slide, Inches(0.3), Inches(1.42), Inches(4.1), Inches(5.2),
            "Central Command Dashboard", [
                "React 18 + TypeScript + Vite + shadcn/ui",
                "Live alerts panel with severity color coding",
                "Multi-drone Leaflet map with GPS markers",
                "Drone FOV polygon + event markers (clustered)",
                "Crowd density heatmap + traffic state timeline",
                "Entity tracking history: full trajectory on map",
                "WebSocket real-time event updates from Kafka",
                "Sound alert for CRITICAL events (browser API)",
            ], title_color=ACCENT_BLUE)

bullet_card(slide, Inches(4.7), Inches(1.42), Inches(4.1), Inches(5.2),
            "Evidence Playback & Reports", [
                "Video.js player with presigned MinIO URLs",
                "Synchronized bounding box overlay on video",
                "Event timeline synchronized to scrubber",
                "Export clip (SUPERVISOR approval required)",
                "Integrity badge (SHA-256 verification status)",
                "WeasyPrint PDF: mission summary reports",
                "Chain of custody PDF with QR verification code",
                "Audit trail export for court submissions",
            ], title_color=ACCENT_CYAN)

bullet_card(slide, Inches(9.1), Inches(1.42), Inches(4.1), Inches(5.2),
            "Admin & Compliance", [
                "SYSTEM_ADMIN: user management via Keycloak API",
                "Mission management: create, configure, close",
                "Zone config: draw GeoJSON polygons on Leaflet",
                "Retention policy management (dual-auth changes)",
                "COMPLIANCE_OFFICER: full audit log view/export",
                "Flag anomalies: bulk access, off-hours access",
                "Playwright automated browser testing per role",
                "WCAG 2.1 AA accessibility compliance",
            ], title_color=ACCENT_GREEN)

add_footer(slide, 28)

# ── SLIDE 29: Roadmap & Milestones ────────────────────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "22-MONTH PRODUCTION ROADMAP",
               "Phase Gates | Delivery Timeline | 2-Engineer Team")

phases_timeline = [
    ("Phase 0", "Weeks 1–3",   "Foundation & Environment",          100, ACCENT_GREEN),
    ("Phase 1", "Weeks 4–10",  "Edge Processing Layer (M2)",         100, ACCENT_GREEN),
    ("Phase 2", "Weeks 11–15", "Central Ingest Server (M3)",         100, ACCENT_GREEN),
    ("Phase 3", "Weeks 16–30", "GPU Analytics Cluster (M4)",           5, ACCENT_GOLD),
    ("Phase 4", "Weeks 31–34", "Evidence Object Storage (M5)",         0, ACCENT_BLUE),
    ("Phase 5", "Weeks 35–38", "Databases & Audit Systems (M6)",       0, ACCENT_BLUE),
    ("Phase 6", "Weeks 39–43", "Authentication & RBAC (M7)",           0, ACCENT_BLUE),
    ("Phase 7", "Weeks 44–52", "Dashboards (M8)",                      0, ACCENT_BLUE),
    ("Phase 8", "Weeks 53–58", "Legal Compliance & CoC (M9)",          0, ACCENT_BLUE),
    ("Phase 9", "Weeks 59–66", "Integration, Load Test, Pilot",        0, ACCENT_PURPLE),
    ("Phase 10","Weeks 67–88", "Monitoring, Hardening & Handover",     0, ACCENT_PURPLE),
]

for i, (phase, weeks, desc, pct, color) in enumerate(phases_timeline):
    cy = Inches(1.45) + i * Inches(0.56)
    add_text(slide, phase, Inches(0.3), cy, Inches(0.85), Inches(0.48),
             font_size=10, bold=True, color=color)
    add_text(slide, weeks, Inches(1.15), cy, Inches(1.1), Inches(0.48),
             font_size=9, color=MID_GRAY)
    add_text(slide, desc, Inches(2.35), cy, Inches(4.5), Inches(0.48),
             font_size=10, color=LIGHT_GRAY)
    progress_bar(slide, Inches(6.95), cy + Inches(0.1), Inches(5.0), Inches(0.28),
                 pct, bar_color=color, label=f"{pct}%" if pct > 0 else "Planned")

# Gate criteria
add_rect(slide, Inches(0.3), Inches(7.68 - 1.5), Inches(12.7), Inches(1.35),
         fill_color=BG_CARD, line_color=ACCENT_GREEN, line_width=0.6)
add_text(slide, "PHASE GATES COMPLETED ✅", Inches(0.42), Inches(6.22),
         Inches(12), Inches(0.35), font_size=13, bold=True, color=ACCENT_GREEN)

gates = [
    "Phase 0: All machines provisioned, CI/CD green, model baselines documented",
    "Phase 1: Rolling buffer 2h+ leak-free, change detection on 3 test videos, cognitive selector ≤20 FPM",
    "Phase 2: 20 simulated drones zero-dropped frames, p95 latency <100ms, ops dashboard live",
]
for i, gate in enumerate(gates):
    add_text(slide, f"▸  {gate}", Inches(0.42), Inches(6.62) + i * Inches(0.3),
             Inches(12.5), Inches(0.28), font_size=10, color=LIGHT_GRAY)

add_footer(slide, 29)

# ── SLIDE 30: Summary & Next Steps ───────────────────────────────────────────
slide = prs.slides.add_slide(BLANK_LAYOUT)
set_bg(slide, BG_DARK)
add_header_bar(slide, "SUMMARY & NEXT STEPS",
               "What's Done | What's Next | Key Risks | Call to Action")

# Accomplished
add_rect(slide, Inches(0.3), Inches(1.42), Inches(6.0), Inches(2.5),
         fill_color=BG_CARD, line_color=ACCENT_GREEN, line_width=0.8)
add_rect(slide, Inches(0.3), Inches(1.42), Inches(6.0), Inches(0.04), fill_color=ACCENT_GREEN)
add_text(slide, "✅  ACCOMPLISHED (Phases 0–2)", Inches(0.42), Inches(1.48),
         Inches(5.8), Inches(0.38), font_size=14, bold=True, color=ACCENT_GREEN)
accomplished = [
    "5 modules (M1–M5) fully implemented and tested",
    "8-service Docker Compose stack operational",
    "Edge pipeline: video capture → change detection → cognitive selector",
    "Central ingest: NGINX + FastAPI + Keycloak + RabbitMQ + GPU consumer",
    "Security: TLS 1.3, JWT RS256, role-based access, rate limiting",
    "All phase gate reviews passed with documented evidence",
]
for i, item in enumerate(accomplished):
    add_text(slide, f"  ▸  {item}", Inches(0.42), Inches(1.92) + i * Inches(0.33),
             Inches(5.7), Inches(0.32), font_size=10, color=LIGHT_GRAY)

# Next steps
add_rect(slide, Inches(6.8), Inches(1.42), Inches(6.0), Inches(2.5),
         fill_color=BG_CARD, line_color=ACCENT_GOLD, line_width=0.8)
add_rect(slide, Inches(6.8), Inches(1.42), Inches(6.0), Inches(0.04), fill_color=ACCENT_GOLD)
add_text(slide, "🚀  IMMEDIATE NEXT STEPS (Phase 3)", Inches(6.92), Inches(1.48),
         Inches(5.8), Inches(0.38), font_size=14, bold=True, color=ACCENT_GOLD)
nextsteps = [
    "Deploy NVIDIA Triton Inference Server on GPU node",
    "Export YOLOv9 → ONNX → TensorRT (FP16 optimized)",
    "Implement PersonTracker + ByteTrack integration",
    "Implement Crowd Density (CSRNet) on batch frames",
    "Build Event Confirmation Engine with YAML config",
    "Achieve >70% GPU utilization under 20-drone load",
]
for i, item in enumerate(nextsteps):
    add_text(slide, f"  ▸  {item}", Inches(6.92), Inches(1.92) + i * Inches(0.33),
             Inches(5.7), Inches(0.32), font_size=10, color=LIGHT_GRAY)

# Risks
add_rect(slide, Inches(0.3), Inches(4.05), Inches(6.0), Inches(2.1),
         fill_color=BG_CARD, line_color=ACCENT_RED, line_width=0.8)
add_rect(slide, Inches(0.3), Inches(4.05), Inches(6.0), Inches(0.04), fill_color=ACCENT_RED)
add_text(slide, "⚠  KEY RISKS & MITIGATIONS", Inches(0.42), Inches(4.11),
         Inches(5.8), Inches(0.38), font_size=14, bold=True, color=ACCENT_RED)
risks = [
    "GPU latency: FP16 + dynamic batching + pipeline parallelism",
    "False positives: MOG2 tuning + temporal fusion (≥3 frame consistency)",
    "Evidence tamper: WORM lock + SHA-256 + dual auth required",
    "Real telemetry (Phase 3): replace mocked GPS with MAVLink SDK",
    "Scale (Phase 9): migrate RabbitMQ → Apache Kafka 3.x for 20+ drones",
]
for i, item in enumerate(risks):
    add_text(slide, f"  ▸  {item}", Inches(0.42), Inches(4.51) + i * Inches(0.32),
             Inches(5.7), Inches(0.3), font_size=10, color=LIGHT_GRAY)

# CTA
add_rect(slide, Inches(6.8), Inches(4.05), Inches(6.0), Inches(2.1),
         fill_color=BG_CARD, line_color=ACCENT_BLUE, line_width=0.8)
add_rect(slide, Inches(6.8), Inches(4.05), Inches(6.0), Inches(0.04), fill_color=ACCENT_BLUE)
add_text(slide, "📋  SUPPORT REQUESTED", Inches(6.92), Inches(4.11),
         Inches(5.8), Inches(0.38), font_size=14, bold=True, color=ACCENT_BLUE)
ctas = [
    "GPU server provisioning for Triton (NVIDIA A100 preferred)",
    "Field laptop deployment with Ansible playbook validation",
    "Keycloak production realm migration to dedicated server",
    "Access to real DJI drone SDK for live telemetry (vs. mock)",
    "Sign-off on Section 65B evidence certification requirements",
]
for i, item in enumerate(ctas):
    add_text(slide, f"  ▸  {item}", Inches(6.92), Inches(4.51) + i * Inches(0.32),
             Inches(5.7), Inches(0.3), font_size=10, color=LIGHT_GRAY)

# Bottom brand block
add_rect(slide, 0, H - Inches(0.9), W, Inches(0.9), fill_color=BG_CARD)
add_rect(slide, 0, H - Inches(0.9), W, Pt(1.5), fill_color=ACCENT_GOLD)
add_text(slide, "Drone Video Analytics System — DVAS",
         Inches(0.4), H - Inches(0.82), Inches(6), Inches(0.38),
         font_size=16, bold=True, color=WHITE)
add_text(slide, "Quantumweave Intelligence × Kerala Police Department | Confidential",
         Inches(0.4), H - Inches(0.47), Inches(8), Inches(0.32),
         font_size=11, color=MID_GRAY)
add_text(slide, "June 2026",
         Inches(11), H - Inches(0.82), Inches(2.1), Inches(0.38),
         font_size=14, color=ACCENT_GOLD, bold=True, align=PP_ALIGN.RIGHT)

add_footer(slide, 30)

# ══════════════════════════════════════════════════════════════════════════════
# SAVE
# ══════════════════════════════════════════════════════════════════════════════
output_path = r'D:\POLICE_DRONE\drone_analytics\DVAS_Progress_Presentation_30Slides.pptx'
prs.save(output_path)
print(f"✅  Saved: {output_path}")
print(f"   Slides: {len(prs.slides)}")
