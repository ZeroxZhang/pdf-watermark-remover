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
- Leaves genuine page content untouched: text, images, tables, charts, vector diagrams, embedded fonts, annotations, bookmarks, links, and metadata

### What it does NOT remove

- Watermarks baked into raster images inside the PDF
- Plain text placed on each page by the authoring tool
- Watermarks encoded as Soft Masks (SMask) on page content

If the script reports `Removed 'Do' calls: 0`, the document uses a different watermark mechanism.

### Requirements

- Python 3
- [PyMuPDF](https://pymupdf.readthedocs.io/)

```bash
pip install pymupdf
```

### Usage

```bash
python3 scripts/remove_watermark.py <input.pdf> <output.pdf>
```

Example output:

```
Pages touched: 12
Removed 'Do' calls: 24
```

### How it works

The tool scans each page's Form XObjects and identifies watermark layers by these reliable signals:

- `/Subtype /Form`
- references a `/Pattern` resource
- draws a full-page filled rectangle using `/Pattern cs` color space

It then removes the corresponding `/Xxx Do` calls from page content streams and rewrites the file, garbage-collecting the now-orphaned objects.

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
- 不影响真实页面内容：文本、图片、表格、图表、矢量图、内嵌字体、批注、书签、链接和元数据

### 无法去除的水印类型

- 烘焙进 PDF 内部位图图像中的水印
- 由排版工具直接以普通文本形式放置在每页上的水印
- 以软蒙版（SMask）形式编码在页面内容上的水印

如果脚本输出 `Removed 'Do' calls: 0`，说明该文档使用了其他水印机制。

### 环境要求

- Python 3
- [PyMuPDF](https://pymupdf.readthedocs.io/)

```bash
pip install pymupdf
```

### 使用方法

```bash
python3 scripts/remove_watermark.py <输入.pdf> <输出.pdf>
```

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

### 开源协议

本项目基于 [MIT 协议](LICENSE) 开源。
