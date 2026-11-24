"""
מייצר ריל (וידאו אנכי 1080x1920) מתמונות המוצר האחרון שפורסם, ומפרסם אותו דרך ה-Worker.
רץ ב-GitHub Actions. משתני סביבה: WORKER_URL, ACCESS_TOKEN
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

W, H = 1080, 1920
SLIDE_SEC = 3.0
FADE_SEC = 0.5
FPS = 30

WORKER = os.environ.get("WORKER_URL", "").rstrip("/")
TOKEN = os.environ.get("ACCESS_TOKEN", "")

# DejaVu ראשון: יש בו גם עברית וגם ספרות ו-%. ב-Noto Sans Hebrew אין ספרות (יוצאות ריבועים)
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansHebrew-Bold.ttf",
]


def font(size):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.RAQM)
    raise RuntimeError("No Hebrew font found")


def api(method, path, data=None, content_type="application/json", timeout=60):
    req = urllib.request.Request(WORKER + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + TOKEN)
    # Cloudflare חוסם את ה-User-Agent ברירת המחדל של Python (שגיאה 403/1010)
    req.add_header("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) reels-bot/1.0")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        sys.exit(f"Worker {method} {path} -> HTTP {e.code}: {body}")


def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return Image.open(BytesIO(r.read())).convert("RGB")


def clean(text):
    # הסרת אימוג'ים וסימנים שהפונט לא מצייר; שמירת עברית, אנגלית, ספרות ופיסוק
    text = re.sub(r"[^\u0590-\u05FFA-Za-z0-9 .,!?%$:;'\"()\-–+/#]", "", text)
    return re.sub(r"\s+", " ", text).strip(" -–")


def parse_caption(caption, title):
    lines = [l.strip() for l in caption.splitlines() if l.strip()]
    lines = [l for l in lines if not l.startswith("#") and "הקישור בביו" not in l]
    hook = clean(lines[0]) if lines else clean(title)
    benefits = [clean(l) for l in lines if l.startswith("✅")]
    if not benefits:
        benefits = [clean(l) for l in lines[1:3]]
    return hook, [b for b in benefits if b][:3]


def wrap(draw, text, fnt, max_w):
    words, out, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=fnt, direction="rtl") <= max_w:
            cur = t
        else:
            if cur:
                out.append(cur)
            cur = w
    if cur:
        out.append(cur)
    return out


def text_block(img, text, size, y, fill="white", box=(0, 0, 0, 150), max_lines=3):
    d = ImageDraw.Draw(img, "RGBA")
    fnt = font(size)
    lines = wrap(d, text, fnt, W - 160)[:max_lines]
    lh = int(size * 1.3)
    total = lh * len(lines) + 50
    d.rounded_rectangle((50, y, W - 50, y + total), radius=36, fill=box)
    for i, line in enumerate(lines):
        tw = d.textlength(line, font=fnt, direction="rtl")
        d.text(((W - tw) / 2, y + 25 + i * lh), line, font=fnt, fill=fill, direction="rtl")
    return y + total


def background(src):
    bg = src.copy()
    scale = max(W / bg.width, H / bg.height)
    bg = bg.resize((int(bg.width * scale) + 1, int(bg.height * scale) + 1))
    left, top = (bg.width - W) // 2, (bg.height - H) // 2
    bg = bg.crop((left, top, left + W, top + H)).filter(ImageFilter.GaussianBlur(40))
    return Image.blend(bg, Image.new("RGB", (W, H), (20, 20, 25)), 0.45)


def product_card(img, src, top, size=900):
    card = Image.new("RGB", (size, size), "white")
    p = src.copy()
    p.thumbnail((size - 40, size - 40))
    card.paste(p, ((size - p.width) // 2, (size - p.height) // 2))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size, size), radius=48, fill=255)
    img.paste(card, ((W - size) // 2, top), mask)


def slide(src, text, size=74):
    img = background(src)
    product_card(img, src, 560)
    text_block(img, text, size, 200)
    return img


def end_card(src, number, discount):
    img = Image.new("RGB", (W, H), (228, 87, 46))
    d = ImageDraw.Draw(img)
    for i in range(H):  # מעבר צבע עדין
        c = int(228 - 110 * i / H), int(87 - 50 * i / H), int(46 + 64 * i / H)
        d.line([(0, i), (W, i)], fill=c)
    product_card(img, src, 520, 700)
    text_block(img, f"מוצר מספר {number}", 96, 200, box=(0, 0, 0, 90), max_lines=1)
    y = 1300
    if discount and discount != "0%":
        y = text_block(img, f"{discount} הנחה", 84, y, fill=(28, 28, 30), box=(255, 216, 77, 255), max_lines=1) + 40
    text_block(img, "הקישור בפרופיל", 80, y, box=(0, 0, 0, 90), max_lines=1)
    return img


def build_video(slides, out_path, workdir):
    inputs, filters = [], []
    for i, s in enumerate(slides):
        p = os.path.join(workdir, f"s{i}.png")
        s.save(p)
        inputs += ["-loop", "1", "-t", str(SLIDE_SEC), "-i", p]
        frames = int(SLIDE_SEC * FPS)
        filters.append(
            f"[{i}:v]scale={W*2}:{H*2},zoompan=z='min(zoom+0.0006,1.05)':d={frames}:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS},setsar=1,format=yuv420p[v{i}]"
        )
    last, offset = "v0", SLIDE_SEC - FADE_SEC
    for i in range(1, len(slides)):
        filters.append(f"[{last}][v{i}]xfade=transition=fade:duration={FADE_SEC}:offset={offset:.2f}[x{i}]")
        last, offset = f"x{i}", offset + SLIDE_SEC - FADE_SEC
    duration = SLIDE_SEC * len(slides) - FADE_SEC * (len(slides) - 1)
    n = len(slides)
    cmd = (
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
        + inputs
        + ["-f", "lavfi", "-t", f"{duration:.2f}", "-i", "anullsrc=r=44100:cl=stereo"]
        + ["-filter_complex", ";".join(filters), "-map", f"[{last}]", "-map", f"{n}:a"]
        + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-r", str(FPS)]
        + ["-c:a", "aac", "-b:a", "64k", "-shortest", "-movflags", "+faststart", out_path]
    )
    subprocess.run(cmd, check=True)
    return duration


def render(job, images, workdir):
    hook, benefits = parse_caption(job["caption"], job["title"])
    slides = [slide(images[0], hook, 80)]
    for i, b in enumerate(benefits):
        slides.append(slide(images[(i + 1) % len(images)], b))
    slides.append(end_card(images[0], job["number"], job.get("discount")))
    video = os.path.join(workdir, f"reel-{job['number']}-{int(time.time())}.mp4")
    build_video(slides, video, workdir)
    cover = video.replace(".mp4", "-cover.jpg")
    slides[0].save(cover, "JPEG", quality=90)
    return video, cover


def main():
    print("raqm:", features.check("raqm"))
    print("font:", next((p for p in FONT_CANDIDATES if os.path.exists(p)), None))
    if not WORKER or not TOKEN:
        sys.exit("WORKER_URL / ACCESS_TOKEN missing")
    job = api("GET", "/api/reel/next").get("job")
    if not job:
        print("No pending reel – nothing to do.")
        return
    print(f"Making reel for post #{job['number']}: {job['title']}")
    images = []
    for u in job["images"]:
        try:
            images.append(download(u))
        except Exception as e:
            print("skip image", u, e)
    if not images:
        sys.exit("No images could be downloaded")

    with tempfile.TemporaryDirectory() as wd:
        video, cover = render(job, images, wd)
        print("video size:", os.path.getsize(video), "bytes")
        v = api("PUT", "/api/media/" + os.path.basename(video), open(video, "rb").read(), "video/mp4", 120)
        c = api("PUT", "/api/media/" + os.path.basename(cover), open(cover, "rb").read(), "image/jpeg", 60)
        res = api("POST", "/api/reel/publish",
                  json.dumps({"number": job["number"], "video_url": v["url"], "cover_url": c["url"]}).encode(),
                  timeout=420)
        print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
