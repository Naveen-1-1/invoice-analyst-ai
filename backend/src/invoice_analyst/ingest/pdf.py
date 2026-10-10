"""PDF text layer (pdfplumber) and page rendering (pypdfium2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
import pypdfium2

MIN_PAGE_CHARS = 20  # fewer printable characters than this on a page means "no usable text layer"
RENDER_DPI = 200


@dataclass
class PdfPage:
    page_no: int
    width_pt: float
    height_pt: float
    text: str  # layout-preserving text
    # [{"t": text, "bbox": [x0, y0, x1, y1] normalised 0-1 (origin top-left), "src": "pdf_text"}]
    words: list[dict] = field(default_factory=list)

    @property
    def has_text(self) -> bool:
        stripped = "".join(self.text.split())
        return len(stripped) >= MIN_PAGE_CHARS and "(cid:" not in self.text


@dataclass
class PdfText:
    pages: list[PdfPage]

    @property
    def layer(self) -> str:
        """full / partial / none, judged page by page."""
        with_text = sum(p.has_text for p in self.pages)
        if with_text == len(self.pages) and self.pages:
            return "full"
        return "partial" if with_text else "none"

    def marked_text(self) -> str:
        n = len(self.pages)
        return "\n".join(f"<<<PAGE {p.page_no} of {n}>>>\n{p.text}" for p in self.pages)


def read_text(path: Path) -> PdfText:
    pages = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            w, h = float(page.width), float(page.height)
            words = [{"t": wd["text"], "bbox": [wd["x0"] / w, wd["top"] / h, wd["x1"] / w, wd["bottom"] / h],
                      "src": "pdf_text"}
                     for wd in page.extract_words(keep_blank_chars=False, use_text_flow=False)]
            pages.append(PdfPage(i, w, h, page.extract_text(layout=True) or "", words))
    return PdfText(pages)


def render_pages(path: Path, out_dir: Path, dpi: int = RENDER_DPI) -> list[Path]:
    """Render every page to PNG at `dpi` (idempotent); returns the image paths in page order."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = pypdfium2.PdfDocument(str(path))
    try:
        paths = []
        for i in range(len(pdf)):
            target = out_dir / f"{i + 1}.png"
            if not target.exists():
                image = pdf[i].render(scale=dpi / 72).to_pil()
                image.save(target, "PNG")
            paths.append(target)
        return paths
    finally:
        pdf.close()
