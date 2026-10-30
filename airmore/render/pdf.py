"""DOCX → PDF：通过 soffice 统一转换，保证与 DOCX 事实一致。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

SOFFICE_DEFAULT = "/opt/homebrew/bin/soffice"


def docx_to_pdf(docx_path: str | Path, out_dir: str | Path,
                soffice: str = SOFFICE_DEFAULT, timeout: float = 120) -> Path:
    docx_path = Path(docx_path)
    out_dir = Path(out_dir)
    if not shutil.which(soffice) and not Path(soffice).exists():
        raise FileNotFoundError(f"未找到 soffice: {soffice}")
    subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf",
         "--outdir", str(out_dir), str(docx_path)],
        check=True, capture_output=True, timeout=timeout,
    )
    pdf = out_dir / (docx_path.stem + ".pdf")
    if not pdf.exists() or pdf.stat().st_size == 0:
        raise RuntimeError(f"PDF 转换失败或为空: {pdf}")
    return pdf


def pdf_page_count(pdf_path: str | Path) -> int:
    """统计 PDF 页数（按 /Type/Page 页面对象计数，适用于 LibreOffice 输出）。"""
    data = Path(pdf_path).read_bytes()
    pages = data.count(b"/Type/Pages") + data.count(b"/Type /Pages")
    total = data.count(b"/Type/Page") + data.count(b"/Type /Page")
    return max(total - pages, 0)
