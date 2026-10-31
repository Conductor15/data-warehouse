"""Biên dịch báo cáo LaTeX (template HCMUT) và xuất slide sang PDF.

    python report/tools/build_pdf.py

Báo cáo: report/latex/main.tex -> pdflatex -> bibtex -> pdflatex x2 -> report/Bao_cao_BTL_CO4031.pdf
Slide  : slides/slides.html -> Chrome headless -> slides/Slide_BTL_CO4031.pdf
"""
import shutil
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
TEX_DIR = ROOT / "report" / "latex"
REPORT_PDF = ROOT / "report" / "Bao_cao_BTL_CO4031.pdf"
SLIDES = (ROOT / "slides" / "slides.html", ROOT / "slides" / "Slide_BTL_CO4031.pdf")


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, cwd=TEX_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def build_report() -> None:
    pdflatex = ["pdflatex", "-interaction=nonstopmode", "main.tex"]
    run(pdflatex)
    run(["bibtex", "main"])
    run(pdflatex)
    run(pdflatex)
    log = (TEX_DIR / "main.log").read_text(encoding="latin-1")
    errors = [line for line in log.splitlines() if line.startswith("!")]
    if errors:
        raise SystemExit("LaTeX lỗi:\n" + "\n".join(errors))
    shutil.copyfile(TEX_DIR / "main.pdf", REPORT_PDF)
    print("PDF:", REPORT_PDF)


def build_slides() -> None:
    src, out = SLIDES
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        page.goto(src.as_uri(), wait_until="networkidle")
        page.wait_for_timeout(800)
        page.pdf(path=str(out), print_background=True, prefer_css_page_size=True)
        browser.close()
    print("PDF:", out)


if __name__ == "__main__":
    build_report()
    build_slides()
