"""Programmatically generated synthetic PDF fixtures for the tests.

The tiled binary dot-matrix watermark used here is purely synthetic: a
staggered grid of cells, each holding either "=" (two horizontal bars) or
"||" (two vertical bars), chosen by a fixed pseudo-random sequence.
"""
import random

import numpy as np

try:
    import pymupdf as fitz
except ImportError:
    import fitz

W, H = 595, 842
PITCH_X, PITCH_Y = 12.0, 8.0
BAR_L, BAR_T, BAR_GAP = 5.0, 1.0, 1.5
BODY = ("Synthetic body text line for watermark removal tests. "
        "The quick brown fox jumps over the lazy dog.")


def glyph_cells(width=W, height=H, seed=7):
    """Yield (x, y, kind) for a staggered grid; kind is 'h' ('=') or 'v' ('||')."""
    rnd = random.Random(seed)
    row = 0
    y = 2.0
    while y < height - BAR_L:
        x = 2.0 + (PITCH_X / 2 if row % 2 else 0.0)
        while x < width - BAR_L:
            yield x, y, rnd.choice("hv")
            x += PITCH_X
        y += PITCH_Y
        row += 1


def glyph_rects(x, y, kind):
    if kind == "h":
        return [fitz.Rect(x, y, x + BAR_L, y + BAR_T),
                fitz.Rect(x, y + BAR_T + BAR_GAP, x + BAR_L, y + 2 * BAR_T + BAR_GAP)]
    return [fitz.Rect(x, y, x + BAR_T, y + BAR_L),
            fitz.Rect(x + BAR_T + BAR_GAP, y, x + 2 * BAR_T + BAR_GAP, y + BAR_L)]


def add_body_text(page, color=(0, 0, 0)):
    for i in range(30):
        page.insert_text((50, 80 + i * 22), BODY, fontsize=10, color=color)


def add_vector_watermark(page, grey=0.94, opacity=1.0):
    shape = page.new_shape()
    for x, y, kind in glyph_cells():
        for r in glyph_rects(x, y, kind):
            shape.draw_rect(r)
        shape.finish(color=None, fill=(grey, grey, grey), fill_opacity=opacity)
    shape.commit(overlay=True)


def vector_doc(opacity=False):
    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    add_body_text(page)
    if opacity:
        add_vector_watermark(page, grey=0.6, opacity=0.15)  # effective ~0.94
    else:
        add_vector_watermark(page)
    return doc


def _set_resource(doc, page, category, value):
    t, v = doc.xref_get_key(page.xref, "Resources")
    target = int(v.split()[0]) if t == "xref" else page.xref
    key = category if t == "xref" else "Resources/" + category
    doc.xref_set_key(target, key, value)


def _append_stream(doc, page, data, prepend=False):
    xref = page.get_contents()[0]
    old = doc.xref_stream(xref)
    doc.update_stream(xref, (data + b"\n" + old) if prepend else (old + b"\n" + data))


def pattern_doc():
    """Dot-matrix drawn as a page-wide tiling Pattern fill."""
    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    add_body_text(page)
    pat = doc.get_new_xref()
    doc.update_object(pat, "<< /Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1 "
                           "/BBox [0 0 24 16] /XStep 24 /YStep 16 /Resources << >> >>")
    doc.update_stream(pat, b"0.93 g 1 1 5 1 re 1 3.5 5 1 re f "
                           b"13 1 1 5 re 15.5 1 1 5 re f "
                           b"7 9 1 5 re 9.5 9 1 5 re f "
                           b"19 9 5 1 re 19 11.5 5 1 re f")
    _set_resource(doc, page, "Pattern", "<< /P0 %d 0 R >>" % pat)
    _append_stream(doc, page, b"q /Pattern cs /P0 scn 0 0 %d %d re f Q" % (W, H))
    return doc


def form_repeat_doc():
    """Dot-matrix drawn as many invocations of two tiny glyph Form XObjects."""
    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    add_body_text(page)
    forms = {}
    for name, body, bbox in (("Gh", b"0.93 g 0 0 5 1 re 0 2.5 5 1 re f", "0 0 5 3.5"),
                             ("Gv", b"0.93 g 0 0 1 5 re 2.5 0 1 5 re f", "0 0 3.5 5")):
        x = doc.get_new_xref()
        doc.update_object(x, "<< /Type /XObject /Subtype /Form /BBox [%s] >>" % bbox)
        doc.update_stream(x, body)
        forms[name] = x
    _set_resource(doc, page, "XObject", "<< /Gh %d 0 R /Gv %d 0 R >>" % (forms["Gh"], forms["Gv"]))
    ops = [b"q 1 0 0 1 %.2f %.2f cm /%s Do Q" % (x, y, b"Gh" if k == "h" else b"Gv")
           for x, y, k in glyph_cells()]
    _append_stream(doc, page, b"\n".join(ops))
    return doc


def _render_gray(page, dpi):
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).astype(float)


def _image_doc(gray):
    g = np.clip(np.round(gray), 0, 255).astype(np.uint8)
    rgb = np.repeat(g[:, :, None], 3, axis=2)
    pix = fitz.Pixmap(fitz.csRGB, g.shape[1], g.shape[0], rgb.tobytes(), False)
    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    page.insert_image(page.rect, pixmap=pix)
    return doc


def raster_watermark_layer(dpi=100, base=250, dark=246, light=252):
    """Grey-level layer: faint tinted base, darker bars and lighter in-cell gaps."""
    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    shape = page.new_shape()
    for x, y, kind in glyph_cells():
        for r in glyph_rects(x, y, kind):
            shape.draw_rect(r)
        shape.finish(color=None, fill=(0, 0, 0))
    shape.commit()
    bars = _render_gray(page, dpi) / 255.0  # 0 on bars, 1 elsewhere
    layer = dark + (base - dark) * bars
    # slightly lighter level at alternate rows of each cell, as in real samples
    rows = (np.arange(layer.shape[0]) // 3) % 2 == 0
    layer[rows] = np.where(bars[rows] > 0.99, light, layer[rows])
    return layer


def raster_doc(dpi=100):
    """Dot-matrix multiplied over body text and burned into one page image."""
    text = fitz.open()
    add_body_text(text.new_page(width=W, height=H))
    t = _render_gray(text[0], dpi)
    return _image_doc(t * raster_watermark_layer(dpi) / 255.0)


def legit_light_grey_doc():
    """Normal light-grey content that must NOT be treated as a watermark."""
    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    shape = page.new_shape()
    # zebra shading behind table rows
    for i in range(0, 30, 2):
        shape.draw_rect(fitz.Rect(40, 60 + i * 24, 555, 84 + i * 24))
        shape.finish(color=None, fill=(0.95, 0.95, 0.95))
    # light-grey table rules, horizontal and vertical
    for i in range(31):
        shape.draw_line((40, 60 + i * 24), (555, 60 + i * 24))
        shape.finish(color=(0.85, 0.85, 0.85), width=0.5)
    for x in (40, 170, 300, 430, 555):
        shape.draw_line((x, 60), (x, 780))
        shape.finish(color=(0.85, 0.85, 0.85), width=0.5)
    # page-wide grid of small light-grey checkbox squares (periodic, not bars)
    for y in range(20, 830, 16):
        for x in range(20, 580, 16):
            shape.draw_rect(fitz.Rect(x, y, x + 4, y + 4))
            shape.finish(color=None, fill=(0.9, 0.9, 0.9))
    # page-wide dashed light-grey rules (periodic short bars, collinear)
    for y in range(30, 830, 24):
        for x in range(20, 580, 10):
            shape.draw_rect(fitz.Rect(x, y, x + 5, y + 1))
            shape.finish(color=None, fill=(0.88, 0.88, 0.88))
    shape.commit()
    for i in range(30):
        page.insert_text((50, 76 + i * 24), "Light grey cell text %d" % i,
                         fontsize=9, color=(0.8, 0.8, 0.8))
    return doc


def light_raster_doc(dpi=100):
    """Page image with light-grey shading and text but no watermark."""
    src = legit_light_grey_doc()
    add_body_text(src[0])
    return _image_doc(_render_gray(src[0], dpi))


def legacy_pattern_form_doc():
    """Tiled-pattern watermark wrapped in a Form XObject (handled by tiled-pattern)."""
    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    add_body_text(page)
    pat = doc.get_new_xref()
    doc.update_object(pat, "<< /Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1 "
                           "/BBox [0 0 100 100] /XStep 100 /YStep 100 /Resources << >> >>")
    doc.update_stream(pat, b"0.8 g 10 10 40 20 re f")
    form = doc.get_new_xref()
    doc.update_object(form, "<< /Type /XObject /Subtype /Form /BBox [0 0 %d %d] "
                            "/Resources << /Pattern << /P0 %d 0 R >> >> >>" % (W, H, pat))
    doc.update_stream(form, b"/Pattern cs /P0 scn 0 0 %d %d re f" % (W, H))
    _set_resource(doc, page, "XObject", "<< /Wm0 %d 0 R >>" % form)
    _append_stream(doc, page, b"q /Wm0 Do Q")
    return doc
