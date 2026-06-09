#!/usr/bin/env python3
"""Remove tiled pattern watermarks from a PDF.

Target watermarks are implemented as Form XObjects that:
  * have /Subtype /Form
  * reference a /Pattern in their Resources
  * draw a full-page filled rectangle with the pattern

These properties reliably distinguish a tiled "background watermark" layer
from regular text, images, diagrams, and vector artwork.

Usage:
    python remove_watermark.py <input.pdf> <output.pdf>
"""
import re
import sys

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERROR: PyMuPDF is required. Install with: pip install pymupdf", file=sys.stderr)
    sys.exit(2)


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


def remove_watermark(input_path: str, output_path: str) -> dict:
    doc = fitz.open(input_path)
    total_do_calls_removed = 0
    pages_touched = 0

    for page in doc:
        xobjects = page.get_xobjects()  # list of (xref, name, ...)
        name_to_xref = {t[1]: t[0] for t in xobjects}

        wm_names = [name for name, xref in name_to_xref.items()
                    if is_tiled_watermark_xobject(doc, xref)]
        if not wm_names:
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

        if page_changed:
            pages_touched += 1

    doc.save(output_path, garbage=4, deflate=True, clean=True)
    doc.close()

    return {
        "pages_touched": pages_touched,
        "do_calls_removed": total_do_calls_removed,
    }


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <input.pdf> <output.pdf>", file=sys.stderr)
        sys.exit(2)
    result = remove_watermark(sys.argv[1], sys.argv[2])
    print(f"Pages touched: {result['pages_touched']}")
    print(f"Removed 'Do' calls: {result['do_calls_removed']}")
    if result["do_calls_removed"] == 0:
        print("WARNING: no tiled-pattern watermarks were detected in the PDF.")
        print("If the file still contains visible watermarks they may be rendered")
        print("as plain images or text — this tool only handles pattern-tiled layers.")


if __name__ == "__main__":
    main()
