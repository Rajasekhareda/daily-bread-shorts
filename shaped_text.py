"""shaped_text.py
Telugu-safe complex-script text rendering (HarfBuzz + FreeType).

Windows Pillow has no raqm, so Telugu needs explicit shaping:
  1. HarfBuzz (uharfbuzz) shapes the string into positioned glyphs
  2. FreeType (freetype-py) rasterizes each glyph at its accumulated
     x-cursor position (the "all letters at one place" bug was the
     missing cursor accumulation)
  3. A single whole-line mask gets a dark stroke, soft shadow, and a
     vertical gradient fill - clean and cinematic
  4. Glyphs missing from the primary font (ASCII digits/parens inside
     a Telugu line) fall back per-glyph to a secondary font
"""
import uharfbuzz as hb
import freetype as ft
import numpy as np
from PIL import Image, ImageFilter

_HB_FONTS = {}
_FT_FACES = {}


def _load_hb_font(path):
    if path not in _HB_FONTS:
        with open(path, "rb") as fh:
            blob = hb.Blob(fh.read())
        face = hb.Face(blob)
        _HB_FONTS[path] = hb.Font(face)
    return _HB_FONTS[path]


def _load_ft_face(path, size_px):
    key = (path, size_px)
    if key not in _FT_FACES:
        if len(_FT_FACES) > 16:
            _FT_FACES.clear()
        face = ft.Face(path)
        face.set_pixel_sizes(0, size_px)
        _FT_FACES[key] = face
    return _FT_FACES[key]


def _shape(text, font_path):
    hbfont = _load_hb_font(font_path)
    upem = hbfont.face.upem
    hbfont.scale = (upem, upem)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(hbfont, buf)
    return upem, buf.glyph_infos, buf.glyph_positions


class ShapedLine:
    def __init__(self, glyphs, advances, width, ascender, descender):
        self.glyphs = glyphs      # [{font, code, xo, yo}]
        self.advances = advances  # [px advance per glyph]
        self.width = width
        self.ascender = ascender
        self.descender = descender

    @property
    def height(self):
        return self.ascender - self.descender


def shape_line(text, font_path, font_size, fallback_path=None):
    """Shape with the primary font; per-glyph fallback for .notdef."""
    upem, infos, positions = _shape(text, font_path)
    scale = font_size / upem

    face = _load_ft_face(font_path, font_size)
    asc = face.ascender * (font_size / face.units_per_EM)
    desc = face.descender * (font_size / face.units_per_EM)

    glyphs, advances = [], []
    x = 0.0
    for info, pos in zip(infos, positions):
        code = info.codepoint
        fpath = font_path
        xo = pos.x_offset * scale
        yo = pos.y_offset * scale
        adv = pos.x_advance * scale

        if code == 0 and fallback_path and fallback_path != font_path:
            # single-character re-shape in the fallback font
            ch = text[info.cluster] if info.cluster < len(text) else ""
            if ch:
                fbupem, fbinfos, fbpositions = _shape(ch, fallback_path)
                if fbinfos and fbinfos[0].codepoint != 0:
                    fbscale = font_size / fbupem
                    code = fbinfos[0].codepoint
                    fpath = fallback_path
                    xo = fbpositions[0].x_offset * fbscale
                    yo = fbpositions[0].y_offset * fbscale
                    adv = fbpositions[0].x_advance * fbscale

        glyphs.append({"font": fpath, "code": code, "xo": xo, "yo": yo})
        advances.append(adv)
        x += adv
    return ShapedLine(glyphs, advances, x, asc, desc)


def measure_shaped(text, font_path, font_size, fallback_path=None):
    return shape_line(text, font_path, font_size, fallback_path).width


def render_shaped_layer(text, font_path, font_size, fallback_path=None,
                        fill_top=(255, 248, 224), fill_bottom=(242, 198, 120),
                        stroke_width=4, stroke_fill=(12, 12, 22, 255),
                        shadow_alpha=190, shadow_blur=6):
    """Render one shaped line as a PIL RGBA image (gradient gold text,
    dark stroke, soft shadow). Returns (image, baseline_y)."""
    shaped = shape_line(text, font_path, font_size, fallback_path)

    pad = max(12, stroke_width * 3)
    W = max(1, int(shaped.width) + pad * 2 + stroke_width * 2)
    H = max(1, int(shaped.height) + pad * 2 + stroke_width * 2)
    baseline = pad + stroke_width + shaped.ascender

    # 1) rasterize all glyphs into ONE whole-line alpha mask,
    #    accumulating the x-cursor so glyphs spread across the line
    mask = Image.new("L", (W, H), 0)
    x = 0.0
    for g, adv in zip(shaped.glyphs, shaped.advances):
        if g["code"]:
            face = _load_ft_face(g["font"], font_size)
            face.load_glyph(g["code"], ft.FT_LOAD_RENDER)
            bm = face.glyph.bitmap
            bw, bh = bm.width, bm.rows
            if bw and bh and bm.buffer:
                gi = Image.frombuffer("L", (bw, bh), bytes(bm.buffer), "raw", "L", 0, 1)
                gx = int(round(pad + stroke_width + x + g["xo"] + face.glyph.bitmap_left))
                gy = int(round(baseline - face.glyph.bitmap_top - g["yo"]))
                mask.paste(255, (gx, gy, gx + bw, gy + bh), gi)
        x += adv

    # 2) whole-line stroke + shadow + gradient fill
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    sh = Image.new("L", (W, H), 0)
    sh.paste(mask, (3, 4))
    sh = sh.filter(ImageFilter.GaussianBlur(shadow_blur))
    shadow_img = Image.new("RGBA", (W, H), (0, 0, 0, shadow_alpha))
    shadow_img.putalpha(sh)
    out = Image.alpha_composite(out, shadow_img)

    if stroke_width > 0:
        stroke_mask = mask.filter(ImageFilter.MaxFilter(2 * stroke_width + 1))
        s_img = Image.new("RGBA", (W, H), stroke_fill)
        s_img.putalpha(stroke_mask)
        out = Image.alpha_composite(out, s_img)

    t_arr = np.linspace(0.0, 1.0, H)[:, None]
    rgb = np.zeros((H, W, 3), np.uint8)
    for c in range(3):
        rgb[..., c] = (fill_top[c] * (1 - t_arr) + fill_bottom[c] * t_arr).astype(np.uint8)
    grad = np.dstack([rgb, np.array(mask)]).astype(np.uint8)
    out = Image.alpha_composite(out, Image.fromarray(grad))

    return out, baseline


if __name__ == "__main__":
    t = "దేవుని ప్రేమ ఎంతో గొప్పది (యోహాను 3:16)"
    img, base = render_shaped_layer(t, "NotoSerifTelugu-Bold.ttf", 96,
                                    fallback_path="NotoSerifMerged-Bold.ttf")
    img.save("_shaped_test.png")
    print("rendered", img.size, "baseline", base)
