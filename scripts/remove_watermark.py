#!/usr/bin/env python3
"""Remove tiled background watermarks from a PDF.

Detection rules (each can be toggled independently, all enabled by default):

  tiled-pattern       Form XObject layers that fill the page with a Pattern
  dot-matrix          tiled binary dot-matrix marks drawn as vector paths,
                      tiling Patterns or repeated small Form XObjects
  dot-matrix-raster   tiled binary dot-matrix marks burned into raster
                      images (best effort, pixel level)

The tiled-pattern rule targets Form XObjects that:
  * have /Subtype /Form
  * reference a /Pattern in their Resources
  * draw a full-page filled rectangle with the pattern

These properties reliably distinguish a tiled "background watermark" layer
from regular text, images, diagrams, and vector artwork.

The dot-matrix rules are implemented in dot_matrix.py.

Usage:
    python remove_watermark.py [--rules LIST] [--disable RULE] <input.pdf> <output.pdf>
"""
import argparse
import os
import re
import sys

try:
    import pymupdf as fitz  # PyMuPDF
except ImportError:
    try:
        import fitz  # older PyMuPDF releases
    except ImportError:
        fitz = None
if fitz is None:
    print("ERROR: PyMuPDF is required. Install with: pip install pymupdf", file=sys.stderr)
    sys.exit(2)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dot_matrix  # noqa: E402

RULES = ("tiled-pattern", "dot-matrix", "dot-matrix-raster")


def is_tiled_watermark_xobject(doc, xref):
    """Return True if the xref points at a Form XObject that draws a tiled pattern fill."""
    try:
        obj = doc.xref_object(xref)
    except Exception:
        return False
    # Must be a Form XObject
    if "/Subtype /Form" not in obj and "/Subtype/Form" not in obj:
        return False
    # Must reference a Pattern resource (the tile)
    if "/Pattern" not in obj:
        return False
    try:
        stream = doc.xref_stream(xref).decode("latin1")
    except Exception:
        return False
    # Pattern colorspace selection + rectangle + fill = tiled watermark
    return "/Pattern cs" in stream and " re" in stream


def remove_watermark(input_path: str, output_path: str, rules=None) -> dict:
    """Remove watermarks using the enabled rules (default: all of RULES)."""
    rules = set(RULES if rules is None else rules)
    unknown = rules - set(RULES)
    if unknown:
        raise ValueError("unknown rule(s): %s" % ", ".join(sorted(unknown)))
    doc = fitz.open(input_path)
    total_do_calls_removed = 0
    dot_marks_removed = 0
    raster_images_cleaned = 0
    pages_touched = 0
    analyzer = dot_matrix.Analyzer(doc)
    visited_forms, visited_images = set(), set()

    for page in doc:
        touched = False
        if "dot-matrix" in rules:
            n = dot_matrix.remove_vector_dot_matrix(doc, page, analyzer, visited_forms)
            dot_marks_removed += n
            touched |= n > 0
        if "dot-matrix-raster" in rules:
            n = dot_matrix.clean_raster_images(doc, page, visited_images)
            raster_images_cleaned += n
            touched |= n > 0
        if "tiled-pattern" not in rules:
            pages_touched += touched
            continue
        xobjects = page.get_xobjects()  # list of (xref, name, ...)
        name_to_xref = {t[1]: t[0] for t in xobjects}

        wm_names = [name for name, xref in name_to_xref.items()
                    if is_tiled_watermark_xobject(doc, xref)]
        if not wm_names:
            pages_touched += touched
            continue

        page_changed = False
        for stream_xref in page.get_contents():
            try:
                stream_bytes = doc.xref_stream(stream_xref)
            except Exception:
                continue
            original = stream_bytes.decode("latin1")
            updated = original
            for name in wm_names:
                pattern = r"/" + re.escape(name) + r"\s+Do"
                new_text, n = re.subn(pattern, "", updated)
                if n > 0:
                    total_do_calls_removed += n
                    page_changed = True
                updated = new_text
            if updated != original:
                doc.update_stream(stream_xref, updated.encode("latin1"))

        if page_changed or touched:
            pages_touched += 1

    doc.save(output_path, garbage=4, deflate=True, clean=True)
    doc.close()

    return {
        "pages_touched": pages_touched,
        "do_calls_removed": total_do_calls_removed,
        "dot_matrix_marks_removed": dot_marks_removed,
        "raster_images_cleaned": raster_images_cleaned,
    }


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Remove tiled background watermarks from a PDF.")
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--rules", default=",".join(RULES),
                    help="comma-separated rules to enable (default: %(default)s)")
    ap.add_argument("--disable", action="append", default=[], choices=RULES,
                    metavar="RULE", help="disable a rule; may be repeated")
    args = ap.parse_args(argv)
    enabled = [r.strip() for r in args.rules.split(",") if r.strip()]
    bad = [r for r in enabled if r not in RULES]
    if bad:
        ap.error("unknown rule(s): %s (choose from %s)" % (", ".join(bad), ", ".join(RULES)))
    args.enabled = [r for r in enabled if r not in args.disable]
    return args


def main(argv=None):
    args = parse_args(argv)
    result = remove_watermark(args.input, args.output, args.enabled)
    print(f"Pages touched: {result['pages_touched']}")
    print(f"Removed 'Do' calls: {result['do_calls_removed']}")
    print(f"Removed dot-matrix marks: {result['dot_matrix_marks_removed']}")
    print(f"Cleaned raster images: {result['raster_images_cleaned']}")
    if not (result["do_calls_removed"] or result["dot_matrix_marks_removed"]
            or result["raster_images_cleaned"]):
        print("WARNING: no supported watermarks were detected in the PDF.")
        print("If the file still contains visible watermarks they may use a")
        print("mechanism this tool does not handle (e.g. plain text or SMask).")


if __name__ == "__main__":
    main()
