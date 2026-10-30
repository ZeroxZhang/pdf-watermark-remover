# PDF Watermark Remover

[English](#english) | [中文](#中文)

---

<a name="english"></a>

## English

A lightweight tool to remove **tiled-pattern background watermarks** from PDF files while preserving text, images, diagrams, and layout.

It reliably handles the most common enterprise watermark scheme — a watermark implemented as a **Form XObject + Pattern tiling + full-page rectangle fill** — used by many export tools and internal systems.

### Features

- Removes tiled pattern Form XObject watermarks
- Strips per-page `/Xxx Do` invocations of the watermark layer
- Cleans up orphaned watermark objects on save (`garbage=4`)
- Removes **tiled binary dot-matrix watermarks** (a staggered page-wide grid of tiny "=" / "||" glyphs in very light grey), whether drawn as vector paths (incl. ExtGState transparency), a tiling Pattern, repeated small Form XObjects, or — best effort — burned into raster images
- Every detection rule can be toggled independently from the CLI
- Leaves genuine page content untouched: text, images, tables, charts, vector diagrams, embedded fonts, annotations, bookmarks, links, and metadata

### What it does NOT remove

- Arbitrary watermarks baked into raster images (only the tiled binary dot-matrix kind is handled, best effort)
- Plain text placed on each page by the authoring tool
- Watermarks encoded as Soft Masks (SMask) on page content

If the script reports `Removed 'Do' calls: 0`, the document uses a different watermark mechanism.

### Requirements

- Python 3
- [PyMuPDF](https://pymupdf.readthedocs.io/)

```bash
pip install -r requirements.txt   # pymupdf, numpy
```

### Usage

```bash
python3 scripts/remove_watermark.py <input.pdf> <output.pdf>
python3 scripts/remove_watermark.py --disable dot-matrix-raster <input.pdf> <output.pdf>
python3 scripts/remove_watermark.py --rules tiled-pattern <input.pdf> <output.pdf>
```

| Rule | What it removes |
|------|-----------------|
| `tiled-pattern` | Form XObject layers that fill the page with a Pattern |
| `dot-matrix` | Tiled binary dot-matrix marks as vector paths, tiling Patterns or repeated small Form XObjects |
| `dot-matrix-raster` | Tiled binary dot-matrix marks burned into raster images (best effort) |

All rules are enabled by default. `--rules` takes a comma-separated list; `--disable RULE` may be repeated.

Example output:

```
Pages touched: 12
Removed 'Do' calls: 24
Removed dot-matrix marks: 0
Cleaned raster images: 0
```

### How it works

The tool scans each page's Form XObjects and identifies watermark layers by these reliable signals:

- `/Subtype /Form`
- references a `/Pattern` resource
- draws a full-page filled rectangle using `/Pattern cs` color space

It then removes the corresponding `/Xxx Do` calls from page content streams and rewrites the file, garbage-collecting the now-orphaned objects.

#### Tiled binary dot-matrix watermarks

Colour alone is never used, so legitimate light-grey table rules, shading and text are kept. Content-stream paint operations are parsed (tracking CTM, fill/stroke colour and ExtGState alpha) and a set of light neutral-grey marks is only removed when **all** of these hold:

- each mark is small and bar shaped, bars come in parallel pairs (`=` / `||`) and both orientations are present
- there are many marks and together they span most of the page (or Form XObject)
- mark positions repeat periodically on both axes (autocorrelation)

Tiling Patterns whose cell holds only such bars, and tiny Form XObjects that only draw such bars and are repeated in the same lattice, are handled the same way. For raster images, light near-white pixels away from dark content are analysed as a high-pass texture; an image is cleaned only if that texture shows bar-shaped marks in both orientations and a 2-D periodic lattice covering most of the image. Text, lines and their anti-aliased edges (and anything darker than grey level 232) are never modified.

Limitations: raster removal is best effort — faint residue may remain right next to text, and very light content (≥ grey 232) inside a watermarked image is flattened to white. Vector removal also drops any genuine light, bar-shaped marks that happen to sit inside a detected lattice.

### Tests

```bash
pip install -r requirements.txt pytest
python3 -m pytest tests
```

Fixtures are generated programmatically in `tests/fixtures.py`.

### License

This project is licensed under the [MIT License](LICENSE).

---

<a name="中文"></a>

## 中文

一个轻量级工具，用于去除 PDF 文件中的**平铺图案背景水印**，同时完整保留文本、图片、图表和版式。

它能可靠地处理最常见的企业级水印方案——以 **Form XObject + Pattern 平铺 + 整页矩形填充** 实现的水印，这种方案被众多导出工具和内部系统广泛使用。

### 功能特性

- 去除平铺图案的 Form XObject 水印
- 移除每页中调用水印图层的 `/Xxx Do` 指令
- 保存时清理水印残留的孤立对象（`garbage=4`）
- 去除**平铺二值点阵水印**（整页交错排列的极浅灰色 “=” / “||” 小字形），支持矢量路径（含 ExtGState 透明度）、平铺 Pattern、重复的小型 Form XObject，以及尽力而为地处理烘焙进位图的情况
- 每条检测规则都可通过命令行单独开关
- 不影响真实页面内容：文本、图片、表格、图表、矢量图、内嵌字体、批注、书签、链接和元数据

### 无法去除的水印类型

- 烘焙进位图图像中的任意水印（仅尽力处理平铺二值点阵水印）
- 由排版工具直接以普通文本形式放置在每页上的水印
- 以软蒙版（SMask）形式编码在页面内容上的水印

如果脚本输出 `Removed 'Do' calls: 0`，说明该文档使用了其他水印机制。

### 环境要求

- Python 3
- [PyMuPDF](https://pymupdf.readthedocs.io/)

```bash
pip install -r requirements.txt   # pymupdf, numpy
```

### 使用方法

```bash
python3 scripts/remove_watermark.py <输入.pdf> <输出.pdf>
python3 scripts/remove_watermark.py --disable dot-matrix-raster <输入.pdf> <输出.pdf>
python3 scripts/remove_watermark.py --rules tiled-pattern <输入.pdf> <输出.pdf>
```

规则：`tiled-pattern`（Pattern 平铺 Form 图层）、`dot-matrix`（矢量 / Pattern / 重复 Form 形式的平铺二值点阵水印）、`dot-matrix-raster`（位图中的平铺二值点阵水印，尽力而为）。默认全部启用；`--rules` 接受逗号分隔列表，`--disable` 可重复使用。

输出示例：

```
Pages touched: 12
Removed 'Do' calls: 24
```

### 工作原理

该工具会扫描每一页的 Form XObject，并通过以下可靠特征识别水印图层：

- `/Subtype /Form`
- 引用了 `/Pattern` 资源
- 使用 `/Pattern cs` 颜色空间绘制整页矩形填充

随后从页面内容流中移除对应的 `/Xxx Do` 调用，并重写文件，回收已成为孤立对象的水印数据。

对于平铺二值点阵水印，工具不会仅凭颜色判断：只有当浅灰色标记同时满足“细小条形且成对平行、横竖两种方向都存在”“数量多且覆盖大部分页面”“位置在两个方向上周期性重复”时才会删除，因此正常的浅灰表格线、底纹和浅色文字会被保留。位图处理为尽力而为：文字及其抗锯齿边缘不会被修改，但文字附近可能残留少量痕迹，水印图像中灰度 ≥ 232 的极浅内容会被压成白色。

测试：`python3 -m pytest tests`（测试用例在 `tests/fixtures.py` 中程序化生成）。

### 开源协议

本项目基于 [MIT 协议](LICENSE) 开源。
