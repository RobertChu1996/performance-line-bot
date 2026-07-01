import os
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(BASE_DIR, "template.png")
PHOTOS_DIR = os.path.join(BASE_DIR, "photos")
OUTPUT_DIR = os.path.join(BASE_DIR, "static", "posters")

# Pixel positions for 706x1000 template — adjust if layout shifts
DATE_POS        = (353, 95)    # date text center
TITLE_HEAD_POS  = (353, 160)   # "本日業績王" center
CIRCLE_CENTER   = (344, 477)   # headshot circle center (measured)
CIRCLE_RADIUS   = 188          # slightly larger than template radius 185
NAME_POS        = (353, 718)   # ribbon center
TITLE_POS       = (353, 800)   # job title center

DATE_COLOR  = "#FFD700"
NAME_COLOR  = "#FFD700"
TITLE_COLOR = "#FFFFFF"
STROKE_COLOR = "#000000"


def get_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",     # Render
        "/usr/share/fonts/truetype/noto/NotoSansCJKtc-Bold.otf",   # Render alt
        "/System/Library/Fonts/PingFang.ttc",                       # macOS
        "/System/Library/Fonts/STHeiti Medium.ttc",                 # macOS fallback
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def draw_centered(draw, text, center, font, color, stroke=2):
    x, y = center
    bb = draw.textbbox((0, 0), text, font=font)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    draw.text(
        (x - tw // 2, y - th // 2),
        text,
        font=font,
        fill=color,
        stroke_width=stroke,
        stroke_fill=STROKE_COLOR,
    )


def find_photo_and_title(name: str):
    """Find photo file starting with name, extract title from filename."""
    for fname in os.listdir(PHOTOS_DIR):
        stem, ext = os.path.splitext(fname)
        if ext.lower() not in (".jpg", ".jpeg", ".png"):
            continue
        if stem.startswith(name):
            title = stem[len(name):]  # everything after the name = title
            return os.path.join(PHOTOS_DIR, fname), title or ""
    return None, None


def generate_poster(name: str, title: str = "", date_str: str = None):
    if date_str is None:
        date_str = datetime.now().strftime("%Y.%m.%d")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    template = Image.open(TEMPLATE_PATH).convert("RGBA")

    # Find photo; title from filename overrides if not manually specified
    photo_path, file_title = find_photo_and_title(name)
    if photo_path is None:
        return None, None
    if not title:
        title = file_title

    return_title = title  # for app.py to use in announcement text

    from PIL import ImageOps
    # Center-crop to square, resize to fill circle
    r = CIRCLE_RADIUS
    raw = Image.open(photo_path).convert("RGB")
    photo = ImageOps.fit(raw, (r * 2, r * 2), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (r * 2, r * 2), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, r * 2, r * 2), fill=255)
    photo.putalpha(mask)

    cx, cy = CIRCLE_CENTER
    template.paste(photo, (cx - r, cy - r), photo)

    # Draw text
    draw = ImageDraw.Draw(template)
    draw_centered(draw, date_str,    DATE_POS,       get_font(52), DATE_COLOR)
    draw_centered(draw, "本日業績王", TITLE_HEAD_POS, get_font(58), DATE_COLOR)
    draw_centered(draw, name,        NAME_POS,       get_font(62), NAME_COLOR)
    draw_centered(draw, title,       TITLE_POS,      get_font(48), TITLE_COLOR)

    out = os.path.join(OUTPUT_DIR, f"winner_{date_str.replace('.', '')}.png")
    template.convert("RGB").save(out)
    return out, return_title
