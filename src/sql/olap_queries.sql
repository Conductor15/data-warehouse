-- =====================================================================
-- olap_queries.sql  —  Bộ truy vấn OLAP minh họa trên kho Olist
-- Mỗi truy vấn bắt đầu bằng marker "-- @query Qxx | <phép OLAP> | <câu hỏi nghiệp vụ>"
-- (Streamlit "OLAP Explorer" và script báo cáo đọc trực tiếp file này)
-- =====================================================================

-- @query Q01 | ROLL-UP (ROLLUP) | Doanh thu theo phân cấp thời gian Năm > Quý > Tháng, kèm tổng phụ
SELECT
    coalesce(CAST(d.year_num AS VARCHAR), '== TỔNG ==')   AS nam,
    coalesce(d.year_quarter, '-- tổng năm')               AS quy,
    coalesce(d.year_month, '')                            AS thang,
    count(DISTINCT f.order_id)                            AS so_don,
    round(sum(f.price), 0)                                AS doanh_thu
FROM dw.fact_sales f
JOIN dw.dim_date d ON d.date_key = f.purchase_date_key
WHERE d.year_num IN (2017, 2018)
GROUP BY ROLLUP (d.year_num, d.year_quarter, d.year_month)
ORDER BY d.year_num NULLS LAST, d.year_quarter NULLS LAST, d.year_month NULLS LAST;

-- @query Q02 | DRILL-DOWN | Từ nhóm ngành "Electronics & Tech" khoan xuống từng danh mục con
SELECT p.category_group, p.category_name,
       count(DISTINCT f.order_id)          AS so_don,
       round(sum(f.price), 0)              AS doanh_thu,
       round(avg(f.price), 1)              AS gia_tb
FROM dw.fact_sales f
JOIN dw.dim_product p ON p.product_key = f.product_key
WHERE p.category_group = 'Electronics & Tech'
GROUP BY ALL
ORDER BY doanh_thu DESC;

-- @query Q03 | SLICE | Lát cắt năm 2018: doanh thu & giá trị đơn TB theo vùng khách hàng
SELECT g.region                            AS vung_khach_hang,
       count(DISTINCT f.order_id)          AS so_don,
       round(sum(f.price), 0)              AS doanh_thu,
       round(sum(f.price) / count(DISTINCT f.order_id), 1) AS aov,
       round(100.0 * sum(f.freight_value) / sum(f.price), 1) AS phi_vc_pct
FROM dw.fact_sales f
JOIN dw.dim_date d      ON d.date_key = f.purchase_date_key
JOIN dw.dim_geography g ON g.geo_key  = f.customer_geo_key
WHERE d.year_num = 2018
GROUP BY ALL
ORDER BY doanh_thu DESC;

-- @query Q04 | DICE | Khối con: H1-2018 x vùng Sudeste x {Health & Beauty, Fashion} x thẻ tín dụng
SELECT d.year_quarter, g.state, p.category_group,
       count(DISTINCT f.order_id) AS so_don,
       round(sum(f.price), 0)     AS doanh_thu
FROM dw.fact_sales f
JOIN dw.dim_date d          ON d.date_key    = f.purchase_date_key
JOIN dw.dim_geography g     ON g.geo_key     = f.customer_geo_key
JOIN dw.dim_product p       ON p.product_key = f.product_key
JOIN dw.dim_order_profile o ON o.profile_key = f.profile_key
WHERE d.year_quarter IN ('2018-Q1', '2018-Q2')
  AND g.region = 'Sudeste'
  AND p.category_group IN ('Health & Beauty', 'Fashion & Accessories')
  AND o.primary_payment_type = 'credit_card'
GROUP BY ALL
ORDER BY 1, 2, 3;

-- @query Q05 | PIVOT | Xoay trục: nhóm ngành (hàng) x năm (cột), giá trị = doanh thu
PIVOT (
    SELECT p.category_group, CAST(d.year_num AS VARCHAR) AS nam, f.price
    FROM dw.fact_sales f
    JOIN dw.dim_date d    ON d.date_key    = f.purchase_date_key
    JOIN dw.dim_product p ON p.product_key = f.product_key
)
ON nam USING round(sum(price), 0)
ORDER BY "2018" DESC NULLS LAST;

-- @query Q06 | CUBE | Mọi tổ hợp vùng khách hàng x phương thức thanh toán (GROUPING() phân biệt dòng tổng)
SELECT
    CASE WHEN GROUPING(g.region) = 1 THEN '(tất cả)' ELSE g.region END                 AS vung,
    CASE WHEN GROUPING(t.payment_type) = 1 THEN '(tất cả)' ELSE t.payment_type END     AS thanh_toan,
    GROUPING(g.region, t.payment_type)                                                 AS grouping_id,
    count(DISTINCT p.order_id)                                                         AS so_don,
    round(sum(p.payment_value), 0)                                                     AS gia_tri_tt,
    round(avg(p.installments), 2)                                                      AS ky_tra_gop_tb
FROM dw.fact_payment p
JOIN dw.fact_order_fulfillment o ON o.order_id = p.order_id
JOIN dw.dim_geography g          ON g.geo_key = o.customer_geo_key
JOIN dw.dim_payment_type t       ON t.payment_type_key = p.payment_type_key
GROUP BY CUBE (g.region, t.payment_type)
ORDER BY grouping_id, vung, gia_tri_tt DESC;

-- @query Q07 | GROUPING SETS + ROLE-PLAYING | Luồng hàng liên vùng: vùng người bán -> vùng khách (2 vai trò của dim_geography)
SELECT
    coalesce(gs.region, '(tất cả)') AS vung_nguoi_ban,
    coalesce(gc.region, '(tất cả)') AS vung_khach_hang,
    count(*)                        AS so_dong_hang,
    round(sum(f.price), 0)          AS doanh_thu,
    round(avg(f.distance_km), 0)    AS khoang_cach_tb_km,
    round(avg(f.freight_value), 1)  AS phi_vc_tb
FROM dw.fact_sales f
JOIN dw.dim_geography gs ON gs.geo_key = f.seller_geo_key     -- vai trò: nơi gửi
JOIN dw.dim_geography gc ON gc.geo_key = f.customer_geo_key   -- vai trò: nơi nhận
GROUP BY GROUPING SETS ((gs.region, gc.region), (gs.region), ())
ORDER BY GROUPING(gs.region), vung_nguoi_ban, GROUPING(gc.region), doanh_thu DESC;

-- @query Q08 | DRILL-ACROSS | Ghép 3 fact (sales, fulfillment, review) qua chiều chung -> doanh thu vs giao trễ vs hài lòng theo nhóm ngành
WITH s AS (
    SELECT p.category_group, sum(f.price) AS doanh_thu, count(DISTINCT f.order_id) AS so_don
    FROM dw.fact_sales f JOIN dw.dim_product p USING (product_key) GROUP BY 1
), o AS (
    SELECT p.category_group, avg(CAST(x.is_late AS INT)) AS ty_le_tre, avg(x.lead_time_days) AS lead_time
    FROM dw.fact_order_fulfillment x
    JOIN (SELECT DISTINCT order_id, product_key FROM dw.fact_sales) fs USING (order_id)
    JOIN dw.dim_product p USING (product_key)
    WHERE x.is_delivered GROUP BY 1
), r AS (
    SELECT p.category_group, avg(rv.review_score) AS diem_tb, avg(CAST(rv.is_negative AS INT)) AS ty_le_xau
    FROM dw.fact_review rv
    JOIN (SELECT DISTINCT order_id, product_key FROM dw.fact_sales) fs USING (order_id)
    JOIN dw.dim_product p USING (product_key) GROUP BY 1
)
SELECT s.category_group, s.so_don, round(s.doanh_thu, 0) AS doanh_thu,
       round(100 * o.ty_le_tre, 1) AS tre_pct, round(o.lead_time, 1) AS lead_time_ngay,
       round(r.diem_tb, 2) AS diem_review, round(100 * r.ty_le_xau, 1) AS review_xau_pct
FROM s JOIN o USING (category_group) JOIN r USING (category_group)
ORDER BY doanh_thu DESC;

-- @query Q09 | RANKING (window) | Top 3 danh mục theo doanh thu trong từng vùng khách hàng
SELECT g.region, p.category_name,
       round(sum(f.price), 0) AS doanh_thu,
       rank() OVER (PARTITION BY g.region ORDER BY sum(f.price) DESC) AS hang
FROM dw.fact_sales f
JOIN dw.dim_product p   ON p.product_key = f.product_key
JOIN dw.dim_geography g ON g.geo_key     = f.customer_geo_key
GROUP BY g.region, p.category_name
QUALIFY hang <= 3
ORDER BY g.region, hang;

-- @query Q10 | TIME-SERIES (window) | Doanh thu tháng, trung bình trượt 3 tháng, tăng trưởng MoM và YoY
WITH m AS (
    SELECT d.year_month, sum(f.price) AS doanh_thu
    FROM dw.fact_sales f JOIN dw.dim_date d ON d.date_key = f.purchase_date_key
    WHERE d.full_date BETWEEN DATE '2017-01-01' AND DATE '2018-08-31'
    GROUP BY 1
)
SELECT year_month, round(doanh_thu, 0) AS doanh_thu,
       round(avg(doanh_thu) OVER (ORDER BY year_month ROWS 2 PRECEDING), 0)            AS ma3,
       round(100 * (doanh_thu / lag(doanh_thu) OVER (ORDER BY year_month) - 1), 1)    AS mom_pct,
       round(100 * (doanh_thu / lag(doanh_thu, 12) OVER (ORDER BY year_month) - 1), 1) AS yoy_pct
FROM m ORDER BY year_month;

-- @query Q11 | SCD TYPE 6 (as-was vs as-is) | Khách đã chuyển bang: doanh thu ghi nhận theo bang lúc mua (Type 2) vs bang hiện tại (Type 1)
WITH moved AS (
    SELECT customer_unique_id FROM dw.dim_customer GROUP BY 1 HAVING count(DISTINCT state) > 1
), base AS (
    SELECT c.state, c.current_state, c.customer_unique_id, f.price
    FROM dw.fact_sales f
    JOIN dw.dim_customer c ON c.customer_key = f.customer_key
    WHERE c.customer_unique_id IN (SELECT customer_unique_id FROM moved)
), was AS (SELECT state AS bang, sum(price) AS dt, count(DISTINCT customer_unique_id) AS kh FROM base GROUP BY 1),
   iss AS (SELECT current_state AS bang, sum(price) AS dt, count(DISTINCT customer_unique_id) AS kh FROM base GROUP BY 1)
SELECT bang,
       coalesce(was.kh, 0)           AS so_khach_as_was,
       round(coalesce(was.dt, 0), 0) AS doanh_thu_as_was,
       coalesce(iss.kh, 0)           AS so_khach_as_is,
       round(coalesce(iss.dt, 0), 0) AS doanh_thu_as_is,
       round(coalesce(iss.dt, 0) - coalesce(was.dt, 0), 0) AS chenh_lech
FROM was FULL OUTER JOIN iss USING (bang)
ORDER BY abs(chenh_lech) DESC, doanh_thu_as_was DESC;

-- @query Q12 | PERIODIC SNAPSHOT (semi-additive) | Tồn đơn cuối tháng: SUM theo seller là đúng, SUM theo thời gian là SAI -> dùng AVG/LAST
WITH m AS (
    SELECT d.year_quarter, d.full_date, sum(s.open_orders_eom) AS ton_don, sum(s.gmv) AS gmv
    FROM dw.fact_seller_monthly_snapshot s
    JOIN dw.dim_date d ON d.date_key = s.snapshot_date_key
    WHERE d.year_num IN (2017, 2018) AND d.full_date <= DATE '2018-08-31'
    GROUP BY 1, 2
)
SELECT year_quarter,
       sum(ton_don)                    AS sai_cong_3_thang,
       round(avg(ton_don), 0)          AS dung_tb_cuoi_thang,
       arg_max(ton_don, full_date)     AS dung_ton_cuoi_quy,
       round(sum(gmv), 0)              AS gmv_quy_cong_duoc
FROM m GROUP BY 1 ORDER BY 1;

-- @query Q13 | ACCUMULATING SNAPSHOT (lag) | Phân rã thời gian chu trình đơn: duyệt -> người bán xử lý -> vận chuyển, theo quý
SELECT d.year_quarter,
       count(*)                          AS so_don_da_giao,
       round(avg(o.approval_hours), 1)   AS duyet_gio,
       round(avg(o.handling_days), 1)    AS xu_ly_ngay,
       round(avg(o.transit_days), 1)     AS van_chuyen_ngay,
       round(avg(o.lead_time_days), 1)   AS tong_ngay,
       round(avg(o.promised_days), 1)    AS cam_ket_ngay,
       round(100 * avg(CAST(o.is_late AS INT)), 1) AS tre_pct
FROM dw.fact_order_fulfillment o
JOIN dw.dim_date d ON d.date_key = o.purchase_date_key
WHERE o.is_delivered AND NOT o.dq_timeline_anomaly AND d.full_date BETWEEN DATE '2017-01-01' AND DATE '2018-08-31'
GROUP BY 1 ORDER BY 1;

-- @query Q14 | AGGREGATE NAVIGATION | Cùng câu hỏi trên bảng tổng hợp (mart.agg_*) thay vì fact chi tiết
SELECT year_quarter, customer_region, category_group,
       sum(orders) AS so_don, round(sum(revenue), 0) AS doanh_thu
FROM mart.agg_sales_month_category_state
WHERE year_num = 2018
GROUP BY ALL
ORDER BY doanh_thu DESC
LIMIT 10;
