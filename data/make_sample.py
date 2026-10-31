"""Tạo bộ dữ liệu mẫu nhỏ (data/sample/) — 2.000 đơn ngẫu nhiên kèm toàn bộ dòng liên quan,
giữ nguyên định dạng nguồn, đủ để chạy thử pipeline:
    OLIST_RAW_DIR=data/sample OLIST_DB=warehouse/sample.duckdb python src/etl/pipeline.py

    python data/make_sample.py
"""
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
RAW, OUT = HERE / "raw", HERE / "sample"

con = duckdb.connect()
r = lambda f: f"read_csv('{(RAW / f).as_posix()}', header=true, all_varchar=true)"
OUT.mkdir(exist_ok=True)
con.execute(f"CREATE TABLE o AS SELECT * FROM {r('olist_orders_dataset.csv')} USING SAMPLE 2000 ROWS (reservoir, 42)")
queries = {
    "olist_orders_dataset.csv": "SELECT * FROM o",
    "olist_order_items_dataset.csv": f"SELECT * FROM {r('olist_order_items_dataset.csv')} WHERE order_id IN (SELECT order_id FROM o)",
    "olist_order_payments_dataset.csv": f"SELECT * FROM {r('olist_order_payments_dataset.csv')} WHERE order_id IN (SELECT order_id FROM o)",
    "olist_order_reviews_dataset.csv": f"SELECT * FROM {r('olist_order_reviews_dataset.csv')} WHERE order_id IN (SELECT order_id FROM o)",
    "olist_customers_dataset.csv": f"SELECT * FROM {r('olist_customers_dataset.csv')} WHERE customer_id IN (SELECT customer_id FROM o)",
}
for name, sql in queries.items():
    con.execute(f"COPY ({sql}) TO '{(OUT / name).as_posix()}' (HEADER, DELIMITER ',')")
con.execute(f"CREATE TABLE i AS SELECT * FROM read_csv('{(OUT / 'olist_order_items_dataset.csv').as_posix()}', all_varchar=true)")
con.execute(f"CREATE TABLE c AS SELECT * FROM read_csv('{(OUT / 'olist_customers_dataset.csv').as_posix()}', all_varchar=true)")
rest = {
    "olist_products_dataset.csv": f"SELECT * FROM {r('olist_products_dataset.csv')} WHERE product_id IN (SELECT product_id FROM i)",
    "olist_sellers_dataset.csv": f"SELECT * FROM {r('olist_sellers_dataset.csv')} WHERE seller_id IN (SELECT seller_id FROM i)",
    "olist_geolocation_dataset.csv": f"""SELECT * FROM {r('olist_geolocation_dataset.csv')} WHERE geolocation_zip_code_prefix IN
        (SELECT lpad(customer_zip_code_prefix, 5, '0') FROM c UNION SELECT lpad(seller_zip_code_prefix, 5, '0')
         FROM {r('olist_sellers_dataset.csv')} WHERE seller_id IN (SELECT seller_id FROM i))
        QUALIFY row_number() OVER (PARTITION BY geolocation_zip_code_prefix ORDER BY geolocation_lat) <= 5""",  # tối đa 5 dòng/zip: vẫn giữ tính trùng lặp của nguồn
    "product_category_name_translation.csv": f"SELECT * FROM {r('product_category_name_translation.csv')}",
}
for name, sql in rest.items():
    con.execute(f"COPY ({sql}) TO '{(OUT / name).as_posix()}' (HEADER, DELIMITER ',')")
for f in sorted(OUT.glob("*.csv")):
    print(f"{f.name:45s} {sum(1 for _ in open(f, encoding='utf-8')) - 1:>8,} dòng")
