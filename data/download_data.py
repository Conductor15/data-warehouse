"""Tải bộ dữ liệu Olist (9 tệp CSV, ~125 MB) từ repository công khai của Olist vào data/raw/.

    python data/download_data.py
Nguồn gốc: Brazilian E-Commerce Public Dataset by Olist (Kaggle) — bản phát hành trên GitHub olist/work-at-olist-data.
"""
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/olist/work-at-olist-data/master/datasets/"
FILES = [
    "olist_customers_dataset.csv", "olist_geolocation_dataset.csv", "olist_order_items_dataset.csv",
    "olist_order_payments_dataset.csv", "olist_order_reviews_dataset.csv", "olist_orders_dataset.csv",
    "olist_products_dataset.csv", "olist_sellers_dataset.csv", "product_category_name_translation.csv",
]
RAW = Path(__file__).resolve().parent / "raw"

if __name__ == "__main__":
    RAW.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        dest = RAW / f
        if dest.exists():
            print("đã có ", f)
            continue
        print("tải   ", f)
        urllib.request.urlretrieve(BASE + f, dest)
    print("Xong:", RAW)
