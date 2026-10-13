"""Draws the 3 synthetic rate cards used by the demo and eval/ratecards.py. Usage: uv run scripts/make_ratecards.py"""
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).parent.parent / "bookings" / "static" / "ratecards"
F = "/usr/share/fonts/truetype/"


def font(name, size):
    return ImageFont.truetype(F + name, size)


def mixed(d, xy, text, size, fill, right=False):
    """Draw Hindi + English on one line: Noto Devanagari has no Latin glyphs, so switch font per run."""
    import re
    runs = re.findall(r"[\u0900-\u097F]+(?:\s+[\u0900-\u097F]+)*|[^\u0900-\u097F]+", text)
    fonts = [font("noto/NotoSansDevanagari-Regular.ttf" if "\u0900" <= r[0] <= "\u097F" else "ubuntu/UbuntuSans[wdth,wght].ttf", size) for r in runs]
    x, y = xy
    if right:
        x -= sum(d.textlength(r, font=f) for r, f in zip(runs, fonts))
    for r, f in zip(runs, fonts):
        d.text((x, y), r, font=f, fill=fill)
        x += d.textlength(r, font=f)


def menu_card():
    """Printed clinic fee card: sections, dotted leaders, an 'onwards' price."""
    img = Image.new("RGB", (900, 1250), "#f3f7f8")
    d = ImageDraw.Draw(img)
    d.text((450, 70), "ALIGNRIGHT", font=font("dejavu/DejaVuSerif-Bold.ttf", 56), fill="#0d4f63", anchor="mm")
    d.text((450, 130), "Chiropractic & Physio · Whitefield · Fees", font=font("dejavu/DejaVuSans.ttf", 26), fill="#555", anchor="mm")
    sections = [
        ("CONSULTATION", [("New patient consultation", "₹ 1500"), ("Follow-up adjustment", "₹ 1000")]),
        ("TREATMENTS", [("Spinal decompression", "₹ 1800 onwards"), ("Dry needling", "₹ 800"), ("Kinesio taping", "₹ 500"),
                        ("Sports injury session", "₹ 2000")]),
        ("SPECIAL CARE", [("Kids chiropractic", "₹ 900"), ("Pregnancy care", "₹ 1400")]),
    ]
    y, item, head = 210, font("dejavu/DejaVuSans.ttf", 32), font("dejavu/DejaVuSans-Bold.ttf", 34)
    for title, rows in sections:
        d.text((80, y), title, font=head, fill="#0d4f63"); y += 60
        for name, price in rows:
            d.text((100, y), name, font=item, fill="#222")
            w = d.textlength(price, font=item)
            d.text((820 - w, y), price, font=item, fill="#222")
            x0, x1 = 110 + d.textlength(name, font=item), 800 - w
            for x in range(int(x0), int(x1), 14):
                d.ellipse((x, y + 26, x + 3, y + 29), fill="#aaa")
            y += 56
        y += 30
    d.text((450, 1190), "Please carry previous X-ray / MRI reports", font=font("dejavu/DejaVuSans.ttf", 22), fill="#777", anchor="mm")
    return img


def table_card():
    """Fee schedule with a printed Duration column."""
    img = Image.new("RGB", (1000, 900), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 1000, 120), fill="#2d4a7a")
    d.text((500, 60), "BackFirst Chiropractic — Fee Schedule", font=font("liberation/LiberationSans-Bold.ttf", 46), fill="white", anchor="mm")
    cols, f, fb = (60, 600, 800), font("liberation/LiberationSans-Regular.ttf", 34), font("liberation/LiberationSans-Bold.ttf", 34)
    y = 170
    for x, t in zip(cols, ("Service", "Duration", "Fee (₹)")):
        d.text((x, y), t, font=fb, fill="#2d4a7a")
    rows = [("Initial Assessment", "60 min", "2000"), ("Adjustment", "20 min", "1200"), ("Adjustment + Massage", "45 min", "1800"),
            ("Decompression Therapy", "30 min", "1500"), ("Posture Screening", "30 min", "800")]
    for i, row in enumerate(rows):
        y = 250 + i * 90
        d.line((40, y - 20, 960, y - 20), fill="#ccd", width=2)
        for x, t in zip(cols, row):
            d.text((x, y), t, font=f, fill="#111")
    d.text((60, 760), "Mon–Sat 8 am – 8 pm. Sunday closed.", font=font("liberation/LiberationSans-Italic.ttf", 28), fill="#444")
    return img


def phone_photo():
    """A hand-lettered clinic board shot on a phone: Hindi + English, '/-' prices, tilt, blur, noise."""
    board = Image.new("RGB", (800, 1000), "#1f2f4b")
    d = ImageDraw.Draw(board)
    d.text((400, 80), "DR. MEHTA'S SPINE CLINIC", font=font("ubuntu/UbuntuSans[wdth,wght].ttf", 48), fill="#f4e9c9", anchor="mm")
    d.text((400, 150), "डॉ. मेहता स्पाइन क्लिनिक", font=font("noto/NotoSansDevanagari-Regular.ttf", 44), fill="#f4e9c9", anchor="mm")
    rows = [("परामर्श / Consultation", "800/-"), ("Adjustment (समायोजन)", "600/-"), ("कमर दर्द / Back Pain Therapy", "900/-"),
            ("Neck Therapy (गर्दन)", "700/-"), ("Physiotherapy Session", "500/-")]
    y = 270
    for name, price in rows:
        mixed(d, (60, y), name, 38, "#fff8e7")
        mixed(d, (740, y), price, 38, "#ffd166", right=True)
        y += 115
    photo = Image.new("RGB", (1000, 1200), "#8a8577")
    rot = board.rotate(5, expand=True, fillcolor="#8a8577")
    photo.paste(rot, (60, 40))
    photo = photo.filter(ImageFilter.GaussianBlur(1.2))
    rnd = random.Random(7)
    px = photo.load()
    for _ in range(60000):
        x, y = rnd.randrange(photo.width), rnd.randrange(photo.height)
        r, g, b = px[x, y]
        n = rnd.randint(-28, 28)
        px[x, y] = (max(0, min(255, r + n)), max(0, min(255, g + n)), max(0, min(255, b + n)))
    return photo


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in (("alignright-fees", menu_card), ("backfirst-table", table_card), ("mehta-board-photo", phone_photo)):
        fn().save(OUT / f"{name}.jpg", quality=82)
        print(OUT / f"{name}.jpg")
