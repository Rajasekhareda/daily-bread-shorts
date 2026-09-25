#!/usr/bin/env python3
"""fix_visuals.py - one-shot visual quality patch for generate_video_pro.py.

Fixes requested after review of the first automated videos:
  1. Strip LITERAL backslash-n sequences typed inside sheet cells.
  2. Font order: clean system fonts first, suspect merged bundle font
     demoted to LAST resort (fixes English box glyphs on the runner
     and broken digit/paren fallback inside Telugu screens).
  3. SILENT hairline edge glow: ONE static frame tracing the whole
     border - no spinning, no pulse, no hue drift.
  4. Cinematic polish: Ken Burns slow zoom on image/GIF backgrounds,
     18 Mbps slow-preset encode (max quality for the 1080p Shorts feed).

Safety: every edit is an exact-match replacement. If any anchor is
missing the script ABORTS BEFORE WRITING ANYTHING. The patched code is
syntax-checked before saving, and a backup is kept. Re-running is safe.
"""
import shutil
import sys

P = "generate_video_pro.py"
src = open(P, encoding="utf-8").read()
applied = 0


def rep(old, new, label):
    global src, applied
    if new in src:
        print("[skip - already applied]", label)
        return
    if old not in src:
        print("[ABORT - anchor not found]", label)
        sys.exit(1)
    src = src.replace(old, new, 1)
    applied += 1
    print("[ok]", label)


# ---------- FIX 1: strip literal backslash-n from sheet text ----------
rep(r'''def sanitize_text(text):
    """Normalize punctuation/whitespace but KEEP all Unicode letters."""
    if text is None:
        return text
    for bad, good in _PUNCT_MAP.items():''',
r'''def sanitize_text(text):
    """Normalize punctuation/whitespace but KEEP all Unicode letters.

    Sheet cells often contain LITERAL backslash-n sequences (typed as
    two characters by AI verse generators); those show as visible junk
    in the video, so strip them before anything else.
    """
    if text is None:
        return text
    text = text.replace("\\n", " ").replace("\\r", " ")
    for bad, good in _PUNCT_MAP.items():''',
"FIX 1: strip literal backslash-n")

# ---------- FIX 2a: Telugu font order (merged font demoted to last) ----------
rep(r'''# Dedicated Telugu font FIRST (proper conjuncts with HarfBuzz shaping)
FONT_CANDIDATES_TELUGU = [p for p in [
    FONT_PATH_TELUGU_ENV,
    _TELUGU_FONT,
    _BUNDLED_FONT,
    "/usr/share/fonts/truetype/noto/NotoSerifTelugu-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansTelugu-Bold.ttf",
    r"C:\Windows\Fonts\NirmalaB.ttf",
    r"C:\Windows\Fonts\Nirmala.ttf",
    "/System/Library/Fonts/Supplemental/NotoSansTelugu-Regular.ttf",
] if p and os.path.isfile(p)]''',
r'''# Clean system fonts first; the merged bundle font is the LAST resort
# (a bad font merge can break the cmap -> box glyphs and broken digits).
FONT_CANDIDATES_TELUGU = [p for p in [
    FONT_PATH_TELUGU_ENV,
    "/usr/share/fonts/truetype/noto/NotoSerifTelugu-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansTelugu-Bold.ttf",
    _TELUGU_FONT,
    r"C:\Windows\Fonts\NirmalaB.ttf",
    r"C:\Windows\Fonts\Nirmala.ttf",
    "/System/Library/Fonts/Supplemental/NotoSansTelugu-Regular.ttf",
    _BUNDLED_FONT,
] if p and os.path.isfile(p)]''',
"FIX 2a: Telugu font order")

# ---------- FIX 2b: Latin font order (merged font was winning -> boxes) ----------
rep(r'''FONT_CANDIDATES_LATIN = [p for p in [
    FONT_PATH_LATIN_ENV,
    _BUNDLED_FONT,
    "/usr/share/fonts/truetype/noto/NotoSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    r"C:\Windows\Fonts\georgia.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
] if p and os.path.isfile(p)]''',
r'''# Latin: system fonts first; merged bundle LAST resort (it produced
# box glyphs for English text on the Actions runner).
FONT_CANDIDATES_LATIN = [p for p in [
    FONT_PATH_LATIN_ENV,
    "/usr/share/fonts/truetype/noto/NotoSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    r"C:\Windows\Fonts\georgia.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    _BUNDLED_FONT,
] if p and os.path.isfile(p)]''',
"FIX 2b: Latin font order")

# ---------- FIX 3a: glow constants (static hairline) ----------
rep(r'''# ============ NEON EDGE GLOW ============
# A thin ray that spins fast around the frame border.
GLOW_ON = True
GLOW_REVOLUTIONS = 12     # full loops around the border in 45s (fast)
GLOW_TAIL = 8             # comet-tail segments (short thin ray)
GLOW_LINE_W = 3           # px line width at quarter-res (thin)
GLOW_BLUR = 3             # px blur at quarter-res (tight halo)
GLOW_STRENGTH = 2.6       # additive blend strength''',
r'''# ============ NEON EDGE GLOW ============
# SILENT hairline edge: one STATIC glow tracing the whole border.
GLOW_ON = True
GLOW_LINE_W = 1           # hairline at quarter-res (~4px at 1080p)
GLOW_BLUR = 2             # soft halo at quarter-res
GLOW_STRENGTH = 1.05      # gentle additive glow
GLOW_COLOR = (255, 246, 224)  # warm white-gold, matches the text''',
"FIX 3a: static glow constants")

# ---------- FIX 3b: section comment ----------
rep('# Neon edge glow: a thin ray that spins fast around the border',
    '# Neon edge glow: static hairline tracing the whole border',
    "FIX 3b: section comment")

# ---------- FIX 3c: single frame constant ----------
rep('GLOW_FRAMES = 90  # steps per revolution (precomputed)',
    'GLOW_FRAMES = 1   # single static frame (silent edge glow)',
    "FIX 3c: GLOW_FRAMES = 1")

# ---------- FIX 3d: static full-border glow (replaces the comet) ----------
rep(r'''def make_glow_frames():
    """Precompute quarter-res additive glow frames covering exactly one
    full revolution of the thin ray; looped for the whole video."""
    global _GLOW_FRAMES_CACHE
    if _GLOW_FRAMES_CACHE is not None:
        return _GLOW_FRAMES_CACHE
    qw, qh = VIDEO_SIZE[0] // 4, VIDEO_SIZE[1] // 4
    pts = [(x / 4, y / 4) for x, y in _edge_points()]
    n = len(pts)
    frames = []
    for f in range(GLOW_FRAMES):
        t = f / GLOW_FRAMES
        hue = (t * 1.0) % 1.0            # hue drifts across one loop
        img = Image.new("RGB", (qw, qh), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        pos = t * n                      # float position along perimeter
        for k in range(GLOW_TAIL):       # short thin comet tail
            idx = int(pos - k) % n
            nxt = (idx + 1) % n
            fade = (1 - k / GLOW_TAIL) ** 2.2
            r, g, b = colorsys.hsv_to_rgb((hue + k * 0.005) % 1.0, 0.95, 1.0)
            color = (int(r * 255 * fade), int(g * 255 * fade), int(b * 255 * fade))
            draw.line([pts[idx], pts[nxt]], fill=color,
                      width=max(1, int(GLOW_LINE_W * fade) + 1))
        img = img.filter(ImageFilter.GaussianBlur(GLOW_BLUR))
        frames.append(np.array(img.resize(VIDEO_SIZE, _LANCZOS), dtype=np.float32))
    _GLOW_FRAMES_CACHE = frames
    return frames''',
r'''def make_glow_frames():
    """A single STATIC glow frame: a hairline tracing the ENTIRE border.

    Silent edge treatment - no spinning ray, no pulse, no hue drift.
    """
    global _GLOW_FRAMES_CACHE
    if _GLOW_FRAMES_CACHE is not None:
        return _GLOW_FRAMES_CACHE
    qw, qh = VIDEO_SIZE[0] // 4, VIDEO_SIZE[1] // 4
    pts = [(x / 4, y / 4) for x, y in _edge_points()]
    img = Image.new("RGB", (qw, qh), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.line(pts + [pts[0]], fill=GLOW_COLOR, width=GLOW_LINE_W)
    img = img.filter(ImageFilter.GaussianBlur(GLOW_BLUR))
    frame = np.array(img.resize(VIDEO_SIZE, _LANCZOS), dtype=np.float32)
    _GLOW_FRAMES_CACHE = [frame]
    return _GLOW_FRAMES_CACHE''',
"FIX 3d: static full-border glow")

# ---------- FIX 3e: frame index -> always the single static frame ----------
rep(r'''        if glow_frames is not None:
            loop_t = (t * GLOW_REVOLUTIONS) % 1.0
            gi = int(loop_t * GLOW_FRAMES) % GLOW_FRAMES''',
r'''        if glow_frames is not None:
            gi = 0  # static edge glow: always the single frame''',
"FIX 3e: static frame index")

# ---------- FIX 4a: max-quality encode ----------
rep(r'''        bitrate="10M",
        preset="medium",''',
r'''        bitrate="18M",
        preset="slow",''',
"FIX 4a: 18M slow encode")

# ---------- FIX 4b: Ken Burns helpers (inserted before make_gif_bg) ----------
rep('def make_gif_bg(path):',
r'''def _ken_burns_base(img):
    """Oversized, sharpened base for a slow cinematic zoom (Ken Burns)."""
    big = img.resize((int(VIDEO_SIZE[0] * 1.12), int(VIDEO_SIZE[1] * 1.12)),
                     _LANCZOS)
    return big.filter(ImageFilter.UnsharpMask(radius=2, percent=55, threshold=3))


def _kb_window(base, t):
    """Crop the Ken Burns window for time t (slow zoom-in 1.0 -> 1.10)."""
    zoom = 1.0 + 0.10 * max(0.0, min(t / max(0.1, TOTAL_DURATION), 1.0))
    key = int(zoom * 100)
    cache = getattr(_kb_window, "_cache", None)
    if cache is None:
        cache = _kb_window._cache = {}
    if key not in cache:
        cw = max(1, int(base.width / zoom))
        ch = max(1, int(base.height / zoom))
        x0 = (base.width - cw) // 2
        y0 = (base.height - ch) // 2
        window = base.crop((x0, y0, x0 + cw, y0 + ch)).resize(VIDEO_SIZE, _LANCZOS)
        if len(cache) > 14:
            cache.clear()
        cache[key] = window
    return cache[key]


def make_gif_bg(path):''',
"FIX 4b: Ken Burns helpers")

# ---------- FIX 4c: Ken Burns on image backgrounds ----------
rep(r'''def make_image_bg(path):
    with Image.open(path) as im:
        base = _finish_still(im, IMAGE_DIM)

    def provider(t):
        return base.copy()

    return provider''',
r'''def make_image_bg(path):
    with Image.open(path) as im:
        base = _ken_burns_base(_finish_still(im, IMAGE_DIM))

    def provider(t):
        return _kb_window(base, t).copy()

    return provider''',
"FIX 4c: image Ken Burns")

# ---------- FIX 4d: Ken Burns on GIF backgrounds ----------
rep(r'''    total = offsets[-1]

    def provider(t):
        tt = t % total if total > 0 else 0.0
        k = max(0, min(bisect_right(offsets, tt) - 1, len(frames) - 1))
        return frames[k].copy()

    return provider''',
r'''    total = offsets[-1]
    kb_frames = [_ken_burns_base(f) for f in frames]

    def provider(t):
        tt = t % total if total > 0 else 0.0
        k = max(0, min(bisect_right(offsets, tt) - 1, len(frames) - 1))
        return _kb_window(kb_frames[k], t).copy()

    return provider''',
"FIX 4d: GIF Ken Burns")

# ---------- finalize ----------
if applied == 0:
    print("Nothing to do - all fixes already applied.")
    sys.exit(0)

try:
    compile(src, P, "exec")
except SyntaxError as e:
    print("[ABORT] patched source failed to compile:", e)
    sys.exit(1)

shutil.copyfile(P, P + ".bak2")
with open(P, "w", encoding="utf-8", newline="") as fh:
    fh.write(src)
print("DONE - %d fix group(s) applied. Backup: %s.bak2" % (applied, P))
