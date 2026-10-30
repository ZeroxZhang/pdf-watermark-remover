"""Detection and removal of tiled binary dot-matrix watermarks.

A tiled binary dot-matrix watermark covers the whole page with a regular,
staggered grid of tiny cells. Every cell holds one of two glyphs: two short
horizontal bars ("=") or two short vertical bars ("||"). The marks are a very
light neutral grey and are drawn over everything, including body text.

Colour alone is NOT used to decide that something is a watermark, because
documents legitimately contain light-grey table rules, shading and text.
A set of marks is only treated as a watermark when ALL of these hold:

  * the marks are small, light, neutral-grey fills/strokes (no text, no images)
  * they are bar shaped and come in parallel pairs (the "=" / "||" glyphs),
    with both orientations present
  * there are many of them and together they span most of the page
  * their positions repeat periodically on both axes

Three encodings are handled:

  * vector marks: many small paths/rects in the page (or a Form XObject)
    content stream, optionally with ExtGState transparency
  * tiling Patterns / repeated small Form XObjects whose cell content
    consists only of such marks
  * marks burned into raster images (best effort, pixel level)
"""
import re

import numpy as np

try:
    import pymupdf as fitz
except ImportError:  # older PyMuPDF releases
    import fitz

# ---------------------------------------------------------------- tunables
LIGHT_MIN = 0.85          # effective grey level range (0 = black, 1 = white)
LIGHT_MAX = 0.995
NEUTRAL_TOL = 0.04        # max channel spread for "grey"
MAX_MARK_FRAC = 0.04      # max mark size as a fraction of min(page w, h)
MIN_MARKS = 30
MIN_COVERAGE = 0.6        # fraction of page width AND height spanned
MIN_BAR_ASPECT = 1.8
MIN_PAIRED_FRAC = 0.5
MIN_ORIENT_FRAC = 0.1
MIN_PERIOD_PEAK = 0.25
PATTERN_MIN_AREA_FRAC = 0.5

PAINT_OPS = {b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*", b"s", b"S", b"n"}
FILL_ONLY = {b"f", b"F", b"f*"}
STROKE_ONLY = {b"S", b"s"}
PATH_OPS = {b"m", b"l", b"c", b"v", b"y", b"h", b"re"}

# ---------------------------------------------------------------- tokenizer
_DELIMS = b"()<>[]{}/%"
_WS = b" \t\r\n\f\x00"


def tokenize(data):
    """Yield (kind, value, start, end) for a PDF content stream.

    kind is one of 'num', 'name', 'op', 'other'. Strings, arrays, dicts and
    inline image data are returned as 'other' so offsets stay exact.
    """
    i, n = 0, len(data)
    while i < n:
        c = data[i]
        if c in _WS:
            i += 1
            continue
        start = i
        if c == 0x25:  # % comment
            while i < n and data[i] not in b"\r\n":
                i += 1
            continue
        if c == 0x28:  # ( string )
            depth, i = 1, i + 1
            while i < n and depth:
                ch = data[i]
                if ch == 0x5C:
                    i += 2
                    continue
                if ch == 0x28:
                    depth += 1
                elif ch == 0x29:
                    depth -= 1
                i += 1
            yield "other", None, start, i
            continue
        if c == 0x3C:  # < hex > or <<
            if data[i:i + 2] == b"<<":
                yield "other", b"<<", start, i + 2
                i += 2
                continue
            j = data.find(b">", i)
            i = n if j < 0 else j + 1
            yield "other", None, start, i
            continue
        if c == 0x3E and data[i:i + 2] == b">>":
            yield "other", b">>", start, i + 2
            i += 2
            continue
        if c in b"[]{}>":
            yield "other", bytes([c]), start, i + 1
            i += 1
            continue
        if c == 0x2F:  # /Name
            i += 1
            while i < n and data[i] not in _WS and data[i] not in _DELIMS:
                i += 1
            yield "name", data[start + 1:i], start, i
            continue
        while i < n and data[i] not in _WS and data[i] not in _DELIMS:
            i += 1
        word = data[start:i]
        if not word:
            i += 1
            continue
        try:
            yield "num", float(word), start, i
            continue
        except ValueError:
            pass
        yield "op", word, start, i
        if word == b"ID":  # inline image data: skip to EI
            m = re.compile(rb"\sEI(?=[\s]|$)").search(data, i + 1)
            end = n if m is None else m.end()
            yield "other", None, i, end
            i = end


# ---------------------------------------------------------------- geometry
def _mul(a, b):
    """Multiply PDF matrices a*b, each as (a, b, c, d, e, f)."""
    return (a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5])


def _apply(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def _bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


# ---------------------------------------------------------------- colour
def _grey_level(cs, comps):
    """Return grey level in [0,1] for a colour, or None if not neutral grey."""
    if cs == "pattern" or comps is None:
        return None
    if len(comps) == 1:
        return comps[0]
    if len(comps) == 3:
        if max(comps) - min(comps) > NEUTRAL_TOL:
            return None
        return sum(comps) / 3.0
    if len(comps) == 4:
        c, m, y, k = comps
        if max(c, m, y) - min(c, m, y) > NEUTRAL_TOL:
            return None
        return (1 - min(c, m, y)) * (1 - k)
    return None


def _effective(level, alpha):
    if level is None:
        return None
    return 1.0 - alpha * (1.0 - level)


def _is_light(level):
    return level is not None and LIGHT_MIN <= level <= LIGHT_MAX


# ---------------------------------------------------------------- resources
class Resources:
    """Lazy lookup of ExtGState alpha, XObjects and Patterns for a stream."""

    def __init__(self, doc, owner_xref, page=None):
        self.doc = doc
        self.owner = owner_xref
        self.page = page
        self._cache = {}

    def _lookup(self, category, name):
        key = (category, name)
        if key in self._cache:
            return self._cache[key]
        val = None
        try:
            t, v = self.doc.xref_get_key(self.owner, "Resources/%s/%s" % (category, name))
            if t == "xref":
                val = int(v.split()[0])
            elif t == "dict":
                val = v
        except Exception:
            val = None
        self._cache[key] = val
        return val

    def alpha(self, gs_name, stroke=False):
        ref = self._lookup("ExtGState", gs_name)
        if ref is None:
            return None
        try:
            src = self.doc.xref_object(ref) if isinstance(ref, int) else ref
        except Exception:
            return None
        m = re.search(r"/%s\s+([0-9.]+)" % ("CA" if stroke else "ca"), src)
        return float(m.group(1)) if m else None

    def xobject(self, name):
        ref = self._lookup("XObject", name)
        return ref if isinstance(ref, int) else None

    def pattern(self, name):
        ref = self._lookup("Pattern", name)
        return ref if isinstance(ref, int) else None


# ---------------------------------------------------------------- analysis
class Item:
    """A paint operation (path segment or Do call) found in a stream."""
    __slots__ = ("start", "end", "bbox", "bars", "light", "kind", "pattern")

    def __init__(self, start, end, bbox, bars, light, kind, pattern=None):
        self.start, self.end = start, end
        self.bbox, self.bars = bbox, bars
        self.light, self.kind, self.pattern = light, kind, pattern


class _State:
    __slots__ = ("ctm", "fcs", "fcol", "scs", "scol", "falpha", "salpha", "lw")

    def __init__(self):
        self.ctm = IDENTITY
        self.fcs, self.fcol = "gray", [0.0]
        self.scs, self.scol = "gray", [0.0]
        self.falpha = self.salpha = 1.0
        self.lw = 1.0

    def copy(self):
        s = _State()
        for k in self.__slots__:
            v = getattr(self, k)
            setattr(s, k, list(v) if isinstance(v, list) else v)
        return s


def _cs_name(name):
    name = name.decode("latin1") if isinstance(name, bytes) else name
    return {"DeviceGray": "gray", "G": "gray", "CalGray": "gray",
            "DeviceRGB": "rgb", "RGB": "rgb", "CalRGB": "rgb",
            "DeviceCMYK": "cmyk", "CMYK": "cmyk", "Pattern": "pattern"}.get(name, "other")


def scan_stream(data, res, form_checker=None):
    """Walk a content stream and return (items, has_text_or_image)."""
    items = []
    st = _State()
    stack = []
    operands = []
    op_start = None
    path_start = None
    subpaths = []       # list of point lists in device space
    cur = None
    clip = False
    has_text = False
    for kind, val, s, e in tokenize(data):
        if kind != "op":
            if op_start is None:
                op_start = s
            operands.append((kind, val))
            continue
        ostart = op_start if op_start is not None else s
        nums = [v for k, v in operands if k == "num"]
        names = [v for k, v in operands if k == "name"]
        op = val
        if op == b"q":
            stack.append(st.copy())
        elif op == b"Q":
            if stack:
                st = stack.pop()
        elif op == b"cm" and len(nums) >= 6:
            st.ctm = _mul(tuple(nums[-6:]), st.ctm)
        elif op == b"w" and nums:
            st.lw = nums[-1]
        elif op == b"g" and nums:
            st.fcs, st.fcol = "gray", nums[-1:]
        elif op == b"G" and nums:
            st.scs, st.scol = "gray", nums[-1:]
        elif op == b"rg" and len(nums) >= 3:
            st.fcs, st.fcol = "rgb", nums[-3:]
        elif op == b"RG" and len(nums) >= 3:
            st.scs, st.scol = "rgb", nums[-3:]
        elif op == b"k" and len(nums) >= 4:
            st.fcs, st.fcol = "cmyk", nums[-4:]
        elif op == b"K" and len(nums) >= 4:
            st.scs, st.scol = "cmyk", nums[-4:]
        elif op == b"cs" and names:
            st.fcs, st.fcol = _cs_name(names[-1]), None
        elif op == b"CS" and names:
            st.scs, st.scol = _cs_name(names[-1]), None
        elif op in (b"sc", b"scn"):
            st.fcol = names[-1].decode("latin1") if names else nums
            if names:
                st.fcs = "pattern"
        elif op in (b"SC", b"SCN"):
            st.scol = names[-1].decode("latin1") if names else nums
            if names:
                st.scs = "pattern"
        elif op == b"gs" and names:
            nm = names[-1].decode("latin1")
            a = res.alpha(nm)
            if a is not None:
                st.falpha = a
            a = res.alpha(nm, stroke=True)
            if a is not None:
                st.salpha = a
        elif op in (b"BT", b"BI", b"sh") :
            has_text = True
        elif op == b"Do" and names:
            nm = names[-1].decode("latin1")
            info = form_checker(nm) if form_checker else None
            if info is None:
                has_text = True  # unknown / image / content form: opaque
            else:
                fbbox, fbars, fmatrix = info
                m = _mul(fmatrix, st.ctm)
                x0, y0, x1, y1 = fbbox
                bb = _bbox([_apply(m, x0, y0), _apply(m, x1, y0),
                            _apply(m, x0, y1), _apply(m, x1, y1)])
                bars = [_bbox([_apply(m, b[0], b[1]), _apply(m, b[2], b[3])]) for b in fbars]
                items.append(Item(ostart, e, bb, bars, True, "form"))
        elif op in PATH_OPS:
            if path_start is None:
                path_start = ostart
            if op == b"re" and len(nums) >= 4:
                x, y, w, h = nums[-4:]
                subpaths.append([_apply(st.ctm, x, y), _apply(st.ctm, x + w, y + h),
                                 _apply(st.ctm, x + w, y), _apply(st.ctm, x, y + h)])
                cur = None
            elif op == b"m" and len(nums) >= 2:
                cur = [_apply(st.ctm, *nums[-2:])]
                subpaths.append(cur)
            elif op in (b"l", b"c", b"v", b"y") and nums and cur is not None:
                for j in range(0, len(nums) - 1, 2):
                    cur.append(_apply(st.ctm, nums[j], nums[j + 1]))
        elif op in (b"W", b"W*"):
            clip = True
        elif op in PAINT_OPS:
            if path_start is not None and subpaths and not clip:
                pts = [p for sp in subpaths for p in sp]
                bb = _bbox(pts)
                if op in FILL_ONLY:
                    if st.fcs == "pattern" and isinstance(st.fcol, str):
                        items.append(Item(path_start, e, bb, [], False, "pattern",
                                          pattern=st.fcol))
                    else:
                        lvl = _effective(_grey_level(st.fcs, st.fcol), st.falpha)
                        bars = [_bbox(sp) for sp in subpaths]
                        items.append(Item(path_start, e, bb, bars, _is_light(lvl), "path"))
                elif op in STROKE_ONLY:
                    lvl = _effective(_grey_level(st.scs, st.scol), st.salpha)
                    hw = abs(st.lw * st.ctm[0]) / 2.0
                    bars = []
                    for sp in subpaths:
                        b = _bbox(sp)
                        bars.append((b[0] - hw, b[1] - hw, b[2] + hw, b[3] + hw))
                    bb = (bb[0] - hw, bb[1] - hw, bb[2] + hw, bb[3] + hw)
                    items.append(Item(path_start, e, bb, bars, _is_light(lvl), "path"))
                elif op != b"n":
                    items.append(Item(path_start, e, bb, [], False, "path"))
            path_start, subpaths, cur, clip = None, [], None, False
        operands = []
        op_start = None
    return items, has_text


def _is_bar(b):
    w, h = b[2] - b[0], b[3] - b[1]
    lo, hi = min(w, h), max(w, h)
    return hi > 0 and (lo <= 0 or hi / lo >= MIN_BAR_ASPECT)


def _orient(b):
    return "h" if (b[2] - b[0]) >= (b[3] - b[1]) else "v"


def glyph_bars_ok(bars):
    """Check bars look like '=' / '||' glyphs: paired parallel bars, both orientations."""
    bars = [b for b in bars if _is_bar(b)]
    if len(bars) < 2:
        return False
    arr = np.array(bars, dtype=float)
    cx = (arr[:, 0] + arr[:, 2]) / 2
    cy = (arr[:, 1] + arr[:, 3]) / 2
    w = arr[:, 2] - arr[:, 0]
    h = arr[:, 3] - arr[:, 1]
    horiz = w >= h
    length = np.maximum(w, h)
    cell = max(float(np.median(length)) * 2.0, 1e-3)
    grid = {}
    for i, (x, y) in enumerate(zip(cx, cy)):
        grid.setdefault((int(x // cell), int(y // cell)), []).append(i)
    paired = 0
    for i in range(len(bars)):
        gx, gy = int(cx[i] // cell), int(cy[i] // cell)
        L = length[i]
        found = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((gx + dx, gy + dy), ()):
                    if j == i or horiz[j] != horiz[i]:
                        continue
                    if abs(length[j] - L) > 0.35 * L:
                        continue
                    along = abs(cx[j] - cx[i]) if horiz[i] else abs(cy[j] - cy[i])
                    across = abs(cy[j] - cy[i]) if horiz[i] else abs(cx[j] - cx[i])
                    if along <= 0.25 * L and 0 < across <= 1.2 * L:
                        found = True
                        break
                if found:
                    break
            if found:
                break
        paired += found
    n = len(bars)
    nh = int(horiz.sum())
    return (paired / n >= MIN_PAIRED_FRAC and nh / n >= MIN_ORIENT_FRAC
            and (n - nh) / n >= MIN_ORIENT_FRAC)


def _periodic_1d(values, lo, hi):
    span = hi - lo
    if span <= 0 or len(values) < 3:
        return False
    nb = int(min(max(span / 0.5, 16), 8192))
    hist, _ = np.histogram(values, bins=nb, range=(lo, hi))
    hist = hist.astype(float) - hist.mean()
    f = np.fft.rfft(hist, 2 * nb)
    ac = np.fft.irfft(f * np.conj(f))[:nb]
    if ac[0] <= 0:
        return False
    ac /= ac[0]
    binw = span / nb
    lmin = max(int(3.0 / binw), 2)
    lmax = nb // 3
    if lmax <= lmin:
        return False
    seg = ac[lmin:lmax]
    k = int(np.argmax(seg)) + lmin
    if ac[k] < MIN_PERIOD_PEAK:
        return False
    k2 = 2 * k
    return k2 < nb and ac[max(k2 - 2, 0):k2 + 3].max() >= MIN_PERIOD_PEAK * 0.6


def lattice_ok(items, ref_rect):
    """Decide whether a set of light items forms a page-wide periodic dot-matrix."""
    if len(items) < MIN_MARKS:
        return False
    rw, rh = ref_rect[2] - ref_rect[0], ref_rect[3] - ref_rect[1]
    bbs = np.array([it.bbox for it in items], dtype=float)
    if (bbs[:, 2].max() - bbs[:, 0].min()) < MIN_COVERAGE * rw:
        return False
    if (bbs[:, 3].max() - bbs[:, 1].min()) < MIN_COVERAGE * rh:
        return False
    bars = [b for it in items for b in it.bars]
    if not glyph_bars_ok(bars):
        return False
    cx = (bbs[:, 0] + bbs[:, 2]) / 2
    cy = (bbs[:, 1] + bbs[:, 3]) / 2
    return (_periodic_1d(cx, cx.min(), cx.max()) and
            _periodic_1d(cy, cy.min(), cy.max()))


def _small(bb, limit):
    return (bb[2] - bb[0]) <= limit and (bb[3] - bb[1]) <= limit


def _parse_rect(src, key):
    m = re.search(r"/%s\s*\[([^\]]*)\]" % key, src)
    if not m:
        return None
    try:
        v = [float(x) for x in m.group(1).split()]
    except ValueError:
        return None
    return v


class Analyzer:
    """Caches per-document decisions about Forms and Patterns."""

    def __init__(self, doc):
        self.doc = doc
        self._forms = {}
        self._patterns = {}

    def _stream_items(self, xref):
        try:
            data = self.doc.xref_stream(xref) or b""
        except Exception:
            return None, True
        res = Resources(self.doc, xref)
        return scan_stream(data, res)

    def small_mark_form(self, xref, limit):
        """Return (bbox, bars, matrix) if the Form only draws a few light glyph bars."""
        key = (xref, round(limit, 1))
        if key in self._forms:
            return self._forms[key]
        out = None
        try:
            src = self.doc.xref_object(xref)
        except Exception:
            src = ""
        if "/Subtype /Form" in src or "/Subtype/Form" in src:
            bbox = _parse_rect(src, "BBox")
            mat = _parse_rect(src, "Matrix")
            items, opaque = self._stream_items(xref)
            if (bbox and len(bbox) == 4 and not opaque and items
                    and all(it.light and it.kind == "path" for it in items)):
                bars = [b for it in items for b in it.bars]
                fb = (min(bbox[0], bbox[2]), min(bbox[1], bbox[3]),
                      max(bbox[0], bbox[2]), max(bbox[1], bbox[3]))
                if all(_is_bar(b) for b in bars) and _small(fb, limit * 2.5):
                    m = tuple(mat) if mat and len(mat) == 6 else IDENTITY
                    out = (fb, bars, m)
        self._forms[key] = out
        return out

    def dot_matrix_pattern(self, xref):
        """True if a tiling Pattern's cell only holds light paired '=' / '||' bars."""
        if xref in self._patterns:
            return self._patterns[xref]
        ok = False
        try:
            src = self.doc.xref_object(xref)
        except Exception:
            src = ""
        if re.search(r"/PatternType\s+1\b", src):
            items, opaque = self._stream_items(xref)
            xs = re.search(r"/XStep\s+([-0-9.]+)", src)
            ys = re.search(r"/YStep\s+([-0-9.]+)", src)
            if items and not opaque and xs and ys:
                step = min(abs(float(xs.group(1))), abs(float(ys.group(1))))
                bars = [b for it in items for b in it.bars]
                ok = (all(it.light and it.kind == "path" for it in items)
                      and all(_small(it.bbox, step * 0.75) for it in items)
                      and glyph_bars_ok(bars))
        self._patterns[xref] = ok
        return ok


def clean_stream(analyzer, data, res, ref_rect):
    """Return (new_data, removed_count) for one content stream."""
    limit = MAX_MARK_FRAC * min(ref_rect[2] - ref_rect[0], ref_rect[3] - ref_rect[1])
    limit = max(limit, 6.0)

    def form_checker(name):
        x = res.xobject(name)
        return analyzer.small_mark_form(x, limit) if x else None

    items, _ = scan_stream(data, res, form_checker)
    remove = []
    area = max((ref_rect[2] - ref_rect[0]) * (ref_rect[3] - ref_rect[1]), 1e-6)
    for it in items:
        if it.kind == "pattern":
            px = res.pattern(it.pattern)
            bb = it.bbox
            cover = max(bb[2] - bb[0], 0) * max(bb[3] - bb[1], 0) / area
            if px and cover >= PATTERN_MIN_AREA_FRAC and analyzer.dot_matrix_pattern(px):
                remove.append(it)
    marks = [it for it in items if it.light and it.kind in ("path", "form")
             and _small(it.bbox, limit) and it.bars]
    if lattice_ok(marks, ref_rect):
        remove.extend(marks)
    if not remove:
        return data, 0
    remove.sort(key=lambda it: it.start)
    out, pos = [], 0
    for it in remove:
        if it.start < pos:
            continue
        out.append(data[pos:it.start])
        pos = it.end
    out.append(data[pos:])
    return b"".join(out), len(remove)


def _page_rect_pdf(page):
    """Page rectangle in PDF (unrotated, y-up) user space."""
    mb = page.mediabox
    return (mb.x0, mb.y0, mb.x1, mb.y1)


def remove_vector_dot_matrix(doc, page, analyzer, visited):
    """Remove vector / pattern / repeated-form dot-matrix marks from a page.

    Returns the number of removed marks (paint operations).
    """
    removed = 0
    xrefs = page.get_contents()
    if xrefs:
        data = b"".join((doc.xref_stream(x) or b"") + b"\n" for x in xrefs)
        res = Resources(doc, page.xref)
        new, n = clean_stream(analyzer, data, res, _page_rect_pdf(page))
        if n:
            doc.update_stream(xrefs[0], new)
            for x in xrefs[1:]:
                doc.update_stream(x, b"")
            removed += n
    # Watermark marks may also live inside a (page sized) Form XObject layer.
    for info in page.get_xobjects():
        xref = info[0]
        if xref in visited:
            continue
        visited.add(xref)
        try:
            src = doc.xref_object(xref)
        except Exception:
            continue
        if "/Subtype /Form" not in src and "/Subtype/Form" not in src:
            continue
        bbox = _parse_rect(src, "BBox")
        if not bbox or len(bbox) != 4:
            continue
        data = doc.xref_stream(xref) or b""
        new, n = clean_stream(analyzer, data, Resources(doc, xref), tuple(bbox))
        if n:
            doc.update_stream(xref, new)
            removed += n
    return removed


# ---------------------------------------------------------------- raster
# In raster form the marks are a low-amplitude texture of near-white levels
# (often a faint tinted base with slightly darker / lighter bars), so pixel
# removal works on the local high-pass signal rather than on colour.
R_LIGHT_LO = 232          # lightest content kept below this level is never touched
R_DARK = 200
R_NEUTRAL = 14
R_MIN_SIDE = 160
R_TEXTURE_STD = 0.6       # min local std-dev (grey levels) of the texture
R_WIN = 4                 # local window radius for texture statistics


def _box_count(mask, r):
    """Sum of values in a (2r+1)^2 window around each pixel."""
    m = np.pad(np.asarray(mask, dtype=np.float64), ((r + 1, r), (r + 1, r)))
    ii = m.cumsum(0).cumsum(1)
    k = 2 * r + 1
    return ii[k:, k:] - ii[:-k, k:] - ii[k:, :-k] + ii[:-k, :-k]


def _line_count(mask, r, axis):
    """Number of True pixels in a 1-D window of length 2r+1 along axis."""
    pad = [(0, 0), (0, 0)]
    pad[axis] = (r + 1, r)
    c = np.pad(mask.astype(np.int32), pad).cumsum(axis)
    k = 2 * r + 1
    if axis == 0:
        return c[k:, :] - c[:-k, :]
    return c[:, k:] - c[:, :-k]


def raster_analyze(rgb):
    """Return (candidate, highpass, textured) arrays for an RGB image.

    candidate: light neutral pixels away from dark content (text, lines and
               their anti-aliased edges are excluded)
    highpass:  grey level minus local mean, on candidate pixels
    textured:  candidate pixels whose neighbourhood varies slightly, i.e. not
               flat white and not flat light shading
    """
    gray = rgb.mean(axis=2)
    spread = rgb.max(axis=2) - rgb.min(axis=2)
    light = (gray >= R_LIGHT_LO) & (spread <= R_NEUTRAL)
    near_dark = _box_count(gray < R_DARK, 2) > 0
    cand = light & ~near_dark
    cnt = np.maximum(_box_count(cand, R_WIN), 1)
    gl = np.where(cand, gray, 0.0)
    mean = _box_count(gl, R_WIN) / cnt
    var = _box_count(gl * gl, R_WIN) / cnt - mean * mean
    std = np.sqrt(np.maximum(var, 0))
    hp = np.where(cand, gray - mean, 0.0)
    textured = cand & (std >= R_TEXTURE_STD)
    return cand, hp, textured


def raster_glyph_shape_ok(mask):
    """Both short horizontal and short vertical bars must be present."""
    hc = _line_count(mask, 3, 1)
    vc = _line_count(mask, 3, 0)
    total = mask.sum()
    if total == 0:
        return False
    horiz = (mask & (hc >= 4) & (hc >= 2 * vc)).sum() / total
    vert = (mask & (vc >= 4) & (vc >= 2 * hc)).sum() / total
    # the two glyphs are used in similar amounts across a page
    return min(horiz, vert) >= 0.07 and min(horiz, vert) >= 0.4 * max(horiz, vert)


def raster_has_dot_matrix(hp, textured):
    h, w = hp.shape
    if textured.mean() < 0.05:
        return False
    bs = 32
    hb, wb = h // bs, w // bs
    if hb < 3 or wb < 3:
        return False
    blocks = textured[:hb * bs, :wb * bs].reshape(hb, bs, wb, bs).mean(axis=(1, 3)) > 0.2
    if blocks.mean() < MIN_COVERAGE:
        return False
    if not raster_glyph_shape_ok(hp <= -1.0):  # marks darker than their surroundings
        return False
    f = np.fft.rfft2(hp, s=(2 * h, 2 * w))
    ac = np.fft.irfft2(f * np.conj(f), s=(2 * h, 2 * w))
    if ac[0, 0] <= 0:
        return False
    ac /= ac[0, 0]
    ly, lx = min(h // 3, 200), min(w // 3, 200)
    win = np.concatenate([ac[:ly, :lx], ac[-ly:, :lx]], axis=0)
    yy = np.concatenate([np.arange(ly), np.arange(-ly, 0)])[:, None]
    xx = np.arange(lx)[None, :]
    win = np.where(yy ** 2 + xx ** 2 < 16, -1, win)
    # need two non-collinear repeat vectors (a 2-D lattice)
    i = np.unravel_index(np.argmax(win), win.shape)
    if win[i] < 0.2:
        return False
    v1 = np.array([yy[i[0], 0], xx[0, i[1]]], dtype=float)
    cross = np.abs(yy * v1[1] - xx * v1[0]) / (np.hypot(*v1) + 1e-9)
    win2 = np.where(cross < 3, -1, win)
    return win2.max() >= 0.15


def raster_clean_array(rgb):
    """Return a cleaned copy of an RGB uint8 array, or None if no watermark."""
    cand, hp, textured = raster_analyze(rgb.astype(np.int16))
    if not raster_has_dot_matrix(hp, textured):
        return None
    region = _box_count(textured, R_WIN) > 0
    out = rgb.copy()
    out[cand & region] = 255
    return out


def clean_raster_images(doc, page, done):
    """Best-effort whitening of dot-matrix marks burned into page images."""
    cleaned = 0
    for img in page.get_images(full=True):
        xref, smask = img[0], img[1]
        if xref in done:
            continue
        done.add(xref)
        if smask:
            continue
        try:
            pix = fitz.Pixmap(doc, xref)
            if pix.alpha:
                continue
            if pix.n != 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
        except Exception:
            continue
        if min(pix.width, pix.height) < R_MIN_SIDE:
            continue
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.stride)[:, :pix.width * 3].reshape(pix.height, pix.width, 3)
        out = raster_clean_array(rgb)
        if out is None:
            continue
        newpix = fitz.Pixmap(fitz.csRGB, pix.width, pix.height, out.tobytes(), False)
        try:
            page.replace_image(xref, pixmap=newpix)
        except Exception:
            continue
        cleaned += 1
    return cleaned
