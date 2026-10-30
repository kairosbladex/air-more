"""Render using source-preserved Word components and isolated PDF conversion."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib import font_manager
from matplotlib.ft2font import FT2Font

from airmore.regional.render import convert_pdf
from .config import Settings
from .models import ReportError


def preflight(cfg: Settings):
    executable = shutil.which(cfg.soffice)
    if not executable:
        raise ReportError("RENDER_RUNTIME_MISSING", "未找到配置的LibreOffice转换器")
    for family in ("Songti SC", "SimSun", "Noto Serif CJK SC", "PingFang SC", "Noto Sans CJK SC"):
        try:
            path = font_manager.findfont(font_manager.FontProperties(family=[family]), fallback_to_default=False)
            font = FT2Font(path)
            if all(font.get_char_index(ord(c)) for c in "机库空气质量研判报告μ³℃"):
                break
        except (ValueError, RuntimeError):
            continue
    else:
        raise ReportError("RENDER_FONT_MISSING", "缺少可用中文字体")
    env = os.environ.copy()
    if not env.get("FONTCONFIG_FILE") and Path("/opt/homebrew/etc/fonts/fonts.conf").is_file():
        env["FONTCONFIG_FILE"] = "/opt/homebrew/etc/fonts/fonts.conf"
    version = subprocess.run([executable, "--version"], timeout=20, check=True, capture_output=True, text=True, env=env).stdout.strip()
    local_name = {"Songti SC": "宋体-简", "PingFang SC": "苹方-简"}.get(family, family) if sys.platform == "darwin" else family
    return {"soffice": executable, "version": version, "font_family": family,
            "docx_font_family": local_name, "font_path": str(path), "env": env}



def render(stats, analyses, composite, cfg: Settings, output: Path):
    # Site exports must render the statistics loaded for the selected sites.
    # A city enrichment flag must never replace those measurements with a
    # regional bulletin. Regional reports have their own separate pipeline.
    from .approved_report import render_document
    runtime = preflight(cfg)
    result = render_document(stats, analyses, composite, cfg, output, runtime)
    convert_pdf(output / 'report.docx', runtime)
    return result
