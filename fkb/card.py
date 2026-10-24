"""Render an Albion-style PNG kill card with Pillow. Network is injectable; failures fall back to placeholders."""
import io, os, time, urllib.request
from PIL import Image, ImageDraw, ImageFont
from . import feed

W = 900
BG, PANEL, TXT, DIM, GOLD = (24, 27, 31), (36, 40, 46), (235, 235, 235), (150, 155, 162), (240, 200, 90)
RULE = {"kill": (46, 204, 113), "loss": (231, 76, 60), "neutral": (127, 140, 141)}
SEC = {"core": ((46, 160, 90), "CORE · SAFE"), "marches": ((220, 150, 40), "MARCHES · CONTESTED"),
       "deep": ((200, 50, 50), "DEEP · LAWLESS")}
FONT_DIR = "/usr/share/fonts/truetype/dejavu"


def _font(size, bold=False):
    try:
        return ImageFont.truetype(os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"), size)
    except OSError:
        return ImageFont.load_default(size)


def _http_bytes(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": feed.UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def ship_image(ship_id, cache_dir, fetch=_http_bytes):
    """Return a PIL image for a ship, cached on disk, or None on any failure."""
    if not ship_id or not ship_id.replace("-", "").replace("_", "").isalnum():
        return None
    path = os.path.join(cache_dir, f"{ship_id}.webp") if cache_dir else None
    try:
        if path and os.path.exists(path):
            return Image.open(path).convert("RGBA")
        if fetch is None:
            return None
        data = fetch(f"{feed.BASE}/assets/portraits/killmails/feed/{ship_id}.webp")
        img = Image.open(io.BytesIO(data)).convert("RGBA")
        if path:
            os.makedirs(cache_dir, exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
        return img
    except Exception:
        return None


def _fit(img, box_w, box_h):
    img = img.copy()
    img.thumbnail((box_w, box_h))
    return img


def _ptxt(p):
    p = p or {}
    corp = p.get("corporation") or {}
    return p.get("callsign") or "Unknown", (f"[{corp['ticker']}] {corp.get('name','')}".strip() if corp.get("ticker") else "No corp")


def _ship_box(d, im, x, y, w, h, ship, img):
    d.rounded_rectangle([x, y, x + w, y + h], 10, fill=PANEL, outline=(70, 75, 82))
    if img is not None:
        t = _fit(img, w - 16, h - 16)
        im.paste(t, (x + (w - t.width) // 2, y + (h - t.height) // 2), t)
    else:
        d.text((x + w // 2, y + h // 2), (ship or {}).get("hullClass", "ship").upper(), fill=DIM, font=_font(16, True), anchor="mm")
    tier = (ship or {}).get("tier")
    if tier:
        d.rectangle([x, y, x + 34, y + 22], fill=(0, 0, 0))
        d.text((x + 17, y + 11), f"T{tier}", fill=TXT, font=_font(13, True), anchor="mm")


def render_card(km, kind="neutral", cache_dir=None, fetch=_http_bytes, footer="DEAD Syndicate · DXD killboard"):
    v = km.get("victim") or {}
    fb = km.get("finalBlow") or {}
    atts = sorted(km.get("attackers") or [], key=lambda a: -(a.get("damage") or 0))
    killer = fb.get("pilot") or (atts[0]["pilot"] if atts else {})
    kship = fb.get("ship") or (atts[0].get("ship") if atts else {})
    loc = km.get("location") or {}
    n_bars = max(1, min(len(atts), 8))
    H = 550 + n_bars * 34 + (24 if len(atts) > 8 else 0)
    im = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(im)
    f_s, f_m, f_l, f_xl = _font(14), _font(17), _font(26, True), _font(30, True)

    kn, kc = _ptxt(killer)
    vn, vc = _ptxt(v.get("pilot"))
    d.text((24, 16), kc, fill=DIM, font=f_m)
    d.text((24, 40), kn, fill=TXT, font=f_l)
    d.text((24, 74), "FINAL BLOW", fill=DIM, font=f_s)
    d.text((W - 24, 16), vc, fill=DIM, font=f_m, anchor="ra")
    d.text((W - 24, 40), vn, fill=TXT, font=f_l, anchor="ra")
    d.text((W - 24, 74), "VICTIM", fill=DIM, font=f_s, anchor="ra")
    ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime((km.get("occurredAt") or 0) / 1000))
    d.text((W // 2, 26), ts, fill=DIM, font=f_m, anchor="mm")
    d.rectangle([0, 98, W, 103], fill=RULE.get(kind, RULE["neutral"]))

    # ships
    bw, bh, by = 250, 170, 122
    _ship_box(d, im, 24, by, bw, bh, kship, ship_image((kship or {}).get("shipId"), cache_dir, fetch))
    _ship_box(d, im, W - 24 - bw, by, bw, bh, v.get("ship"), ship_image((v.get("ship") or {}).get("shipId"), cache_dir, fetch))
    d.text((24 + bw // 2, by + bh + 14), feed._ship(kship), fill=TXT, font=f_s, anchor="mm")
    d.text((W - 24 - bw // 2, by + bh + 14), feed._ship(v.get("ship")), fill=TXT, font=f_s, anchor="mm")

    cx = W // 2
    for i, (lab, val, col) in enumerate((("PARTY", str(km.get("attackerCount", len(atts))), TXT),
                                          ("VALUE DESTROYED", f"{feed._num(km.get('totalValue'))} cr", GOLD),
                                          ("KILL ID", km.get("id", "")[:8], DIM))):
        y = by + 4 + i * 62
        d.text((cx, y), lab, fill=DIM, font=f_s, anchor="mt")
        d.text((cx, y + 20), val, fill=col, font=f_l if i < 2 else f_m, anchor="mt")

    # location block
    ly = 330
    d.rounded_rectangle([24, ly, W - 24, ly + 110], 12, fill=PANEL, outline=(80, 86, 94))
    d.text((44, ly + 14), "LOCATION — HUNT HERE", fill=DIM, font=f_s)
    d.text((44, ly + 36), loc.get("systemName") or "Unknown system", fill=TXT, font=f_xl)
    sub = " · ".join(x for x in (loc.get("poiName"), loc.get("regionName")) if x)
    d.text((44, ly + 76), sub or "—", fill=DIM, font=f_m)
    band = (loc.get("securityBand") or "").lower()
    col, label = SEC.get(band, ((110, 110, 110), (band or "unknown").upper()))
    tw = d.textlength(label, font=_font(16, True)) + 32
    d.rounded_rectangle([W - 44 - tw, ly + 40, W - 44, ly + 76], 18, fill=col)
    d.text((W - 44 - tw / 2, ly + 58), label, fill=(255, 255, 255), font=_font(16, True), anchor="mm")

    # damage bars
    dy = ly + 135
    d.text((24, dy), "Damage", fill=DIM, font=f_m)
    top = max([a.get("damage") or 0 for a in atts] + [1])
    total = sum(a.get("damage") or 0 for a in atts) or 1
    fbid = (killer or {}).get("pilotId")
    for i, a in enumerate(atts[:8]):
        y = dy + 28 + i * 34
        dmg = a.get("damage") or 0
        d.rectangle([24, y, 24 + 500, y + 12], fill=PANEL)
        d.rectangle([24, y, 24 + int(500 * dmg / top), y + 12], fill=(192, 57, 43))
        name, _ = _ptxt(a.get("pilot"))
        tk = ((a.get("pilot") or {}).get("corporation") or {}).get("ticker")
        star = " ★" if (a.get("pilot") or {}).get("pilotId") == fbid else ""
        d.text((24, y + 15), f"{name}{' [' + tk + ']' if tk else ''}{star}", fill=TXT, font=f_s)
        d.text((540, y + 2), f"{int(dmg):,}  ({100 * dmg / total:.0f}%)", fill=(230, 120, 110), font=f_s)
        d.text((W - 24, y + 2), feed._ship(a.get("ship")), fill=DIM, font=f_s, anchor="ra")
    if not atts:
        d.text((24, dy + 30), "No attacker data", fill=DIM, font=f_s)
    if len(atts) > 8:
        d.text((24, dy + 28 + 8 * 34), f"+{len(atts) - 8} more", fill=DIM, font=f_s)

    d.text((24, H - 30), footer, fill=DIM, font=f_s)
    d.text((W - 24, H - 30), f"Total {feed._num(km.get('totalValue'))} cr", fill=GOLD, font=f_m, anchor="ra")
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "PNG", optimize=True)
    return buf.getvalue()
