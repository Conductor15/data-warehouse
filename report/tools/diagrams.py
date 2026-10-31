"""Sinh sơ đồ SVG cho báo cáo: kiến trúc, star schema, luồng ETL, kiến trúc DSS.

    python report/tools/diagrams.py
"""
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "figures"
FONT = "font-family='Segoe UI, Arial, sans-serif'"
NAVY, BLUE, GOLD, GREY, GREEN, RED = "#1b3a5c", "#2e86ab", "#c9a227", "#6b7785", "#3b8b5a", "#c73e1d"


class Svg:
    def __init__(self, w, h):
        self.w, self.h, self.bg, self.fg = w, h, [], []

    def line(self, x1, y1, x2, y2, label=None, dash=False, color=GREY, arrow=False, lx=None, ly=None):
        d = " stroke-dasharray='5,4'" if dash else ""
        m = " marker-end='url(#arr)'" if arrow else ""
        self.bg.append(f"<line x1='{x1}' y1='{y1}' x2='{x2}' y2='{y2}' stroke='{color}' stroke-width='1.6'{d}{m}/>")
        if label:
            lx = (x1 + x2) / 2 if lx is None else lx
            ly = (y1 + y2) / 2 if ly is None else ly
            self.fg.append(f"<rect x='{lx - len(label) * 2.75 - 4}' y='{ly - 9}' width='{len(label) * 5.5 + 8}' height='15' rx='3' fill='white' opacity='0.92'/>"
                           f"<text x='{lx}' y='{ly + 2}' {FONT} font-size='10' fill='{color}' text-anchor='middle' font-style='italic'>{escape(label)}</text>")

    def table(self, x, y, w, title, cols, kind="dim", note=None):
        head = {"dim": BLUE, "fact": NAVY, "bridge": GOLD, "other": GREY}[kind]
        h = 24 + 15 * len(cols) + 8
        self.fg.append(f"<rect x='{x}' y='{y}' width='{w}' height='{h}' rx='5' fill='white' stroke='{head}' stroke-width='1.6'/>"
                       f"<rect x='{x}' y='{y}' width='{w}' height='24' rx='5' fill='{head}'/>"
                       f"<rect x='{x}' y='{y + 16}' width='{w}' height='8' fill='{head}'/>"
                       f"<text x='{x + w / 2}' y='{y + 16}' {FONT} font-size='12.5' font-weight='700' fill='white' text-anchor='middle'>{escape(title)}</text>")
        for i, c in enumerate(cols):
            weight = "700" if c.startswith(("PK", "🔑")) else "400"
            color = RED if c.startswith("FK") else ("#111" if not c.startswith("·") else GREY)
            self.fg.append(f"<text x='{x + 8}' y='{y + 38 + 15 * i}' {FONT} font-size='10.5' font-weight='{weight}' fill='{color}'>{escape(c)}</text>")
        if note:
            self.fg.append(f"<text x='{x + w / 2}' y='{y + h + 13}' {FONT} font-size='9.5' fill='{GREY}' text-anchor='middle' font-style='italic'>{escape(note)}</text>")
        return (x, y, w, h)

    def box(self, x, y, w, h, title, lines=(), fill="#eef3f8", stroke=NAVY, tcolor=NAVY, fs=12):
        self.fg.append(f"<rect x='{x}' y='{y}' width='{w}' height='{h}' rx='7' fill='{fill}' stroke='{stroke}' stroke-width='1.5'/>"
                       f"<text x='{x + w / 2}' y='{y + 18}' {FONT} font-size='{fs}' font-weight='700' fill='{tcolor}' text-anchor='middle'>{escape(title)}</text>")
        for i, t in enumerate(lines):
            self.fg.append(f"<text x='{x + w / 2}' y='{y + 35 + 14 * i}' {FONT} font-size='10' fill='#333' text-anchor='middle'>{escape(t)}</text>")

    def text(self, x, y, s, size=11, color=NAVY, weight="700", anchor="middle"):
        self.fg.append(f"<text x='{x}' y='{y}' {FONT} font-size='{size}' font-weight='{weight}' fill='{color}' text-anchor='{anchor}'>{escape(s)}</text>")

    def save(self, name):
        defs = (f"<defs><marker id='arr' viewBox='0 0 10 10' refX='9' refY='5' markerWidth='7' markerHeight='7' orient='auto-start-reverse'>"
                f"<path d='M0,0 L10,5 L0,10 z' fill='{GREY}'/></marker></defs>")
        svg = (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {self.w} {self.h}' width='{self.w}' height='{self.h}'>"
               f"<rect width='100%' height='100%' fill='white'/>{defs}{''.join(self.bg)}{''.join(self.fg)}</svg>")
        (OUT / f"{name}.svg").write_text(svg, encoding="utf-8")


def c(t):  # tâm hộp
    x, y, w, h = t
    return x + w / 2, y + h / 2


def star_sales():
    s = Svg(940, 600)
    f = s.table(355, 170, 230, "fact_sales", [
        "PK order_id (degenerate)", "PK order_item_id (degenerate)", "FK purchase_date_key", "FK shipping_limit_date_key",
        "FK customer_key", "FK product_key", "FK seller_key", "FK customer_geo_key", "FK seller_geo_key", "FK profile_key",
        "quantity · price · freight_value", "item_total · distance_km", "batch_id"], "fact", "Transaction fact · grain = 1 dòng sản phẩm")
    d_date = s.table(20, 20, 210, "dim_date", ["PK date_key (YYYYMMDD)", "full_date, day_name, is_weekend", "year > quarter > month",
                                               "is_holiday, holiday_name", "is_black_friday, is_month_end"])
    d_cus = s.table(20, 230, 225, "dim_customer  (SCD 6)", ["PK customer_key", "customer_unique_id (NK)", "zip, city, state, region  [T2]",
                                                            "original_state  [T0]", "current_state/region  [T1]",
                                                            "version_no, effective_from/to", "is_current"])
    d_geo = s.table(20, 445, 225, "dim_geography (conformed)", ["PK geo_key", "zip_prefix (NK)", "region > state > city > zip",
                                                                 "latitude, longitude", "has_coordinates"])
    d_prod = s.table(705, 20, 215, "dim_product  (SCD 1)", ["PK product_key", "product_id (NK)", "category_group > category",
                                                            "weight_g, volume_cm3", "weight_class, size_class", "photos_qty"])
    d_sel = s.table(705, 235, 215, "dim_seller  (SCD 1 + T0)", ["PK seller_key", "seller_id (NK)", "zip, city, state, region",
                                                                "FK geo_key", "first_sale_date  [T0]"])
    d_prof = s.table(705, 425, 215, "dim_order_profile (junk)", ["PK profile_key", "primary_payment_type", "installment_band",
                                                                 "has_voucher, is_multi_payment", "is_multi_seller"])
    fx, fy = c(f)
    for t, lab in ((d_date, "purchase / shipping_limit"), (d_cus, None), (d_prod, None), (d_sel, None), (d_prof, None)):
        s.line(*c(t), fx, fy, lab)
    s.line(*c(d_geo), fx, fy + 40, "customer_geo / seller_geo")
    s.save("d_star_sales")


def star_fulfillment():
    s = Svg(940, 560)
    f = s.table(345, 130, 250, "fact_order_fulfillment", [
        "PK order_id (degenerate)", "FK purchase_date_key", "FK approved_date_key", "FK carrier_date_key", "FK delivered_date_key",
        "FK estimated_date_key", "FK customer_key · customer_geo_key", "FK profile_key", "order_status (cập nhật)",
        "item_count · order_value · freight", "approval_hours · handling_days", "transit_days · lead_time_days",
        "promised_days · delay_days", "is_delivered · is_late · is_canceled", "first/last_batch_id · update_count"],
        "fact", "Accumulating snapshot · grain = 1 đơn hàng")
    d_date = s.table(20, 40, 200, "dim_date", ["PK date_key", "· 5 vai trò (role-playing)", "· purchase, approved, carrier,", "  delivered, estimated"])
    d_cus = s.table(20, 230, 200, "dim_customer", ["PK customer_key", "· SCD 6"])
    d_geo = s.table(20, 360, 200, "dim_geography", ["PK geo_key", "· customer_geo"])
    d_prof = s.table(20, 470, 200, "dim_order_profile", ["PK profile_key", "· junk"])
    br = s.table(705, 40, 215, "bridge_order_seller", ["PK order_id, seller_key", "item_count, revenue", "allocation_weight (Σ=1)"], "bridge",
                 "nhiều-nhiều đơn ↔ người bán")
    d_sel = s.table(705, 175, 215, "dim_seller", ["PK seller_key"])
    rv = s.table(705, 265, 215, "fact_review", ["PK review_id, order_id", "FK review/answer_date_key", "review_score, is_negative",
                                                "has_comment, response_hours"], "fact", "Transaction fact · grain = 1 review")
    snap = s.table(705, 405, 215, "fact_seller_monthly_snapshot", ["PK snapshot_date_key, seller_key", "orders_cnt, gmv (additive)",
                                                                   "delivered_w, late_w, review_w", "open_orders_eom (semi-add.)"],
                   "fact", "Periodic snapshot · seller × tháng")
    fx, fy = c(f)
    s.line(*c(d_date), fx, fy - 60, "×5 vai trò")
    for t in (d_cus, d_geo, d_prof):
        s.line(*c(t), fx, fy)
    s.line(*c(br), fx, fy - 40, "order_id")
    s.line(*c(br), *c(d_sel))
    s.line(*c(rv), fx, fy + 10, "drill-across (order_id)", dash=True)
    s.line(*c(snap), *c(d_sel), dash=True)
    s.line(*c(snap), fx, fy + 80, "dựng từ facts", dash=True)
    s.save("d_star_fulfillment")


def architecture():
    s = Svg(980, 430)
    cols = [(15, "NGUỒN"), (200, "STAGING (stg)"), (400, "KHO DỮ LIỆU (dw)"), (625, "MART / SEMANTIC (mart)"), (815, "ỨNG DỤNG")]
    for x, t in cols:
        s.text(x + 80, 22, t, 11.5, GREY)
    s.box(15, 40, 160, 150, "Olist CSV (9 tệp)", ["orders, order_items", "payments, reviews", "customers, sellers", "products, geolocation",
                                                  "category translation", "≈ 1,55 triệu dòng"], fill="#f4f6f8", stroke=GREY)
    s.box(15, 210, 160, 92, "Master data nhóm soạn", ["27 bang + vùng Brazil", "39 ngày lễ / Black Friday", "73 danh mục → 10 nhóm"],
          fill="#f4f6f8", stroke=GREY)
    s.box(200, 40, 175, 100, "Landing  stg.raw_*", ["Nạp nguyên trạng", "mọi cột VARCHAR", "(không mất số 0 đầu zip)"], fill="#fff8e6", stroke=GOLD)
    s.box(200, 160, 175, 142, "Clean  stg.*", ["Ép kiểu, chuẩn hóa", "12 luật làm sạch R01–R12", "Gộp geolocation 1M → 19K",
                                               "Gắn cờ bất thường", "Ảnh chụp as-of cutoff"], fill="#fff8e6", stroke=GOLD)
    s.box(400, 40, 200, 262, "Star schema (Kimball)", ["7 dimension", "date · customer(SCD6) · geography", "product · seller · payment_type",
                                                        "order_profile (junk)", "", "5 fact + 1 bridge", "sales (transaction)", "order_fulfillment (accumulating)",
                                                        "payment · review (transaction)", "seller_monthly (periodic)", "bridge_order_seller"])
    s.box(625, 40, 170, 120, "Semantic layer", ["v_sales · v_fulfillment", "v_order_route", "v_seller_monthly", "agg_sales_month_category_state"],
          fill="#eaf5ee", stroke=GREEN, tcolor=GREEN)
    s.box(625, 180, 170, 122, "Kết quả mô hình DSS", ["dss_promise_backtest", "dss_late_risk_scores", "dss_seller_metrics", "(ghi ngược vào kho)"],
          fill="#eaf5ee", stroke=GREEN, tcolor=GREEN)
    s.box(815, 40, 150, 120, "Dashboard Streamlit", ["Tổng quan · Bán hàng", "Giao hàng", "OLAP Explorer", "ETL & DQ"], fill="#eef3f8")
    s.box(815, 180, 150, 122, "Mô-đun DSS", ["DSS-1 Cam kết giao", "DSS-2 Cảnh báo trễ", "DSS-3 Người bán", "what-if · goal seek"], fill="#eef3f8")
    s.box(200, 330, 595, 80, "etl schema — metadata vận hành", ["batch_log · load_stats · watermark · dq_result (26 kiểm tra/batch: CLEANSING · RI · RECONCILIATION · BUSINESS)",
                                                                  "Orchestrator: src/etl/pipeline.py — initial load + 10 batch incremental theo tháng, mỗi batch 1 transaction, fail-fast"],
          fill="#f4f6f8", stroke=GREY, tcolor=GREY)
    for (x1, y1, x2, y2) in ((175, 115, 200, 90), (175, 256, 200, 230), (287, 140, 287, 160), (375, 170, 400, 170),
                             (600, 100, 625, 100), (600, 240, 625, 240), (795, 100, 815, 100), (795, 240, 815, 240), (500, 302, 500, 330)):
        s.line(x1, y1, x2, y2, arrow=True)
    s.text(490, 425, "Toàn bộ chạy trên DuckDB (OLAP columnar, nhúng) — 1 tệp warehouse/olist_dw.duckdb", 10, GREY, "400")
    s.save("d_architecture")


def etl_flow():
    s = Svg(980, 250)
    steps = [("1. asof_source", ["ảnh chụp nguồn", "tại cutoff"]), ("2. dim_geography", ["upsert SCD1"]),
             ("3. dim_product", ["SCD1"]), ("4. dim_seller", ["SCD1 + Type 0"]), ("5. dim_customer", ["SCD2 → Type 6"]),
             ("6. order_attributes", ["PIT lookup", "junk profile"]), ("7. fact_sales", ["+ bridge", "+ fact_payment"]),
             ("8. fulfillment", ["INSERT mới", "UPDATE mốc"]), ("9. fact_review", ["theo ngày tạo"]), ("10. snapshot", ["tháng đã đóng"])]
    w = 88
    for i, (t, ls) in enumerate(steps):
        x = 12 + i * 96
        fill = "#fff8e6" if i == 0 else ("#eaf2fb" if i < 6 else "#e9eef5")
        s.box(x, 40, w, 72, t, ls, fill=fill, fs=10)
        if i:
            s.line(x - 8, 76, x, 76, arrow=True)
    s.text(490, 25, "Một batch = một transaction:  [watermark, cutoff)  →  10 bước trong 10_batch_load.sql", 11.5)
    s.box(12, 140, 300, 90, "04_mart.sql", ["Làm mới view & aggregate", "(semantic layer)"], fill="#eaf5ee", stroke=GREEN, tcolor=GREEN)
    s.box(340, 140, 300, 90, "05_dq_checks.sql", ["26 kiểm tra: CLEANSING · RI · RECONCILIATION · BUSINESS", "FAIL → ROLLBACK toàn batch"],
          fill="#fbeeee", stroke=RED, tcolor=RED)
    s.box(668, 140, 300, 90, "Ghi metadata", ["load_stats (insert/update theo bảng)", "watermark ← cutoff · batch_log SUCCESS", "COMMIT"],
          fill="#f4f6f8", stroke=GREY, tcolor=GREY)
    s.line(312, 185, 340, 185, arrow=True)
    s.line(640, 185, 668, 185, arrow=True)
    s.line(920, 112, 162, 140, arrow=True)
    s.save("d_etl_flow")


def dss_arch():
    s = Svg(980, 330)
    s.box(15, 30, 230, 270, "Hệ con dữ liệu (Data)", ["Kho dữ liệu Olist (dw.*)", "fact_order_fulfillment", "fact_seller_monthly_snapshot",
                                                       "mart.v_order_route", "", "Đặc trưng point-in-time:", "lịch sử seller tháng trước,",
                                                       "tắc nghẽn 14 ngày của bang", "", "Kết quả mô hình ghi ngược", "mart.dss_*"])
    s.box(275, 30, 420, 270, "Hệ con mô hình (Model base)", [], fill="#eaf2fb")
    s.box(290, 60, 390, 70, "DSS-1  Cam kết ngày giao — tối ưu / mô phỏng",
          ["Hồi quy quantile GBM + bảng quantile tuyến, walk-forward", "What-if (α, ε) · Goal seeking (α min đạt mục tiêu) · Sensitivity"], fill="white", fs=11)
    s.box(290, 140, 390, 70, "DSS-2  Cảnh báo trễ — dự báo + quyết định theo chi phí",
          ["Gradient Boosting vs Logistic; ngưỡng t* tối đa lợi ích ròng", "What-if chi phí C_late, C_action, hiệu quả can thiệp"], fill="white", fs=11)
    s.box(290, 220, 390, 70, "DSS-3  Đánh giá người bán — MCDM",
          ["AHP (eigenvector, CR) → TOPSIS → luật hành động", "Sensitivity Monte Carlo Dirichlet trên trọng số"], fill="white", fs=11)
    s.box(725, 30, 240, 170, "Hệ con giao diện (UI)", ["Streamlit: thanh trượt what-if,", "đường biên Pareto, đường cong", "quyết định, watch-list,",
                                                        "ma trận so sánh cặp AHP,", "bản đồ danh mục người bán"], fill="#eef3f8")
    s.box(725, 215, 240, 85, "Tri thức (Knowledge)", ["review TB theo mức trễ (học từ kho)", "luật hành động · thang Saaty", "giả định co giãn nhu cầu"],
          fill="#fff8e6", stroke=GOLD, tcolor="#8a6d00")
    s.line(245, 165, 275, 165, arrow=True)
    s.line(695, 115, 725, 115, arrow=True)
    s.line(725, 250, 695, 250, arrow=True)
    s.text(490, 322, "Người ra quyết định: Trưởng vận hành (DSS-1, DSS-2) · Quản lý quan hệ người bán (DSS-3)", 10.5, GREY, "400")
    s.save("d_dss_arch")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    star_sales(); star_fulfillment(); architecture(); etl_flow(); dss_arch()
    print("Đã tạo sơ đồ SVG")
