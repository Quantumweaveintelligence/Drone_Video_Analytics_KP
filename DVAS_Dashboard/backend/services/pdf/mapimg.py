"""
Offline report maps (R3 / Q7). No request leaves the system.

Points (and optional polygons) are projected in Web Mercator and drawn with
Pillow: graticule with lat/lon labels, scale bar, north arrow and a legend.
If REPORT_BASEMAP_MBTILES names a raster .mbtiles file (e.g. a Kerala extract
rendered offline), its tiles are composed underneath; otherwise the background
is plain. OSM tiles are never fetched at generation time: that would send
incident locations to a third party and break the OSM tile usage policy.
"""
import io
import math
import os
import sqlite3
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from core.config import REPORT_BASEMAP_MBTILES
from services.pdf.document import FONT_DIR

W, H = 1600, 1000
KIND_RGB = {"evidence": (40, 90, 200), "alert": (200, 50, 50), "intrusion": (220, 110, 20),
            "theft": (150, 40, 160), "crowd": (20, 140, 90)}


def _merc(lat: float, lon: float) -> Tuple[float, float]:
    lat = max(min(lat, 85.0), -85.0)
    x = (lon + 180.0) / 360.0
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2
    return x, y


def _inv(x: float, y: float) -> Tuple[float, float]:
    lon = x * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y))))
    return lat, lon


def _extent(points: Sequence[dict]):
    xs, ys = zip(*(_merc(p["lat"], p["lon"]) for p in points))
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    min_span = 2e-5                                   # ≈ 800 m at Kerala's latitude
    span = max(x1 - x0, (y1 - y0) * W / H, min_span) * 1.25
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return cx - span / 2, cy - span * H / W / 2, span, span * H / W


def _basemap(x0, y0, sw, sh) -> Optional[Image.Image]:
    path = REPORT_BASEMAP_MBTILES
    if not path or not os.path.isfile(path):
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        zooms = [r[0] for r in con.execute("SELECT DISTINCT zoom_level FROM tiles ORDER BY 1")]
        if not zooms:
            return None
        z = zooms[0]
        for cand in zooms:                            # deepest zoom that keeps the mosaic ≤ ~2× output
            if sw * 256 * (2 ** cand) <= W * 2:
                z = cand
        n = 2 ** z
        tx0, tx1 = int(x0 * n), int((x0 + sw) * n)
        ty0, ty1 = int(y0 * n), int((y0 + sh) * n)
        if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) > 64:
            return None
        mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256), (238, 238, 232))
        for tx in range(tx0, tx1 + 1):
            for ty in range(ty0, ty1 + 1):
                row = con.execute("SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                                  (z, tx, n - 1 - ty)).fetchone()          # MBTiles rows are TMS
                if row:
                    tile = Image.open(io.BytesIO(row[0])).convert("RGB")
                    mosaic.paste(tile, ((tx - tx0) * 256, (ty - ty0) * 256))
        con.close()
        left, top = (x0 * n - tx0) * 256, (y0 * n - ty0) * 256
        crop = mosaic.crop((int(left), int(top), int(left + sw * n * 256), int(top + sh * n * 256)))
        return crop.resize((W, H))
    except Exception:
        return None


def _font(size: int):
    try:
        return ImageFont.truetype(os.path.join(FONT_DIR, "NotoSans-Regular.ttf"), size)
    except OSError:
        return ImageFont.load_default()


def _nice_step(span_deg: float) -> float:
    raw = span_deg / 5
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def render_map(points: Sequence[dict], polygons: Sequence[Sequence[Tuple[float, float]]] = ()) -> Tuple[Optional[bytes], str]:
    """points: [{"lat", "lon", "kind", "label"}]. Returns (png_bytes, caption)."""
    pts = [p for p in points if p.get("lat") is not None and p.get("lon") is not None]
    if not pts:
        return None, ""
    x0, y0, sw, sh = _extent(pts)

    def px(lat, lon):
        x, y = _merc(lat, lon)
        return (x - x0) / sw * W, (y - y0) / sh * H

    base = _basemap(x0, y0, sw, sh)
    img = base if base is not None else Image.new("RGB", (W, H), (247, 248, 250))
    d = ImageDraw.Draw(img)
    f_small, f_med = _font(20), _font(26)

    # graticule
    lat_top, lon_left = _inv(x0, y0)
    lat_bot, lon_right = _inv(x0 + sw, y0 + sh)
    step = _nice_step(lon_right - lon_left)
    lon = math.ceil(lon_left / step) * step
    while lon < lon_right:
        x, _ = px(lat_top, lon)
        d.line([(x, 0), (x, H)], fill=(205, 210, 220), width=1)
        d.text((x + 4, H - 28), f"{lon:.4f}°E", fill=(110, 110, 120), font=f_small)
        lon += step
    lat = math.ceil(lat_bot / step) * step
    while lat < lat_top:
        _, y = px(lat, lon_left)
        d.line([(0, y), (W, y)], fill=(205, 210, 220), width=1)
        d.text((6, y + 4), f"{lat:.4f}°N", fill=(110, 110, 120), font=f_small)
        lat += step

    for poly in polygons:
        if len(poly) >= 3:
            d.polygon([px(la, lo) for la, lo in poly], outline=(200, 40, 40), width=3)

    counts: Dict[str, int] = {}
    for p in pts:
        x, y = px(p["lat"], p["lon"])
        rgb = KIND_RGB.get(p.get("kind"), (60, 60, 60))
        d.ellipse([x - 9, y - 9, x + 9, y + 9], fill=rgb, outline=(255, 255, 255), width=2)
        counts[p.get("kind") or "other"] = counts.get(p.get("kind") or "other", 0) + 1

    # scale bar (metres at the map centre)
    c_lat, _ = _inv(x0 + sw / 2, y0 + sh / 2)
    metres_per_px = 40075016.686 * math.cos(math.radians(c_lat)) * sw / W
    target = metres_per_px * W / 5
    nice = 10 ** math.floor(math.log10(target))
    for m in (5, 2, 1):
        if m * nice <= target:
            nice *= m
            break
    bar_px = nice / metres_per_px
    d.rectangle([W - 40 - bar_px, H - 60, W - 40, H - 50], fill=(30, 30, 30))
    label = f"{nice / 1000:g} km" if nice >= 1000 else f"{nice:g} m"
    d.text((W - 40 - bar_px, H - 95), label, fill=(30, 30, 30), font=f_med)
    # north arrow
    d.polygon([(W - 60, 30), (W - 75, 75), (W - 45, 75)], fill=(30, 30, 30))
    d.text((W - 69, 80), "N", fill=(30, 30, 30), font=f_med)
    # legend
    y = 20
    for kind, n in counts.items():
        d.ellipse([20, y + 4, 38, y + 22], fill=KIND_RGB.get(kind, (60, 60, 60)))
        d.text((46, y), f"{kind} ({n})", fill=(30, 30, 30), font=f_med)
        y += 34
    d.rectangle([0, 0, W - 1, H - 1], outline=(180, 180, 180), width=2)

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    source = "offline base map" if base is not None else "no base map (coordinates only)"
    caption = (f"{len(pts)} located events. Web Mercator, {source}; centre {c_lat:.4f}°N. "
               "No map data is fetched from outside DVAS.")
    return buf.getvalue(), caption
