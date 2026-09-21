"""Getting text out of uploaded files, whatever form they arrive in.

Counties send .txt/.csv/.xlsx exports, .docx permits, native PDFs and — very
often — PDFs that are nothing but scanned images.  The parsers in this package
work on *text*, so this module's job is to produce text from any of those and
record honestly how it did so, because a value OCR'd off a faint scan does not
deserve the same confidence as one read from a spreadsheet cell.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from app.core.confidence import Confidence
from app.core.provenance import ExtractionMethod

#: Pages with less ink than this are blank scanner output.  Real permit pages
#: in the sample scans run 5-7% ink; blank separator sheets run under 0.05%.
BLANK_PAGE_INK_THRESHOLD = 0.004

#: Resolution for rasterising PDF pages before OCR.  300dpi is where tesseract
#: reads these county forms reliably; 150 loses the site-ID digits.
OCR_DPI = 300


@dataclass
class PageText:
    """Text recovered from one page, with how it was obtained."""

    page_number: int
    text: str
    method: str
    is_blank: bool = False
    ink_ratio: float | None = None

    @property
    def confidence(self) -> str:
        """OCR'd text is never as trustworthy as a real text layer."""
        return Confidence.MEDIUM if self.method == ExtractionMethod.OCR else Confidence.VERIFIED


@dataclass
class DocumentText:
    """All text recovered from a document."""

    text: str
    method: str
    pages: list[PageText] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def used_ocr(self) -> bool:
        return any(p.method == ExtractionMethod.OCR for p in self.pages) or (
            self.method == ExtractionMethod.OCR
        )


def sha256_file(path: str | Path) -> str:
    """Hash an uploaded file so re-uploads are detected instead of duplicated."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ocr_available() -> bool:
    return shutil.which("tesseract") is not None and shutil.which("pdftoppm") is not None


# ---------------------------------------------------------------------------
# .docx
# ---------------------------------------------------------------------------

def docx_text(path: str | Path) -> DocumentText:
    """Extract text from a .docx, preserving table-cell boundaries.

    Cell boundaries matter: a permit's CONTACT LIST and SITES LIST are tables,
    and the column split is the only thing separating a licence number from an
    expiry date.  They are emitted as ``|`` so downstream parsers can see them.
    """
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", "ignore")

    # Mark structure before stripping tags.
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"</w:tc>", " | ", xml)
    xml = re.sub(r"</w:tr>", "\n", xml)
    xml = re.sub(r"<w:br[^>]*/>", "\n", xml)
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    text = re.sub(r"<[^>]+>", "", xml)

    # Undo XML entity escaping.
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                         ("&apos;", "'"), ("&#8217;", "'"), ("&#8211;", "-")):
        text = text.replace(entity, char)

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return DocumentText(text=text.strip(), method=ExtractionMethod.DOCX_TEXT)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def _pdf_native_text(path: str | Path) -> list[PageText]:
    import pdfplumber

    pages: list[PageText] = []
    with pdfplumber.open(str(path)) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            pages.append(
                PageText(
                    page_number=index,
                    text=text,
                    method=ExtractionMethod.PDF_TEXT_LAYER,
                    is_blank=not text.strip(),
                )
            )
    return pages


def _page_ink_ratio(image_path: Path) -> float:
    """Fraction of dark pixels, used to skip blank scanner pages.

    Running OCR on a blank page does not return nothing — it returns pages of
    plausible-looking garbage read from scanner noise, which is far worse than
    returning nothing. Measuring ink first avoids that entirely.
    """
    from PIL import Image

    with Image.open(image_path) as image:
        grey = image.convert("L")
        # Downsample; we only need a coarse ink estimate.
        grey.thumbnail((400, 400))
        pixels = list(grey.getdata())
    if not pixels:
        return 0.0
    return sum(1 for value in pixels if value < 160) / len(pixels)


def _pdf_ocr_text(path: str | Path, *, dpi: int = OCR_DPI) -> list[PageText]:
    """Rasterise a PDF and OCR each non-blank page."""
    pages: list[PageText] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-gray", "-png", str(path), str(tmp / "page")],
            check=True,
            capture_output=True,
        )
        for index, image_path in enumerate(sorted(tmp.glob("page-*.png")), start=1):
            ink = _page_ink_ratio(image_path)
            if ink < BLANK_PAGE_INK_THRESHOLD:
                pages.append(
                    PageText(
                        page_number=index,
                        text="",
                        method=ExtractionMethod.OCR,
                        is_blank=True,
                        ink_ratio=ink,
                    )
                )
                continue
            result = subprocess.run(
                ["tesseract", str(image_path), "-", "--psm", "6"],
                capture_output=True,
                text=True,
                check=False,
            )
            pages.append(
                PageText(
                    page_number=index,
                    text=result.stdout or "",
                    method=ExtractionMethod.OCR,
                    ink_ratio=ink,
                )
            )
    return pages


def pdf_text(path: str | Path, *, allow_ocr: bool = True) -> DocumentText:
    """Read a PDF, falling back to OCR when it has no usable text layer."""
    notes: list[str] = []
    try:
        pages = _pdf_native_text(path)
    except Exception as exc:  # pragma: no cover - corrupt upload
        pages = []
        notes.append(f"PDF text layer unreadable ({exc}); will attempt OCR")

    native_chars = sum(len(p.text.strip()) for p in pages)
    # A handful of stray characters is not a text layer — scanned county
    # permits sometimes carry a stamp or a page number as real text.
    if native_chars > 200 * max(1, len([p for p in pages if not p.is_blank])) // 10:
        return DocumentText(
            text="\n\n".join(p.text for p in pages if p.text.strip()),
            method=ExtractionMethod.PDF_TEXT_LAYER,
            pages=pages,
            notes=notes,
        )

    notes.append(
        f"PDF has no usable text layer ({native_chars} characters across "
        f"{len(pages)} pages); treating it as a scan"
    )
    if not allow_ocr:
        return DocumentText(text="", method=ExtractionMethod.PDF_TEXT_LAYER, pages=pages,
                            notes=notes + ["OCR disabled"])
    if not ocr_available():
        return DocumentText(
            text="",
            method=ExtractionMethod.PDF_TEXT_LAYER,
            pages=pages,
            notes=notes + ["OCR unavailable: tesseract and poppler-utils are not installed"],
        )

    ocr_pages = _pdf_ocr_text(path)
    blank = sum(1 for p in ocr_pages if p.is_blank)
    if blank:
        notes.append(f"skipped {blank} blank page(s) rather than OCR scanner noise")
    notes.append(f"OCR'd {len(ocr_pages) - blank} page(s) at {OCR_DPI}dpi")
    return DocumentText(
        text="\n\n".join(p.text for p in ocr_pages if p.text.strip()),
        method=ExtractionMethod.OCR,
        pages=ocr_pages,
        notes=notes,
    )


def plain_text(path: str | Path) -> DocumentText:
    data = Path(path).read_text(encoding="utf-8", errors="replace")
    return DocumentText(text=data, method=ExtractionMethod.TABULAR_COLUMN)


def load_text(path: str | Path, *, allow_ocr: bool = True) -> DocumentText:
    """Read any supported document into text."""
    suffix = Path(path).suffix.lower()
    if suffix == ".docx":
        return docx_text(path)
    if suffix == ".pdf":
        return pdf_text(path, allow_ocr=allow_ocr)
    return plain_text(path)
