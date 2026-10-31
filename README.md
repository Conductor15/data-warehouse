# Olist DW & DSS — Bài tập lớn CO4031 (HK261)

Kho dữ liệu và hệ hỗ trợ quyết định cho sàn thương mại điện tử Olist (Brazil):
**star schema Kimball** (5 fact · 7 dimension · bridge), **ETL tăng dần** có SCD 2/6 và accumulating snapshot,
**OLAP** (14 truy vấn + dashboard 8 trang) và **3 mô-đun DSS** (cam kết ngày giao, cảnh báo giao trễ, đánh giá người bán).

## Chạy nhanh

```bash
pip install -r requirements.txt
python data/download_data.py        # tải 9 CSV Olist vào data/raw/ (~125 MB)
python src/etl/pipeline.py          # staging + initial load + 10 batch incremental (~30 giây)
python src/dss/build_dss.py         # chạy mô hình DSS, ghi kết quả vào kho (~2 phút)
python -m pytest src/tests -q       # 24 kiểm thử
streamlit run src/app/app.py        # dashboard: http://localhost:8501
```

Chạy thử trên dữ liệu mẫu (2.000 đơn, đã có sẵn trong `data/sample/`):

```bash
OLIST_RAW_DIR=data/sample OLIST_DB=warehouse/sample.duckdb python src/etl/pipeline.py
```

## Cấu trúc

| Đường dẫn | Nội dung |
|---|---|
| `src/sql/01_staging.sql` | Extract (landing VARCHAR) + làm sạch (12 luật DQ) |
| `src/sql/02_dw_schema.sql` | DDL: dimension, fact, bridge, metadata ETL |
| `src/sql/03_static_dims.sql` | dim_date, junk dimension, unknown members |
| `src/sql/10_batch_load.sql` | Transform + Load 1 batch (as-of source, SCD2/6, accumulating, periodic snapshot) |
| `src/sql/04_mart.sql` | Semantic layer (view + aggregate table) |
| `src/sql/05_dq_checks.sql` | 26 kiểm tra CLEANSING / RI / RECONCILIATION / BUSINESS |
| `src/sql/olap_queries.sql` | 14 truy vấn OLAP (ROLLUP, CUBE, GROUPING SETS, PIVOT, drill-across…) |
| `src/etl/pipeline.py` | Orchestrator: watermark, 1 transaction/batch, log, fail-fast |
| `src/dss/` | `delivery_promise.py`, `late_risk.py`, `seller_mcdm.py`, `build_dss.py` |
| `src/app/` | Dashboard Streamlit (`app.py` + `pages/`) |
| `src/tests/` | Kiểm thử pytest |
| `data/` | `download_data.py`, `make_sample.py`, `sample/` |
| `report/latex/` | Báo cáo LaTeX theo template HCMUT (`main.tex`, `references.bib`) → `report/Bao_cao_BTL_CO4031.pdf` |
| `report/figures/`, `report/tools/` | Hình, sơ đồ, ảnh chụp và các script sinh chúng từ kho |
| `slides/` | Slide (`slides.html` → `Slide_BTL_CO4031.pdf`), kịch bản thuyết trình |

## Tái tạo báo cáo & slide

```bash
streamlit run src/app/app.py --server.port 8599 &   # để chụp màn hình
python report/tools/make_figures.py                 # biểu đồ + stats.json (số liệu từ kho)
python report/tools/diagrams.py                     # sơ đồ SVG
python report/tools/svg_to_pdf.py                   # sơ đồ SVG -> PDF vector cho LaTeX
python report/tools/screenshots.py                  # ảnh chụp dashboard
python report/tools/build_pdf.py                    # pdflatex + bibtex (báo cáo) và slide -> PDF
```

Báo cáo cần TeX (MiKTeX/TeX Live) với `pdflatex` + `bibtex`; có thể mở `report/latex/` trên Overleaf (upload kèm `report/figures/`).

## Dữ liệu

Brazilian E-Commerce Public Dataset by Olist (Kaggle), bản phát hành công khai tại GitHub `olist/work-at-olist-data` — 99.441 đơn hàng, 09/2016–10/2018.
