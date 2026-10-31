-- =====================================================================
-- 04_mart.sql  —  Lớp truy cập (semantic layer) cho OLAP, dashboard, DSS
--   * View phẳng hóa star schema (dễ dùng cho người phân tích / BI tool)
--   * Bảng tổng hợp (aggregate table) vật lý hóa cho truy vấn dashboard nhanh
-- Chạy lại sau mỗi batch (CREATE OR REPLACE).
-- =====================================================================

-- ---------- View: star schema bán hàng, đã join đủ chiều ----------
CREATE OR REPLACE VIEW mart.v_sales AS
SELECT
    f.order_id, f.order_item_id,
    d.full_date, d.year_num, d.year_quarter, d.year_month, d.month_num, d.day_name, d.day_of_week,
    d.is_weekend, d.is_holiday, d.is_black_friday,
    p.product_id, p.category_group, p.category_name, p.weight_class, p.size_class,
    s.seller_id, s.state AS seller_state, s.region AS seller_region,
    c.customer_unique_id, c.state AS customer_state, c.region AS customer_region,
    c.current_state AS customer_current_state,
    gc.city AS customer_city,
    pr.primary_payment_type, pr.installment_band, pr.has_voucher, pr.is_multi_seller,
    f.quantity, f.price, f.freight_value, f.item_total, f.distance_km
FROM dw.fact_sales f
JOIN dw.dim_date          d  ON d.date_key   = f.purchase_date_key
JOIN dw.dim_product       p  ON p.product_key  = f.product_key
JOIN dw.dim_seller        s  ON s.seller_key   = f.seller_key
JOIN dw.dim_customer      c  ON c.customer_key = f.customer_key
JOIN dw.dim_geography     gc ON gc.geo_key     = f.customer_geo_key
JOIN dw.dim_order_profile pr ON pr.profile_key = f.profile_key;

-- ---------- View: hiệu suất giao hàng cấp đơn (accumulating snapshot) ----------
CREATE OR REPLACE VIEW mart.v_fulfillment AS
SELECT
    o.*,
    d.year_num, d.year_month, d.year_quarter, d.month_num, d.day_of_week, d.is_holiday, d.is_black_friday,
    g.state AS customer_state, g.region AS customer_region, g.city AS customer_city,
    pr.primary_payment_type, pr.is_multi_seller,
    CASE WHEN NOT o.is_delivered OR o.delay_days IS NULL THEN 'N/A'
         WHEN o.delay_days <= -10 THEN '1. Sớm >=10 ngày'
         WHEN o.delay_days <= 0   THEN '2. Sớm/đúng hạn <10 ngày'
         WHEN o.delay_days <= 3   THEN '3. Trễ 1-3 ngày'
         WHEN o.delay_days <= 7   THEN '4. Trễ 4-7 ngày'
         ELSE '5. Trễ >7 ngày' END AS delay_bucket,
    r.review_score
FROM dw.fact_order_fulfillment o
JOIN dw.dim_date      d ON d.date_key = o.purchase_date_key
JOIN dw.dim_geography g ON g.geo_key  = o.customer_geo_key
JOIN dw.dim_order_profile pr ON pr.profile_key = o.profile_key
LEFT JOIN (  -- đơn có nhiều review -> lấy review mới nhất
    SELECT order_id, first(review_score ORDER BY review_date_key DESC, answer_date_key DESC, review_id) AS review_score
    FROM dw.fact_review GROUP BY order_id
) r ON r.order_id = o.order_id;

-- ---------- View: tuyến giao hàng (seller_state -> customer_state) — drill-across 2 fact qua order_id ----------
CREATE OR REPLACE VIEW mart.v_order_route AS
SELECT
    o.order_id, o.purchase_ts, o.year_month, o.is_delivered, o.is_late, o.lead_time_days, o.promised_days,
    o.delay_days, o.handling_days, o.transit_days, o.review_score, o.order_value, o.freight_value,
    o.customer_state, o.customer_region,
    s.state AS seller_state, s.region AS seller_region,
    s.state || ' → ' || o.customer_state AS route,
    x.distance_km, x.total_weight_g
FROM mart.v_fulfillment o
JOIN dw.bridge_order_seller b ON b.order_id = o.order_id
JOIN dw.dim_seller s ON s.seller_key = b.seller_key
JOIN (SELECT f.order_id, f.seller_key, avg(f.distance_km) AS distance_km, sum(p.weight_g) AS total_weight_g
      FROM dw.fact_sales f JOIN dw.dim_product p USING (product_key)
      GROUP BY f.order_id, f.seller_key) x ON x.order_id = b.order_id AND x.seller_key = b.seller_key
WHERE o.seller_count = 1;   -- chỉ đơn 1 người bán: tuyến xác định duy nhất

-- ---------- View: KPI người bán theo tháng (từ periodic snapshot) ----------
CREATE OR REPLACE VIEW mart.v_seller_monthly AS
SELECT
    d.full_date AS snapshot_date, d.year_month,
    s.seller_id, s.state AS seller_state, s.region AS seller_region, s.first_sale_date,
    f.*,
    CASE WHEN f.delivered_w  > 0 THEN 1 - f.late_w / f.delivered_w END            AS on_time_rate,
    CASE WHEN f.review_w     > 0 THEN f.review_score_sum_w / f.review_w END       AS avg_review,
    CASE WHEN f.handed_items > 0 THEN f.sla_breach_items * 1.0 / f.handed_items END AS sla_breach_rate,
    CASE WHEN f.items_cnt    > 0 THEN f.canceled_items * 1.0 / f.items_cnt END    AS cancel_rate
FROM dw.fact_seller_monthly_snapshot f
JOIN dw.dim_date   d ON d.date_key   = f.snapshot_date_key
JOIN dw.dim_seller s ON s.seller_key = f.seller_key;

-- ---------- Aggregate table (vật lý hóa) — tăng tốc dashboard ----------
CREATE OR REPLACE TABLE mart.agg_sales_month_category_state AS
SELECT
    d.year_num, d.year_quarter, d.year_month,
    p.category_group, p.category_name,
    g.region AS customer_region, g.state AS customer_state,
    count(DISTINCT f.order_id)  AS orders,
    sum(f.quantity)             AS items,
    sum(f.price)                AS revenue,
    sum(f.freight_value)        AS freight,
    count(DISTINCT f.customer_key) AS customers
FROM dw.fact_sales f
JOIN dw.dim_date      d ON d.date_key    = f.purchase_date_key
JOIN dw.dim_product   p ON p.product_key = f.product_key
JOIN dw.dim_geography g ON g.geo_key     = f.customer_geo_key
GROUP BY ALL;
