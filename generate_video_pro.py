"""
generate_video_pro.py
=====================
Cinematic 45-second YouTube SHORTS Bible-verse generator (9:16 vertical).

Flow: full Telugu text animates in line-by-line from the top (as many
screens as the text needs), a 3-second pause, then the English text.
Backgrounds are random cinematic animated scene GIFs (no immediate
repeat) with a smooth neon edge glow. Music-only by default; with
ElevenLabs keys set, verses are narrated (Telugu: eleven_v3, English:
eleven_turbo_v2_5), music is ducked under the voice, and duration adapts
to the narration.

Telugu text is shaped correctly with HarfBuzz (uharfbuzz) and
rasterized with FreeType - Windows Pillow has no raqm, so plain PIL
draws broken conjuncts. Latin text uses PIL directly.
"""

import argparse
import colorsys
import glob
import json
import math
import os
import platform
import random
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from bisect import bisect_right
from http.client import IncompleteRead
from ssl import SSLError

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

try:
    from fontTools.ttLib import TTFont as _FTFont
    _HAS_FONTTOOLS = True
except ImportError:
    _HAS_FONTTOOLS = False

try:
    from moviepy import (AudioFileClip, CompositeAudioClip, VideoClip,
                         VideoFileClip, concatenate_audioclips)
except ImportError:
    from moviepy.editor import (AudioFileClip, CompositeAudioClip,
                                VideoClip, VideoFileClip,
                                concatenate_audioclips)

# Correct complex-script shaping for Telugu (Windows Pillow lacks raqm)
try:
    from shaped_text import (shape_line as _shape_line,
                             render_shaped_layer as _render_shaped_layer)
    _HAS_SHAPING = True
except ImportError:
    _HAS_SHAPING = False

# Optional ElevenLabs narration; absent keys -> music-only fallback
try:
    import tts_elevenlabs as _tts
except ImportError:
    print("WARNING: tts_elevenlabs.py not found - narration disabled. "
          "(Did you forget to commit/add it?)")
    _tts = None

# Windows consoles default to cp1252; make all Unicode prints safe
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

# ================= SHEET LAYOUT =================
# Column A = Telugu verse text
# Column B = English verse text
# Column C = optional brief explanation/note (any language)
# Column D = "used" marker, written automatically by this script
# ==================================================

SHEET_ID = os.environ.get("SHEET_ID", "")
SHEET_TAB = os.environ.get("SHEET_TAB", "Sheet1")
MUSIC_DIR = os.environ.get("MUSIC_DIR", "assets/music")

OUTPUT_DIR = "output"
THUMBNAIL_DIR = os.path.join(OUTPUT_DIR, "thumbnails")

# ================= VIDEO SPEC (SHORTS 9:16) ================
FPS = 30
VIDEO_SIZE = (1080, 1920)          # 9:16 vertical for YouTube Shorts
TOTAL_DURATION = 45.0              # exact video length, seconds

# Shorts safe areas: bigger top/bottom margins so text never hides
# under the Shorts UI (title overlay top, action buttons bottom)
SAFE_MARGIN_X_RATIO = 0.08
SAFE_MARGIN_TOP_RATIO = 0.14
SAFE_MARGIN_BOTTOM_RATIO = 0.16

SAFE_LEFT = int(VIDEO_SIZE[0] * SAFE_MARGIN_X_RATIO)
SAFE_RIGHT = int(VIDEO_SIZE[0] * (1 - SAFE_MARGIN_X_RATIO))
SAFE_TOP = int(VIDEO_SIZE[1] * SAFE_MARGIN_TOP_RATIO)
SAFE_BOTTOM = int(VIDEO_SIZE[1] * (1 - SAFE_MARGIN_BOTTOM_RATIO))
SAFE_TEXT_WIDTH = int((SAFE_RIGHT - SAFE_LEFT) * 0.96)

# ============ ANIMATION TIMING ============
# Full text flows from the top; screens fill up, then a new screen
# starts. After all Telugu screens: a 3s pause, then English.
LINE_FADE = 0.55          # seconds for each line to fade in
LINE_STAGGER = 0.65       # seconds between consecutive line starts
LINE_RISE_PIXELS = 14      # gentle settle-up as each line appears
ENTRANCE_CAP = 8.0         # max seconds for a screen's full entrance
HOLD_SECONDS = 6.0         # text holds this long after entrance
PAGE_FADE_OUT = 0.9        # clean fade-away at screen end
MIN_PAGE_DURATION = 3.0     # never squeeze a screen below this
TELUGU_PAUSE = 3.0          # empty-screen pause between Telugu & English

# Typography
SHADOW_COLOR = (0, 0, 0, 200)
STROKE_COLOR = (15, 15, 25, 210)
SHADOW_BLUR_RADIUS = 6
LINE_SPACING_MULTIPLIER = 1.42

# Telugu: golden gradient text (top -> bottom) for a rich cinematic look
TELUGU_GRADIENT_TOP = (255, 248, 224)     # warm ivory
TELUGU_GRADIENT_BOTTOM = (242, 196, 112)  # deep gold

# Cinematic text accents matched to each gradient palette
TEXT_ACCENTS = {
    "Midnight Purple": (232, 225, 255),
    "Ocean Blue":      (214, 236, 255),
    "Wine Red":        (255, 226, 229),
    "Emerald Teal":    (222, 255, 244),
    "Sunset Amber":    (255, 236, 204),
    "Indigo Violet":   (228, 224, 255),
    "Midnight Slate":  (233, 240, 247),
    "Charcoal":        (250, 246, 238),
}
DEFAULT_TEXT_ACCENT = (255, 244, 224)

# ============ GOOGLE STUDIO EDGE GLOW ============
# Static, high-quality soft glow around the border
GLOW_ON = True
GLOW_LINE_W = 2           # Width of the core glow line
GLOW_BLUR = 15            # Wider blur for a 'studio' soft look
GLOW_STRENGTH = 1.2       # Blend factor
GLOW_COLOR = (255, 246, 224)  # Warm white-gold glow

# ============ BACKGROUNDS (SHORTS: scenic animated GIFs) ============
# BACKGROUND_MODE: gif (default) | gradient | image | video
BACKGROUND_MODE = (os.environ.get("BACKGROUND_MODE", "gif").strip().lower()
                   or "gif")
BACKGROUND_DIR = os.environ.get("BACKGROUND_DIR", "assets/backgrounds")
BACKGROUND_IMAGE = os.environ.get("BACKGROUND_IMAGE", "").strip()
BACKGROUND_GIF = os.environ.get("BACKGROUND_GIF", "").strip()
BACKGROUND_VIDEO = os.environ.get("BACKGROUND_VIDEO", "").strip()
IMAGE_DIM = 0.40
VIDEO_DIM = 0.50
GIF_FRAME_CAP = 24
LAST_BG_FILE = ".last_background"   # avoids repeating the same scene

# Manual-run controls
PRIVACY_STATUS = os.environ.get("PRIVACY_STATUS", "private").strip().lower()
MUSIC_CHOICE = os.environ.get("MUSIC_CHOICE", "random").strip()
BACKGROUND_THEME = os.environ.get("BACKGROUND_THEME", "random").strip()
INCLUDE_EXPLANATION = os.environ.get("INCLUDE_EXPLANATION", "Auto").strip().lower()
TELUGU_OVERRIDE = os.environ.get("TELUGU_OVERRIDE", "").strip()
ENGLISH_OVERRIDE = os.environ.get("ENGLISH_OVERRIDE", "").strip()
EXPLANATION_OVERRIDE = os.environ.get("EXPLANATION_OVERRIDE", "").strip()

FONT_PATH_TELUGU_ENV = os.environ.get("FONT_PATH_TELUGU", "").strip()
FONT_PATH_LATIN_ENV = os.environ.get("FONT_PATH_LATIN", "").strip()

# ============ OPTIONAL NARRATION (ElevenLabs) ============

def _env_float(name, default):
    """Tolerant float from env: empty/invalid/unset -> default.

    CI often sets env vars to empty strings (e.g. `X: ${{ secrets.X }}`
    with a missing secret), and float("") would crash at import time.
    """
    raw = (os.environ.get(name) or "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        print(f"WARNING: {name}={raw!r} is not a number; using {default}.")
        return default


TTS_PAD_LEAD = _env_float("TTS_PAD_LEAD", 0.4)       # silence before voice starts
TTS_PAD_TAIL = _env_float("TTS_PAD_TAIL", 0.9)       # text lingers after voice ends
MUSIC_DUCK_VOLUME = _env_float("MUSIC_DUCK_VOLUME", 0.22)  # music level under voice
PUBLISH_AT = os.environ.get("PUBLISH_AT", "").strip()           # optional RFC3339 -> YouTube publishAt
MAX_SHORTS_DURATION = 59.5                                       # Shorts limit is 60s

GRADIENT_PALETTES = {
    "Midnight Purple": ((18, 12, 52), (46, 22, 74)),
    "Ocean Blue":       ((8, 30, 70), (16, 55, 96)),
    "Wine Red":         ((36, 8, 20), (72, 22, 42)),
    "Emerald Teal":     ((8, 38, 36), (14, 66, 60)),
    "Sunset Amber":     ((40, 20, 10), (86, 46, 20)),
    "Indigo Violet":    ((20, 14, 50), (48, 34, 96)),
    "Midnight Slate":   ((14, 20, 34), (26, 38, 60)),
    "Charcoal":         ((16, 16, 20), (30, 30, 36)),
}

BASE_HASHTAGS = ["#BibleVerse", "#DailyVerse", "#Faith", "#God", "#Jesus", "#Scripture", "#Shorts"]
TELUGU_HASHTAGS = ["#TeluguChristian", "#YesuKrishtu", "#Telugu"]
ENGLISH_HASHTAGS = ["#Christian", "#Gospel", "#WordOfGod"]

_REPO_DIR = os.path.dirname(os.path.abspath(__file__))
_BUNDLED_FONT = os.path.join(_REPO_DIR, "NotoSerifMerged-Bold.ttf")
_TELUGU_FONT = os.path.join(_REPO_DIR, "NotoSerifTelugu-Bold.ttf")

# Clean system fonts first; bundled fonts are last resort to avoid box glyphs
FONT_CANDIDATES_TELUGU = [p for p in [
    FONT_PATH_TELUGU_ENV,
    r"C:\Windows\Fonts\NirmalaB.ttf",
    r"C:\Windows\Fonts\Nirmala.ttf",
    "/usr/share/fonts/truetype/noto/NotoSerifTelugu-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansTelugu-Bold.ttf",
    "/System/Library/Fonts/Supplemental/NotoSansTelugu-Regular.ttf",
    _TELUGU_FONT,
    _BUNDLED_FONT,
] if p and os.path.isfile(p)]

FONT_CANDIDATES_LATIN = [p for p in [
    FONT_PATH_LATIN_ENV,
    r"C:\Windows\Fonts\georgia.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/noto/NotoSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    _BUNDLED_FONT,
] if p and os.path.isfile(p)]

# ===================================================================
# Text helpers
# ===================================================================

def is_telugu(text):
    return any("\u0c00" <= ch <= "\u0c7f" for ch in text)


def detect_language(text):
    if not text:
        return "unknown"
    telugu_chars = sum(1 for ch in text if "\u0c00" <= ch <= "\u0c7f")
    total_chars = len([ch for ch in text if ch.isalpha()])
    if total_chars == 0:
        return "unknown"
    telugu_ratio = telugu_chars / total_chars
    if telugu_ratio > 0.7:
        return "telugu"
    elif telugu_ratio > 0.1:
        return "mixed"
    return "english"


_PUNCT_MAP = {
    "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-",
    "\u2026": "...",
    "\u2022": "-", "\u25cf": "-", "\u2023": "-",
    "\u2020": "*", "\u2021": "*",
    "\u00a7": "Sec.",
    "\u00b6": "",
    "\u2212": "-",
    "\u00d7": "x",
    "\u00f7": "/",
    "\u00b0": " deg",
    "\u00ab": '"', "\u00bb": '"',
    "\u00a0": " ", "\u2007": " ", "\u2009": " ", "\u200a": " ", "\u2028": " ",
    "\u200b": "", "\u200c": "", "\u200d": "", "\ufeff": "",
}


def sanitize_text(text):
    """Normalize punctuation/whitespace but KEEP all Unicode letters."""
    if text is None:
        return text
    for bad, good in _PUNCT_MAP.items():
        text = text.replace(bad, good)
    text = text.replace("\n", " ").replace("\t", " ")
    text = re.sub(r" {2,}", " ", text).strip()
    return text


def extract_reference_tag(text):
    match = re.search(r"\(([^()]+)\)\s*$", text.strip())
    if not match:
        return None
    inner = match.group(1).strip()
    return inner.split()[0] if inner.split() else None


def count_words(text):
    if not text:
        return 0
    return len(re.findall(r"\b\w+\b", text))


def generate_hashtags(telugu_text, english_text):
    tags = list(BASE_HASHTAGS)
    tags += TELUGU_HASHTAGS if is_telugu(telugu_text) else []
    tags += ENGLISH_HASHTAGS if english_text else []
    for source in (english_text, telugu_text):
        tag_word = extract_reference_tag(source or "")
        if tag_word:
            cleaned = "".join(
                ch for ch in tag_word
                if not unicodedata.category(ch).startswith(("P", "Z", "C", "N"))
            )
            book_tag = "#" + cleaned
            if book_tag != "#" and book_tag not in tags:
                tags.append(book_tag)
            break
    return tags[:10]


# ===================================================================
# Fonts + shaped measuring (Telugu-safe)
# ===================================================================

_FONT_CACHE = {}


def load_font(font_path, size):
    key = (font_path, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    try:
        font = ImageFont.truetype(font_path, size) if font_path else None
        if font is None:
            raise OSError("no font path")
    except OSError:
        print(f"WARNING: could not load font at '{font_path}'.")
        font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def resolve_font_path(candidates, script_key):
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def measure_text(text, font_path, font_size, fallback_path=None):
    """Width in px; Telugu measured through HarfBuzz shaping so wrapped
    widths match what actually renders."""
    if _HAS_SHAPING and is_telugu(text):
        return _shape_line(text, font_path, font_size, fallback_path).width
    font = load_font(font_path, font_size)
    return font.getlength(text)


def wrap_text(text, font_path, font_size, max_width, fallback_path=None):
    """Word-wrap by shaped width; hard-break a too-long word."""
    def mw(s):
        return measure_text(s, font_path, font_size, fallback_path)

    lines = []
    for word in text.split(" "):
        if not word:
            continue
        if mw(word) <= max_width:
            if lines and mw(lines[-1] + " " + word) <= max_width:
                lines[-1] += " " + word
            else:
                lines.append(word)
        else:
            # hard-break the long word character by character
            piece = ""
            for ch in word:
                if mw(piece + ch) <= max_width:
                    piece += ch
                else:
                    if piece:
                        lines.append(piece)
                    piece = ch
            if piece:
                lines.append(piece)
    return lines or [""]


def choose_font_size(total_words, video_size):
    w = video_size[0]
    base = int(w * 0.13)
    if total_words > 60:
        scale = 0.62
    elif total_words > 40:
        scale = 0.72
    elif total_words > 24:
        scale = 0.84
    elif total_words > 12:
        scale = 0.93
    else:
        scale = 1.0
    size = int(base * scale)
    return max(int(w * 0.07), min(size, int(w * 0.16)))


def _explanation_enabled():
    return INCLUDE_EXPLANATION in ("auto", "yes", "true", "1")


# ===================================================================
# Screen building: full text flows from top, screens fill up
# ===================================================================

def split_reference(text):
    """Split a trailing verse reference like '(John 3:16)' off the text.

    Returns (body, reference) where reference is e.g. 'John 3:16'
    (without parens) or None when no reference exists. The reference
    always renders as its OWN separate last line - never glued to the
    verse body, never split into stray pieces.
    """
    if not text:
        return text, None
    match = re.search(r"\(([^()]+)\)\s*$", text.strip())
    if not match:
        return text, None
    ref = match.group(1).strip()
    body = text.strip()[:match.start(1) - 1].strip()
    # body should still have meaningful content
    if not ref or not body:
        return text, None
    return body, ref


def build_screen_list(telugu_text, english_text, explanation_text,
                      telugu_font_path, latin_font_path, font_size):
    """Wrap each language and pack lines into screens that FILL from
    the top. Between Telugu and English a pause screen is inserted.
    The verse reference always becomes its own separate final line.

    Returns list of screens: {"lines": [(text, font_path)], "kind": ...}
    """
    line_h = int(font_size * LINE_SPACING_MULTIPLIER)
    usable_h = SAFE_BOTTOM - SAFE_TOP
    max_lines_per_screen = max(2, int(usable_h // line_h))

    def wrap_and_pack(text, font_path, kind, reference=None, section=None):
        if (not text or not text.strip()) and not reference:
            return []
        fallback = latin_font_path if kind == "telugu" else None
        lines = []
        if text and text.strip():
            lines = wrap_text(text, font_path, font_size, SAFE_TEXT_WIDTH,
                              fallback_path=fallback)
        # reference ALWAYS as its own clean separate line (never glued,
        # never a leftover piece of the body wrap)
        if reference:
            lines.append("\u2014 " + reference)
        screens = []
        chunk, count = [], 0
        for ln in lines:
            if count >= max_lines_per_screen:
                screens.append({"lines": chunk, "kind": kind, "section": section or kind})
                chunk, count = [], 0
            chunk.append((ln, font_path))
            count += 1
        if chunk:
            screens.append({"lines": chunk, "kind": kind, "section": section or kind})
        return screens

    telugu_body, telugu_ref = split_reference(telugu_text)
    english_body, english_ref = split_reference(english_text)

    screens = []
    screens += wrap_and_pack(telugu_body, telugu_font_path, "telugu", telugu_ref,
                             section="telugu")
    if telugu_text and english_text:
        screens.append({"lines": [], "kind": "pause", "section": "pause"})
    screens += wrap_and_pack(english_body, latin_font_path, "english", english_ref,
                             section="english")
    if explanation_text and _explanation_enabled():
        exp_path = telugu_font_path if is_telugu(explanation_text) else latin_font_path
        kind = "telugu" if is_telugu(explanation_text) else "english"
        screens.append({"lines": [], "kind": "pause", "section": "pause"})
        screens += wrap_and_pack(explanation_text, exp_path, kind,
                                 section="explanation")
    return screens


# ===================================================================
# Timing
# ===================================================================

def ease_out_cubic(p):
    p = max(0.0, min(1.0, p))
    return 1 - (1 - p) ** 3


def _rebalance_min_duration(durs, indices, total_budget, screens):
    """Raise screens below MIN_PAGE_DURATION to the floor and shrink the
    others proportionally, re-checking until stable (max 3 passes).

    Screens sitting exactly AT the floor are never shrunk (`d > MIN`, not
    `d >= MIN`), so the loop converges. Used by both fixed-length and
    narration-budget scheduling modes.
    """
    active = [i for i in indices if screens[i]["kind"] != "pause"]
    for _ in range(3):
        tight = [i for i in active if durs[i] < MIN_PAGE_DURATION]
        if not tight:
            break
        for i in tight:
            durs[i] = MIN_PAGE_DURATION
        free = [i for i in active if durs[i] > MIN_PAGE_DURATION]
        if not free:
            break
        room = total_budget - MIN_PAGE_DURATION * len(tight)
        sub = sum(durs[i] for i in free)
        if sub <= 0 or room <= 0:
            break
        for i in free:
            durs[i] *= room / sub


def schedule_screens(screens, section_budgets=None):
    """Assign durations.

    Default (section_budgets=None): durations scale to exactly
    TOTAL_DURATION - the classic fixed 45s music-only behavior.

    With section_budgets (e.g. {"telugu": 15.3, "english": 12.1} from
    TTS narration lengths): each section stretches/shrinks to fill
    exactly its budget so text stays on screen while the voice reads;
    pause screens keep their natural raw durations. Total length then
    adapts to the narration.
    """
    for s in screens:
        if s["kind"] == "pause":
            s["entrance"] = 0.0
            s["fade_out"] = 0.0
            s["raw"] = TELUGU_PAUSE
            continue
        nl = max(1, len(s["lines"]))
        entrance = min(ENTRANCE_CAP, (nl - 1) * LINE_STAGGER + LINE_FADE)
        s["entrance"] = entrance
        s["fade_out"] = PAGE_FADE_OUT
        s["raw"] = entrance + HOLD_SECONDS + PAGE_FADE_OUT

    if section_budgets is None:
        # ---- classic fixed-length mode (original behavior) ----
        scale = TOTAL_DURATION / sum(s["raw"] for s in screens)
        durs = [s["raw"] * scale for s in screens]

        _rebalance_min_duration(durs, range(len(durs)), TOTAL_DURATION, screens)

        total = sum(durs)
        durs = [d * TOTAL_DURATION / total for d in durs]
    else:
        # ---- narration-budget mode ----
        durs = [s["raw"] for s in screens]
        for label, budget in section_budgets.items():
            idxs = [i for i, s in enumerate(screens) if s.get("section") == label]
            if not idxs:
                continue
            raw_total = sum(durs[i] for i in idxs)
            if raw_total <= 0:
                continue
            f = budget / raw_total
            for i in idxs:
                durs[i] *= f
            _rebalance_min_duration(durs, idxs, budget, screens)

    starts = []
    acc = 0.0
    for s, d in zip(screens, durs):
        s["duration"] = d
        if s["kind"] == "pause":
            s["line_fade"] = 0
            s["line_starts"] = []
            starts.append(acc)
            acc += d
            continue
        f = d / s["raw"]
        s["fade_out"] = max(0.1, min(s["fade_out"] * f, d * 0.3))
        s["entrance"] = max(0.2, min(s["entrance"] * f, d - s["fade_out"] - 0.1))
        lf = max(0.08, min(LINE_FADE * f, s["entrance"] * 0.5))
        s["line_fade"] = lf
        nl = len(s["lines"])
        if nl > 1:
            stagger = max(0.01, (s["entrance"] - lf) / (nl - 1))
        else:
            stagger = 0.0
        s["line_starts"] = [i * stagger for i in range(nl)]
        starts.append(acc)
        acc += d
    return starts

# ===================================================================
# Backgrounds: gradient / scenic gif / image / video
# ===================================================================

_LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
_VIGNETTE_MASK = None
_ACTIVE_PALETTE_NAME = [None]


def _vignette_mask():
    global _VIGNETTE_MASK
    if _VIGNETTE_MASK is None:
        mask = Image.new("L", VIDEO_SIZE, 0)
        d = ImageDraw.Draw(mask)
        d.ellipse([-VIDEO_SIZE[0] * 0.2, -VIDEO_SIZE[1] * 0.2,
                   VIDEO_SIZE[0] * 1.2, VIDEO_SIZE[1] * 1.2], fill=255)
        _VIGNETTE_MASK = mask.filter(
            ImageFilter.GaussianBlur(int(120 * VIDEO_SIZE[1] / 1920)))
    return _VIGNETTE_MASK


def _cover_resize(img):
    w, h = VIDEO_SIZE
    iw, ih = img.size
    if (iw, ih) == (w, h):
        return img
    scale = max(w / iw, h / ih)
    nw, nh = int(iw * scale + 0.5), int(ih * scale + 0.5)
    img = img.resize((nw, nh), _LANCZOS)
    left = (nw - w) // 2
    top = (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def _finish_still(img, dim):
    img = _cover_resize(img.convert("RGB"))
    if dim > 0:
        img = Image.blend(img, Image.new("RGB", img.size, (0, 0, 0)), dim)
    return Image.composite(img, Image.new("RGB", img.size, (0, 0, 0)),
                           _vignette_mask())


def pick_gradient_palette():
    name = None
    if BACKGROUND_THEME and BACKGROUND_THEME.lower() != "random" and BACKGROUND_THEME in GRADIENT_PALETTES:
        name = BACKGROUND_THEME
    if not name:
        name = _ACTIVE_PALETTE_NAME[0]
    if not name or name not in GRADIENT_PALETTES:
        name = random.choice(list(GRADIENT_PALETTES.keys()))
    _ACTIVE_PALETTE_NAME[0] = name
    return name, GRADIENT_PALETTES[name]


def text_accent_color():
    name = _ACTIVE_PALETTE_NAME[0]
    if name and name in TEXT_ACCENTS:
        return TEXT_ACCENTS[name] + (255,)
    return DEFAULT_TEXT_ACCENT + (255,)


def create_background(t=None):
    name, (top_color, bottom_color) = pick_gradient_palette()
    background = Image.new("RGB", VIDEO_SIZE)
    draw = ImageDraw.Draw(background)
    for y in range(VIDEO_SIZE[1]):
        ratio = y / VIDEO_SIZE[1]
        draw.line([(0, y), (VIDEO_SIZE[0], y)], fill=(
            int(top_color[0] * (1 - ratio) + bottom_color[0] * ratio),
            int(top_color[1] * (1 - ratio) + bottom_color[1] * ratio),
            int(top_color[2] * (1 - ratio) + bottom_color[2] * ratio)))
    return Image.composite(background, Image.new("RGB", VIDEO_SIZE, (0, 0, 0)),
                           _vignette_mask())


def make_gradient_bg():
    base = create_background()

    def provider(t):
        return base.copy()

    return provider


def _parse_duration_hint(im, default=100):
    d = im.info.get("duration")
    return d if d and d > 0 else default


def _ken_burns_base(img):
    """Oversized base for a slow cinematic zoom."""
    big = img.resize((int(VIDEO_SIZE[0] * 1.15), int(VIDEO_SIZE[1] * 1.15)), _LANCZOS)
    return big.filter(ImageFilter.UnsharpMask(radius=2, percent=55, threshold=3))

def _kb_window(base, t):
    """Crop the Ken Burns window for time t (slow zoom-in 1.0 -> 1.10)."""
    zoom = 1.0 + 0.10 * max(0.0, min(t / max(0.1, TOTAL_DURATION), 1.0))
    cw = max(1, int(base.width / zoom))
    ch = max(1, int(base.height / zoom))
    x0 = (base.width - cw) // 2
    y0 = (base.height - ch) // 2
    return base.crop((x0, y0, x0 + cw, y0 + ch)).resize(VIDEO_SIZE, _LANCZOS)

def make_gif_bg(path):
    frames = []
    offsets = [0.0]
    with Image.open(path) as im:
        n_total = getattr(im, "n_frames", 1)
        step = max(1, math.ceil(n_total / GIF_FRAME_CAP))
        for i in range(0, n_total, step):
            im.seek(i)
            frames.append(_ken_burns_base(_finish_still(im, IMAGE_DIM)))
            offsets.append(offsets[-1] + max(0.02, _parse_duration_hint(im) / 1000.0))
            if len(frames) >= GIF_FRAME_CAP:
                break
    if not frames:
        return make_gradient_bg()
    total = offsets[-1]

    def provider(t):
        tt = t % total if total > 0 else 0.0
        k = max(0, min(bisect_right(offsets, tt) - 1, len(frames) - 1))
        return _kb_window(frames[k], t).copy()

    return provider


def make_image_bg(path):
    with Image.open(path) as im:
        base = _ken_burns_base(_finish_still(im, IMAGE_DIM))

    def provider(t):
        return _kb_window(base, t).copy()

    return provider


def make_video_bg(path):
    src = VideoFileClip(path, audio=False)
    scale = max(VIDEO_SIZE[0] / src.w, VIDEO_SIZE[1] / src.h)
    if hasattr(src, "resized"):
        src = src.resized(scale) if abs(scale - 1.0) > 0.01 else src
    cache = {}
    order = []

    def provider(t):
        tt = t % max(0.1, src.duration)
        key = int(tt * 5)
        if key not in cache:
            frame = src.get_frame(key / 5.0)
            cache[key] = frame
            order.append(key)
            if len(order) > 40:
                old = order.pop(0)
                cache.pop(old, None)
        img = Image.fromarray(cache[key]).convert("RGB")
        img = _cover_resize(img)
        return Image.blend(img, Image.new("RGB", img.size, (0, 0, 0)), VIDEO_DIM)

    return provider


def _load_last_bg():
    p = os.path.join(_REPO_DIR, LAST_BG_FILE)
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _save_last_bg(name):
    p = os.path.join(_REPO_DIR, LAST_BG_FILE)
    try:
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(name)
    except OSError:
        pass


def _find_bg_file(kind):
    explicit = {"image": BACKGROUND_IMAGE, "gif": BACKGROUND_GIF,
                "video": BACKGROUND_VIDEO}[kind]
    if explicit and os.path.isfile(explicit):
        return explicit
    if not os.path.isdir(BACKGROUND_DIR):
        return None
    exts = {"image": (".jpg", ".jpeg", ".png", ".webp", ".bmp"),
            "gif": (".gif",), "video": (".mp4", ".mov", ".mkv", ".webm", ".avi")}[kind]
    for f in sorted(os.listdir(BACKGROUND_DIR)):
        if f.lower().endswith(exts):
            return os.path.join(BACKGROUND_DIR, f)
    return None


def _find_scenic_gifs():
    if not os.path.isdir(BACKGROUND_DIR):
        return []
    return sorted(os.path.join(BACKGROUND_DIR, f)
                 for f in os.listdir(BACKGROUND_DIR)
                 if f.lower().endswith(".gif"))


def resolve_background():
    """Pick a random scenic GIF, avoiding the previous video's scene.

    Falls back to gradient when no GIFs exist.
    """
    mode = BACKGROUND_MODE
    if mode not in ("gradient", "image", "gif", "video"):
        print(f"WARNING: unknown BACKGROUND_MODE '{mode}'; using gif.")
        mode = "gif"

    if mode == "gradient":
        return make_gradient_bg(), "gradient"

    if mode == "image":
        p = _find_bg_file("image")
        if p:
            return make_image_bg(p), "image"
        print("WARNING: no image background; falling back to scenic GIF.")

    if mode == "gif":
        if BACKGROUND_GIF and os.path.isfile(BACKGROUND_GIF):
            _save_last_bg(os.path.basename(BACKGROUND_GIF))
            return make_gif_bg(BACKGROUND_GIF), "gif"
        gifs = _find_scenic_gifs()
        if gifs:
            last = _load_last_bg()
            pool = [g for g in gifs if os.path.basename(g) != last] or gifs
            chosen = random.choice(pool)
            print(f"Scenic background GIF: {os.path.basename(chosen)} "
                  f"({len(gifs)} available)")
            _save_last_bg(os.path.basename(chosen))
            return make_gif_bg(chosen), "gif"
        print("WARNING: no scenic GIF found; falling back to gradient.")

    if mode == "video":
        p = _find_bg_file("video")
        if p:
            try:
                return make_video_bg(p), "video"
            except Exception as e:
                print(f"WARNING: video background failed ({e}).")

    return make_gradient_bg(), "gradient"


# ===================================================================
# Neon edge glow: a thin ray that spins fast around the border
# ===================================================================

_GLOW_FRAMES_CACHE = None
GLOW_FRAMES = 90  # steps per revolution (precomputed)


def _edge_points():
    """Perimeter points of the frame, evenly spaced (36 per side +)."""
    w, h = VIDEO_SIZE
    m = int(min(w, h) * 0.025)
    pts = []
    x0, y0, x1, y1 = m, m, w - m, h - m
    n = 40
    for i in range(n):
        pts.append((x0 + (x1 - x0) * i / n, y0))
    for i in range(1, n):
        pts.append((x1, y0 + (y1 - y0) * i / n))
    for i in range(1, n):
        pts.append((x1 - (x1 - x0) * i / n, y1))
    for i in range(1, n):
        pts.append((x0, y1 - (y1 - y0) * i / n))
    return pts


def make_glow_frames():
    """A single STATIC glow frame: a high-quality soft hairline tracing the border."""
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
    return _GLOW_FRAMES_CACHE

# ===================================================================
# Line rendering: pre-rendered layers, shaped for Telugu
# ===================================================================

def render_line_layer(text, font_path, font_size, text_fill, fallback_path=None):
    """Render one line as an RGBA layer.

    Telugu (and any complex script) goes through the shaped path
    (HarfBuzz + FreeType) so conjuncts are correct, with a golden
    gradient fill and per-glyph fallback for missing glyphs
    (digits/parens). Latin uses PIL, unchanged.
    Returns (layer PIL RGBA, width_px).
    """
    stroke_w = max(2, font_size // 24)
    use_shaped = _HAS_SHAPING and is_telugu(text)
    if use_shaped:
        img, _baseline = _render_shaped_layer(
            text, font_path, font_size, fallback_path=fallback_path,
            fill_top=TELUGU_GRADIENT_TOP, fill_bottom=TELUGU_GRADIENT_BOTTOM,
            stroke_width=stroke_w, stroke_fill=STROKE_COLOR[:3])
        return img, img.size[0]
    font = load_font(font_path, font_size)
    pad = max(8, stroke_w * 3)
    w = int(measure_text(text, font_path, font_size))
    h = int(font_size * 1.9)
    img = Image.new("RGBA", (w + pad * 2, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.text((pad + 2, h // 2 - font_size // 2 + 2), text, font=font,
              fill=(0, 0, 0, 150))
    draw.text((pad, h // 2 - font_size // 2), text, font=font, fill=text_fill,
              stroke_width=stroke_w, stroke_fill=STROKE_COLOR)
    return img, w


def build_line_layers(screens, font_size, fallback_font_path=None):
    """Pre-render every line of every screen once (no per-frame
    re-rasterization = no flicker). Attaches layer + placement."""
    text_fill = text_accent_color()
    line_h = int(font_size * LINE_SPACING_MULTIPLIER)
    for s in screens:
        if s["kind"] == "pause":
            s["line_layers"] = []
            continue
        layers = []
        for i, (text, font_path) in enumerate(s["lines"]):
            layer, w = render_line_layer(text, font_path, font_size, text_fill,
                                         fallback_path=fallback_font_path)
            x = (VIDEO_SIZE[0] - w) // 2
            y = SAFE_TOP + i * line_h
            # vertical optical centering inside its line slot
            y = y + (line_h - layer.size[1]) // 2
            layers.append({"layer": layer, "x": max(0, x), "y": y})
        s["line_layers"] = layers
    return screens


# ===================================================================
# Audio: music must play until the very end of the 45s
# ===================================================================

def _compat(obj, new_name, old_name, *args, **kwargs):
    if hasattr(obj, new_name):
        return getattr(obj, new_name)(*args, **kwargs)
    return getattr(obj, old_name)(*args, **kwargs)


def pick_music_file():
    if not os.path.isdir(MUSIC_DIR):
        raise FileNotFoundError(f"Music directory '{MUSIC_DIR}' does not exist")
    music_files = sorted(f for f in os.listdir(MUSIC_DIR) if f.lower().endswith(".mp3"))
    if not music_files:
        raise FileNotFoundError(f"No .mp3 files found in {MUSIC_DIR}")
    if MUSIC_CHOICE and MUSIC_CHOICE.lower() != "random":
        for f in music_files:
            if f.lower() == MUSIC_CHOICE.lower():
                return os.path.join(MUSIC_DIR, f)
        print(f"Warning: '{MUSIC_CHOICE}' not found, picking randomly.")
    return os.path.join(MUSIC_DIR, random.choice(music_files))


def prepare_audio(music_path, duration, volume=0.85):
    """Music clip of exactly `duration` seconds, looped if needed.

    Volume 0.85 (healthy level) with a gentle 1.5s fade in/out.
    """
    src = AudioFileClip(music_path)
    start_offset = min(2.0, max(0.0, src.duration * 0.02))
    available = src.duration - start_offset
    if available <= 0:
        start_offset, available = 0.0, src.duration

    if available >= duration:
        audio = _compat(src, "subclipped", "subclip", start_offset, start_offset + duration)
    else:
        clips = [_compat(src, "subclipped", "subclip", start_offset, src.duration)]
        remaining = duration - available
        while remaining > 0.01:
            take = min(src.duration, remaining)
            clips.append(_compat(src, "subclipped", "subclip", 0, take))
            remaining -= take
        audio = concatenate_audioclips(clips)

    audio = _compat(audio, "with_volume_scaled", "volumex", volume)

    # fade in/out (moviepy 2.x: audio_fadein; 1.x: audio_fadein too)
    try:
        audio = _compat(audio, "with_audio_fadein", "audio_fadein", 1.5)
        audio = _compat(audio, "with_audio_fadeout", "audio_fadeout", min(2.0, duration * 0.08))
    except Exception:
        pass
    return audio


# ===================================================================
# Video builder
# ===================================================================

def build_video(telugu_text, english_text, explanation_text):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(THUMBNAIL_DIR, exist_ok=True)

    pick_gradient_palette()  # pins palette + text tint for this video

    telugu_font_path = resolve_font_path(FONT_CANDIDATES_TELUGU, "telugu")
    latin_font_path = resolve_font_path(FONT_CANDIDATES_LATIN, "latin")
    if telugu_text and not telugu_font_path:
        print("WARNING: no Telugu font resolved.")
    if english_text and not latin_font_path:
        print("WARNING: no Latin font resolved.")

    total_words = count_words(telugu_text) + count_words(english_text)
    if explanation_text and _explanation_enabled():
        total_words += count_words(explanation_text)
    font_size = choose_font_size(total_words, VIDEO_SIZE)

    screens = build_screen_list(telugu_text, english_text, explanation_text,
                                telugu_font_path, latin_font_path, font_size)
    if not screens:
        raise ValueError("No text content provided to render.")

    # --- Optional ElevenLabs narration ---
    voice_plan = None
    if _tts is not None and _tts.tts_available():
        voice_plan = {}
        try:
            if telugu_text:
                p = _tts.synthesize(telugu_text, "te",
                                    os.path.join(OUTPUT_DIR, "tts_telugu.mp3"))
                voice_plan["telugu"] = {"path": p, "dur": _tts.audio_duration(p)}
                print(f"Telugu narration: {voice_plan['telugu']['dur']:.1f}s")
            if english_text:
                p = _tts.synthesize(english_text, "en",
                                    os.path.join(OUTPUT_DIR, "tts_english.mp3"))
                voice_plan["english"] = {"path": p, "dur": _tts.audio_duration(p)}
                print(f"English narration: {voice_plan['english']['dur']:.1f}s")
            if explanation_text and _explanation_enabled():
                # Same predicate as build_screen_list's font choice, so a
                # mixed-script explanation is always narrated by the voice
                # that matches the font on screen (eleven_v3 handles Latin
                # inside Telugu text; turbo cannot voice Telugu script).
                lang = "te" if is_telugu(explanation_text) else "en"
                p = _tts.synthesize(explanation_text, lang,
                                    os.path.join(OUTPUT_DIR, "tts_explanation.mp3"))
                voice_plan["explanation"] = {"path": p,
                                             "dur": _tts.audio_duration(p)}
                print(f"Explanation narration: "
                      f"{voice_plan['explanation']['dur']:.1f}s")
        except Exception as e:
            print(f"WARNING: TTS failed ({e}); falling back to music-only mode.")
            voice_plan = None

    section_budgets = None
    if voice_plan:
        section_budgets = {label: TTS_PAD_LEAD + v["dur"] + TTS_PAD_TAIL
                           for label, v in voice_plan.items()}

    starts = schedule_screens(screens, section_budgets)
    total_dur = starts[-1] + screens[-1]["duration"]
    if voice_plan and total_dur > MAX_SHORTS_DURATION:
        print(f"WARNING: total duration {total_dur:.1f}s exceeds "
              f"{MAX_SHORTS_DURATION}s - YouTube may not treat this as a "
              f"Short. Consider a shorter verse/explanation.")
    build_line_layers(screens, font_size, fallback_font_path=latin_font_path)

    print(f"Prepared {len(screens)} screen(s) across {total_dur:.1f}s "
          f"(narration: {'on' if voice_plan else 'off'}):")
    for i, (s, st) in enumerate(zip(screens, starts)):
        label = s["kind"].upper() if s["kind"] == "pause" else f"{len(s['lines'])} lines"
        preview = " / ".join(t for t, _ in s["lines"])[:60]
        print(f"  Screen {i + 1}: {st:5.2f}s -> {st + s['duration']:5.2f}s "
              f"[{label}]  {preview}")

    bg_provider, bg_mode = resolve_background()
    print(f"Background mode: {bg_mode}")
    glow_frames = make_glow_frames() if GLOW_ON else None

    def make_frame(t):
        t = min(t, total_dur - 1e-3)
        idx = max(0, min(bisect_right(starts, t) - 1, len(screens) - 1))
        screen = screens[idx]
        local_t = t - starts[idx]

        frame = bg_provider(t)

        if screen["kind"] != "pause":
            fo = screen["fade_out"]
            page_alpha = 1.0
            if local_t > screen["duration"] - fo:
                page_alpha = ease_out_cubic(
                    max(0.0, (screen["duration"] - local_t) / fo))

            if page_alpha > 0.01:
                lf = screen["line_fade"]
                for line_layer, l_start in zip(screen["line_layers"],
                                               screen["line_starts"]):
                    lt = local_t - l_start
                    if lt <= 0:
                        continue
                    prog = min(1.0, lt / lf)
                    l_alpha = ease_out_cubic(prog) * page_alpha
                    if l_alpha <= 0.01:
                        continue
                    rise = int(round((1 - ease_out_cubic(prog)) * LINE_RISE_PIXELS))
                    y = line_layer["y"] - rise
                    layer = line_layer["layer"]
                    if l_alpha < 0.999:
                        a = np.array(layer, dtype=np.float32)
                        a[..., 3] *= l_alpha
                        layer = Image.fromarray(a.astype(np.uint8))
                    frame.paste(layer, (line_layer["x"], y), layer)

        frame_arr = np.array(frame, dtype=np.float32)
        if glow_frames is not None:
            loop_t = (t * GLOW_REVOLUTIONS) % 1.0
            gi = int(loop_t * GLOW_FRAMES) % GLOW_FRAMES
            frame_arr += glow_frames[gi] * GLOW_STRENGTH

        return np.clip(frame_arr, 0, 255).astype(np.uint8)

    clip = VideoClip(make_frame, duration=total_dur)
    clip = _compat(clip, "with_fps", "set_fps", FPS)

    # --- Audio: music (ducked under voice) + narrations ---
    music_audio = None
    try:
        music_path = pick_music_file()
        print(f"Music: {os.path.basename(music_path)}")
        music_audio = prepare_audio(
            music_path, total_dur,
            volume=MUSIC_DUCK_VOLUME if voice_plan else 0.85)
    except FileNotFoundError as e:
        print(f"No background music available ({e}); rendering without music.")

    if voice_plan:
        voice_clips = []
        for label, v in voice_plan.items():
            sec_start = None
            for st, s in zip(starts, screens):
                if s.get("section") == label:
                    sec_start = st
                    break
            if sec_start is None:
                continue
            vc = AudioFileClip(v["path"])
            vc = _compat(vc, "with_start", "set_start", sec_start + TTS_PAD_LEAD)
            voice_clips.append(vc)
        parts = ([music_audio] if music_audio is not None else []) + voice_clips
        audio = CompositeAudioClip(parts)
        clip = _compat(clip, "with_audio", "set_audio", audio)
    elif music_audio is not None:
        clip = _compat(clip, "with_audio", "set_audio", music_audio)

    output_path = os.path.join(OUTPUT_DIR, "verse_short.mp4")
    clip.write_videofile(
        output_path,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        bitrate="10M",
        preset="medium",
        threads=4,
        ffmpeg_params=["-pix_fmt", "yuv420p"],
    )

    thumbnail_path = generate_thumbnail(telugu_text, english_text,
                                         telugu_font_path, latin_font_path,
                                         font_size)
    return output_path, thumbnail_path


def generate_thumbnail(telugu_text, english_text, telugu_font_path,
                       latin_font_path, font_size):
    """Vertical (9:16) Shorts thumbnail."""
    thumb_size = (720, 1280)
    bg_img = _cover_resize(create_background().resize(
        (int(thumb_size[0] * 0.67), int(thumb_size[1] * 0.67)), _LANCZOS))

    display_text = telugu_text or english_text or "Daily Bible Verse"
    use_telugu = is_telugu(display_text)
    font_path = telugu_font_path if use_telugu else latin_font_path

    safe_w = int(thumb_size[0] * 0.86)
    fs = int(thumb_size[0] * 0.115)
    fb = latin_font_path if use_telugu else None
    lines = wrap_text(display_text, font_path, fs, safe_w, fallback_path=fb)[:3]

    draw = ImageDraw.Draw(bg_img)
    stroke_w = max(1, fs // 30)
    text_fill = text_accent_color()
    line_h = int(fs * 1.4)
    top = (thumb_size[1] - line_h * len(lines)) // 2
    for i, line in enumerate(lines):
        if use_telugu and _HAS_SHAPING:
            img, w = render_line_layer(line, font_path, fs, text_fill,
                                         fallback_path=latin_font_path)
            bg_img.paste(img, ((thumb_size[0] - img.size[0]) // 2,
                               top + i * line_h), img)
            continue
        font = load_font(font_path, fs)
        w = draw.textlength(line, font=font)
        draw.text(((thumb_size[0] - w) / 2, top + i * line_height_safe(fs)),
                  line, font=font, fill=text_fill,
                  stroke_width=stroke_w, stroke_fill=(0, 0, 0))

    label_font = load_font(latin_font_path, int(fs * 0.32))
    draw.text((36, thumb_size[1] - int(fs * 0.32) - 36), "DAILY VERSE",
              font=label_font, fill=(235, 200, 120),
              stroke_width=2, stroke_fill=(0, 0, 0))

    timestamp = int(time.time())
    thumbnail_path = os.path.join(THUMBNAIL_DIR, f"thumbnail_{timestamp}.jpg")
    bg_img.convert("RGB").save(thumbnail_path, "JPEG", quality=95)
    return thumbnail_path


def line_height_safe(fs):
    return int(fs * 1.4) - int(fs * 0.25)

# ===================================================================
# Google Sheets / YouTube integration
# ===================================================================

def get_user_credentials():
    return UserCredentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/youtube.upload",
        ],
    )


def get_sheets_service():
    sa_info = json.loads(os.environ["GCP_SERVICE_ACCOUNT_JSON"])
    sa_creds = service_account.Credentials.from_service_account_info(
        sa_info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=sa_creds)


def get_youtube_service(creds):
    return build("youtube", "v3", credentials=creds)


def call_with_retries(func, max_retries=5, base_delay=5):
    RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}
    for attempt in range(1, max_retries + 1):
        try:
            return func()
        except HttpError as e:
            status = e.resp.status if getattr(e, "resp", None) else None
            if status not in RETRYABLE_HTTP_STATUSES or attempt == max_retries:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            print(f"Google API returned {status} - retrying in {delay}s...")
            time.sleep(delay)
        except (SSLError, ConnectionError, IncompleteRead, TimeoutError) as e:
            if attempt == max_retries:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            print(f"Network error ({e}) - retrying in {delay}s...")
            time.sleep(delay)


def fetch_next_row(service):
    """Fetch the FIRST unused row (strict queue: row 2, then 3, ...)."""
    range_ = f"{SHEET_TAB}!A2:D"
    result = call_with_retries(
        lambda: service.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=range_).execute()
    )
    rows = result.get("values", [])
    available_count = 0
    for i, row in enumerate(rows):
        telugu = row[0] if len(row) > 0 else ""
        english = row[1] if len(row) > 1 else ""
        explanation = row[2] if len(row) > 2 else ""
        used = row[3] if len(row) > 3 else ""
        if (telugu or english) and used.strip().lower() != "used":
            available_count += 1
            print(f"{available_count} unused row(s) available out of {len(rows)} total.")
            print(f"Queue: selecting row {i + 2} (first unused).")
            return (i + 2, telugu.strip(), english.strip(), explanation.strip())

    print(f"0 unused row(s) available out of {len(rows)} total.")
    return None, None, None, None


def mark_row_used(service, row_number, video_url=None):
    """Mark D='used', E=video URL, F=UTC timestamp (single update)."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    values = [["used", video_url or "", timestamp]]
    call_with_retries(lambda: service.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_TAB}!D{row_number}:F{row_number}",
        valueInputOption="RAW",
        body={"values": values},
    ).execute())
    print(f"Marked row {row_number}: used | URL | timestamp (columns D/E/F).")


def upload_to_youtube(youtube, video_path, telugu_text, english_text):
    base_text = english_text or telugu_text
    title_source = re.sub(r"\([^()]*\)\s*$", "", base_text).strip()
    title = (title_source[:80] + "...") if len(title_source) > 80 else title_source
    if not title:
        title = "Daily Bible Verse"

    hashtags = generate_hashtags(telugu_text, english_text)
    description = f"{telugu_text}\n\n{english_text}\n\n" + " ".join(hashtags)
    privacy = PRIVACY_STATUS if PRIVACY_STATUS in ("private", "public", "unlisted") else "private"

    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "categoryId": "22",
            "tags": [t.lstrip("#") for t in hashtags],
        },
        "status": {"privacyStatus": privacy},
    }

    if PUBLISH_AT:
        try:
            dt = datetime.fromisoformat(PUBLISH_AT.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if privacy != "private":
                print("WARNING: publishAt requires privacyStatus=private; "
                      "ignoring PUBLISH_AT.")
            elif dt <= datetime.now(timezone.utc):
                # YouTube rejects a past publishAt with a non-retryable 400,
                # which would abort before mark_row_used and stall the
                # queue on this row every day. Treat it like invalid input.
                print("WARNING: PUBLISH_AT is in the past; ignoring it "
                      "(YouTube would reject it).")
            else:
                body["status"]["publishAt"] = dt.astimezone(
                    timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                print(f"Video will auto-publish at "
                      f"{body['status']['publishAt']} (UTC).")
        except ValueError:
            print(f"WARNING: PUBLISH_AT '{PUBLISH_AT}' is not valid "
                  f"ISO/RFC3339; ignoring it.")

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = call_with_retries(lambda: request.next_chunk())
        if status:
            print(f"Upload progress: {int(status.progress() * 100)}%")
    print(f"Uploaded video ID: {response['id']} (privacy: {privacy})")
    return response["id"]


# ===================================================================
# Entry points
# ===================================================================

def run_test_render():
    """Renders a bundled bilingual (Telugu + English) example locally."""
    print("Running local test render (no Sheets, no YouTube upload)...")
    telugu_text = sanitize_text(
        "దేవుని ప్రేమ ఎంతో గొప్పది, కాబట్టి తన అద్వితీయ కుమారుని అనుగ్రహించెను; "
        "ఆయన యందు విశ్వాసముచేత నశించక నిత్యజీవము పొందిన అతనికి ఆయనను అనుగ్రహించెను. (యోహాను 3:16)"
    )
    english_text = sanitize_text(
        "For God so loved the world that he gave his one and only Son, that whoever "
        "believes in him shall not perish but have eternal life. (John 3:16)"
    )
    video_path, thumbnail_path = build_video(telugu_text, english_text, "")
    print(f"Test video created at:     {video_path}")
    print(f"Test thumbnail created at: {thumbnail_path}")


def run_production():
    creds = get_user_credentials()
    sheets_service = get_sheets_service()

    row_number = None
    if TELUGU_OVERRIDE or ENGLISH_OVERRIDE:
        telugu_text, english_text, explanation_text = TELUGU_OVERRIDE, ENGLISH_OVERRIDE, EXPLANATION_OVERRIDE
        print("Using override text")
    else:
        row_number, telugu_text, english_text, explanation_text = fetch_next_row(sheets_service)
        if not telugu_text and not english_text:
            print("No unused rows found in the sheet. Exiting.")
            sys.exit(0)
        print(f"Selected row {row_number}")

    telugu_text = sanitize_text(telugu_text)
    english_text = sanitize_text(english_text)
    explanation_text = sanitize_text(explanation_text)

    video_path, thumbnail_path = build_video(telugu_text, english_text, explanation_text)
    print(f"Generated video: {video_path}")
    print(f"Generated thumbnail: {thumbnail_path}")

    youtube_service = get_youtube_service(creds)
    vid = upload_to_youtube(youtube_service, video_path, telugu_text, english_text)
    video_url = f"https://youtu.be/{vid}" if vid else ""
    if video_url:
        print(f"Video URL: {video_url}")

    if row_number is not None:
        mark_row_used(sheets_service, row_number, video_url or None)

    print("Done.")


def main():
    parser = argparse.ArgumentParser(description="Cinematic Shorts verse video generator")
    parser.add_argument(
        "--test", action="store_true",
        help="Render a local bilingual test video (narrated if ElevenLabs env keys are set, otherwise music-only).",
    )
    args = parser.parse_args()

    if args.test:
        run_test_render()
    else:
        run_production()


if __name__ == "__main__":
    main()
