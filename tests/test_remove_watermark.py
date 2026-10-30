import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, HERE)

import fixtures  # noqa: E402
import remove_watermark as rw  # noqa: E402
from fixtures import fitz  # noqa: E402


def run(tmp_path, doc, rules=None):
    src, dst = tmp_path / "in.pdf", tmp_path / "out.pdf"
    doc.save(str(src))
    result = rw.remove_watermark(str(src), str(dst), rules)
    return result, fitz.open(str(src)), fitz.open(str(dst))


def light_drawings(page):
    out = []
    for d in page.get_drawings():
        for col in (d.get("fill"), d.get("color")):
            if col and min(col) > 0.8 and max(col) - min(col) < 0.05:
                out.append(d)
                break
    return out


def gray(page, dpi=60):
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)


@pytest.mark.parametrize("builder", [
    lambda: fixtures.vector_doc(),
    lambda: fixtures.vector_doc(opacity=True),
    fixtures.pattern_doc,
    fixtures.form_repeat_doc,
], ids=["vector-paths", "vector-extgstate-alpha", "tiling-pattern", "repeated-form"])
def test_vector_dot_matrix_removed(tmp_path, builder):
    result, before, after = run(tmp_path, builder())
    assert result["dot_matrix_marks_removed"] > 0
    assert result["pages_touched"] == 1
    assert before[0].get_text() == after[0].get_text()
    g = gray(after[0])
    blank = g[:, :int(g.shape[1] * 0.07)]  # left margin: no body text
    assert (blank < 255).mean() < 0.001
    assert (gray(before[0])[:, :blank.shape[1]] < 255).mean() > 0.05
    # dark text pixels preserved (an opaque watermark may have hidden some)
    assert (g < 128).sum() >= (gray(before[0]) < 128).sum()


def test_vector_rule_can_be_disabled(tmp_path):
    result, before, after = run(tmp_path, fixtures.vector_doc(),
                                rules=["tiled-pattern", "dot-matrix-raster"])
    assert result["dot_matrix_marks_removed"] == 0
    assert len(light_drawings(after[0])) == len(light_drawings(before[0]))


def test_raster_dot_matrix_removed(tmp_path):
    result, before, after = run(tmp_path, fixtures.raster_doc())
    assert result["raster_images_cleaned"] == 1
    gb, ga = gray(before[0], 100), gray(after[0], 100)
    light_b = ((gb >= 232) & (gb <= 254)).sum()
    light_a = ((ga >= 232) & (ga <= 254)).sum()
    assert light_a < 0.25 * light_b
    # body text and its anti-aliased edges are kept
    assert (ga < 200).sum() == pytest.approx((gb < 200).sum(), rel=0.01)
    assert np.array_equal(ga[gb < 200], gb[gb < 200])


def test_raster_rule_can_be_disabled(tmp_path):
    result, _, _ = run(tmp_path, fixtures.raster_doc(), rules=["dot-matrix"])
    assert result["raster_images_cleaned"] == 0


def test_legit_light_grey_vector_content_intact(tmp_path):
    result, before, after = run(tmp_path, fixtures.legit_light_grey_doc())
    assert result["dot_matrix_marks_removed"] == 0
    assert result["pages_touched"] == 0
    assert len(after[0].get_drawings()) == len(before[0].get_drawings())
    assert before[0].get_text() == after[0].get_text()
    assert np.array_equal(gray(before[0]), gray(after[0]))


def test_legit_light_grey_raster_content_intact(tmp_path):
    result, before, after = run(tmp_path, fixtures.light_raster_doc())
    assert result["raster_images_cleaned"] == 0
    assert np.array_equal(gray(before[0]), gray(after[0]))


def test_tiled_pattern_rule_still_works(tmp_path):
    result, before, after = run(tmp_path, fixtures.legacy_pattern_form_doc())
    assert result["do_calls_removed"] == 1
    assert result["pages_touched"] == 1
    assert before[0].get_text() == after[0].get_text()


def test_tiled_pattern_rule_can_be_disabled(tmp_path):
    result, _, _ = run(tmp_path, fixtures.legacy_pattern_form_doc(), rules=["dot-matrix"])
    assert result["do_calls_removed"] == 0


def test_cli_rules_and_disable(tmp_path, capsys):
    src, dst = tmp_path / "in.pdf", tmp_path / "out.pdf"
    fixtures.vector_doc().save(str(src))
    rw.main([str(src), str(dst), "--disable", "dot-matrix"])
    out = capsys.readouterr().out
    assert "Removed dot-matrix marks: 0" in out and "WARNING" in out
    rw.main([str(src), str(dst), "--rules", "dot-matrix"])
    out = capsys.readouterr().out
    assert "Pages touched: 1" in out and "WARNING" not in out
    with pytest.raises(SystemExit):
        rw.main([str(src), str(dst), "--rules", "nope"])


def test_tokenizer_handles_strings_and_inline_images():
    import dot_matrix
    data = b"BT (a \\) re ( f ) ) Tj ET BI /W 1 ID \x00 re EI 0 0 1 1 re f"
    ops = [v for k, v, s, e in dot_matrix.tokenize(data) if k == "op"]
    assert ops == [b"BT", b"Tj", b"ET", b"BI", b"ID", b"re", b"f"]
