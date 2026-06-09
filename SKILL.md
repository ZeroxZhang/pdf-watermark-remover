---
name: pdf-watermark-remover
description: >
  Remove tiled-pattern background watermarks from PDF files.
  Use this skill whenever the user asks to "remove watermark", "去除水印", "去水印",
  "clean background watermark", "strip PDF watermark", or mentions that a PDF has
  a repeated tiled logo/text watermark covering every page. It handles the typical
  enterprise watermark implemented as a Form XObject with a Pattern tiling fill.
compatibility:
  tools: [Bash, Read, Write, mcp__runtime__upload_file]
---

# PDF Watermark Remover

Removes tiled-pattern background watermarks from PDF documents while preserving
text, images, diagrams, and layout. Works reliably on documents whose watermark
is implemented as a **Form XObject + Pattern tiling + full-page rectangle fill**
— the most common scheme used by enterprise export tools and internal systems.

## When to use

Trigger on any of the following:
- Explicit request: "去水印", "去除PDF水印", "remove watermark from this PDF", "clean background watermark"
- Implicit: user uploads a PDF and complains about repeated background text or logos
- File naming hints such as `*_watermarked.pdf`, `*_带水印.pdf`

Do NOT use this skill if:
- The "watermark" is actually a scan of stamped text overlaid on a page image — that
  is an image-processing problem, not a PDF structure problem.
- The watermark is rendered as regular visible text on each page (not an XObject layer).

## Capabilities and limitations

| What it removes | What it leaves alone |
|-----------------|----------------------|
| Tiled pattern Form XObject watermarks | Genuine page content (text, images, tables, charts) |
| Per-page `/Xxx Do` invocations of the watermark layer | Vector diagrams and embedded fonts |
| Orphaned watermark objects (via `garbage=4` on save) | Annotations, bookmarks, links, metadata |

It will NOT remove:
- Watermarks baked into raster images inside the PDF
- Plain text placed on each page by the authoring tool
- Watermarks encoded as Soft Masks (SMask) on page content

If the script reports `Removed 'Do' calls: 0` the document uses a different
watermark mechanism; report this honestly to the user and offer alternatives
(e.g., re-exporting from the source, or rendering + re-compositing as a last
resort).

## Workflow

Follow this exact sequence.

### Step 1 — Locate the input PDF

- If the user uploaded the file in this turn, the file is already saved in the
  current session directory (find it with `ls`).
- If the user references a path, use that path directly.
- Confirm the file exists and is a real PDF:
  ```bash
  file <input.pdf>
  ```

### Step 2 — Ensure PyMuPDF is available

Check once per session:
```bash
python3 -c "import fitz; print(fitz.version)" || pip install --user --break-system-packages pymupdf
```

### Step 3 — Run the bundled script

The script lives at `scripts/remove_watermark.py` next to this SKILL.md.
Run it with an absolute output path in the current session directory:
```bash
python3 "<skill-dir>/scripts/remove_watermark.py" <input.pdf> <output_nowm.pdf>
```

Expected stdout example:
```
Pages touched: 12
Removed 'Do' calls: 24
```

If `Removed 'Do' calls` is 0, see the "limitations" section above and do not
pretend the file was watermark-stripped.

### Step 4 — Quality check

Render the **first page** (or the user's page of interest) before and after to
visually confirm the watermark is gone and content is intact:
```bash
python3 - <<'PY'
import fitz
for src, label in [("input.pdf", "before"), ("output_nowm.pdf", "after")]:
    d = fitz.open(src)
    d[0].get_pixmap(dpi=110).save(f"/tmp/{label}.png")
PY
```
Read both PNGs with the Read tool and compare mentally — do not skip this step.

### Step 5 — Deliver the output

1. Upload the cleaned PDF with `mcp__runtime__upload_file`.
2. Report:
   - how many pages were touched
   - how many watermark `Do` calls were removed
   - the download URL
   - a short note if anything looked odd during the visual check

## Error handling

- **`ImportError: No module named fitz`** → `pip install --user --break-system-packages pymupdf`
- **MuPDF stderr warnings `cannot find object in xref`** during save: benign noise
  from `garbage=4` collecting orphaned Pattern objects; the output file is still
  valid and complete.
- **Output file larger than input**: expected when deflate + clean rewrites streams
  with different compression settings; quality is not reduced.
- **Script exits 2**: wrong number of arguments — double-check the CLI invocation.

## File layout

```
pdf-watermark-remover/
├── SKILL.md
└── scripts/
    └── remove_watermark.py   # PyMuPDF-based stripper
```
