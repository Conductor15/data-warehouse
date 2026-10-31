"""Chuyển sơ đồ SVG (report/figures/d_*.svg) sang PDF vector để chèn vào LaTeX.

    python report/tools/svg_to_pdf.py
"""
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

FIG = Path(__file__).resolve().parents[1] / "figures"

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page()
    for svg in sorted(FIG.glob("d_*.svg")):
        text = svg.read_text(encoding="utf-8")
        w, h = map(int, re.search(r"viewBox='0 0 (\d+) (\d+)'", text).groups())
        page.set_content(f"<html><body style='margin:0'>{text}</body></html>")
        page.pdf(path=str(svg.with_suffix(".pdf")), width=f"{w}px", height=f"{h + 1}px",
                 print_background=True, page_ranges="1")
        print("pdf:", svg.with_suffix(".pdf").name)
    browser.close()
