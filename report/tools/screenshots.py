"""Chụp ảnh màn hình dashboard cho báo cáo (cần dashboard đang chạy ở cổng 8599).

    streamlit run src/app/app.py --server.port 8599
    python report/tools/screenshots.py
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[1] / "figures" / "screens"
BASE = "http://localhost:8599"
PAGES = {
    "home": ("", 2300),
    "sales": ("sales", 2600),
    "delivery": ("delivery", 2300),
    "olap": ("olap", 1900),
    "dss1": ("dss1", 2500),
    "dss2": ("dss2", 2100),
    "dss3": ("dss3", 2900),
    "etl": ("etl", 2400),
}


def main(only: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        for name, (path, height) in PAGES.items():
            if only and name not in only:
                continue
            page = browser.new_page(viewport={"width": 1500, "height": height}, device_scale_factor=1.5)
            page.goto(f"{BASE}/{path}", wait_until="networkidle", timeout=120_000)
            page.wait_for_selector("[data-testid='stAppViewContainer']", timeout=60_000)
            page.wait_for_function(
                "() => !document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=180_000)
            page.wait_for_timeout(5000)
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=False)
            print("saved", name)
            page.close()
        browser.close()


if __name__ == "__main__":
    main(sys.argv[1:])
