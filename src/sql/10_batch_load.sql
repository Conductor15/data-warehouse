-- =====================================================================
-- 10_batch_load.sql  —  TRANSFORM + LOAD cho 1 batch (initial / incremental)
-- ---------------------------------------------------------------------
-- Biến (SET VARIABLE bởi pipeline.py):
--   batch_id     : số hiệu batch
--   prev_cutoff  : watermark của batch trước (exclusive lower bound)
--   cutoff       : mốc cắt của batch này     (exclusive upper bound)
-- Mô phỏng hệ nguồn "as-of cutoff": mốc thời gian nào > cutoff coi như CHƯA xảy ra.
-- Nhờ vậy accumulating snapshot & SCD2 được cập nhật đúng như vận hành thật.
-- Mỗi khối "-- @step" được pipeline chạy, đo thời gian và ghi log riêng.
-- =====================================================================

-- @step asof_source
-- Ảnh chụp hệ nguồn tại thời điểm cutoff
CREATE OR REPLACE TEMP TABLE b_orders AS
WITH c AS (SELECT getvariable('cutoff') AS cutoff)
SELECT
    o.order_id, o.customer_id, o.purchase_ts,
    CASE WHEN o.approved_ts  < c.cutoff THEN o.approved_ts  END AS approved_ts,
    CASE WHEN o.carrier_ts   < c.cutoff THEN o.carrier_ts   END AS carrier_ts,
    CASE WHEN o.delivered_ts < c.cutoff THEN o.delivered_ts END AS delivered_ts,
    o.estimated_ts,
    CASE
        WHEN o.order_status IN ('canceled', 'unavailable')           THEN o.order_status
        WHEN o.order_status = 'delivered' AND o.delivered_ts IS NULL THEN 'delivered'   -- R02: giữ trạng thái nguồn
        WHEN o.delivered_ts < c.cutoff                               THEN 'delivered'
        WHEN o.carrier_ts   < c.cutoff                               THEN 'shipped'
        WHEN o.order_status IN ('delivered', 'shipped')              THEN
             CASE WHEN o.approved_ts < c.cutoff THEN 'processing' ELSE 'created' END
        ELSE o.order_status
    END AS order_status,
    o.dq_timeline_anomaly,
    o.purchase_ts >= getvariable('prev_cutoff') AS is_new
FROM stg.orders o, c
WHERE o.purchase_ts < c.cutoff;

CREATE OR REPLACE TEMP TABLE b_new_items AS
SELECT i.* FROM stg.order_items i
JOIN b_orders o USING (order_id)
WHERE o.is_new;

-- @step dim_geography
-- Nguồn zip = geolocation ∪ customers ∪ sellers (zip không có tọa độ -> has_coordinates = false — R12)
CREATE OR REPLACE TEMP TABLE src_geo AS
WITH z AS (
    SELECT zip_prefix, city, state FROM stg.customers
    UNION ALL
    SELECT zip_prefix, city, state FROM stg.sellers
), city_n AS (
    SELECT zip_prefix, city, count(*) AS n FROM z WHERE city IS NOT NULL GROUP BY ALL
), state_n AS (
    SELECT zip_prefix, state, count(*) AS n FROM z WHERE state IS NOT NULL GROUP BY ALL
), zc AS (   -- giá trị phổ biến nhất; hòa thì theo thứ tự chữ cái để kết quả tái lập được
    SELECT zip_prefix,
           (SELECT first(x.city ORDER BY x.n DESC, x.city) FROM city_n x WHERE x.zip_prefix = z0.zip_prefix)    AS city,
           (SELECT first(x.state ORDER BY x.n DESC, x.state) FROM state_n x WHERE x.zip_prefix = z0.zip_prefix) AS state
    FROM (SELECT DISTINCT zip_prefix FROM z) z0
), u AS (
    SELECT coalesce(g.zip_prefix, zc.zip_prefix) AS zip_prefix,
           coalesce(zc.city, g.city)             AS city,
           coalesce(zc.state, g.state)           AS state,
           g.lat, g.lng
    FROM stg.geolocation g
    FULL OUTER JOIN zc ON zc.zip_prefix = g.zip_prefix
)
SELECT u.zip_prefix, u.city, u.state,
       coalesce(s.state_name, 'Unknown') AS state_name,
       coalesce(s.region, 'Unknown')     AS region,
       u.lat, u.lng, u.lat IS NOT NULL   AS has_coordinates
FROM u LEFT JOIN stg.ref_states s ON s.state = u.state;

UPDATE dw.dim_geography d
SET city = s.city, state = s.state, state_name = s.state_name, region = s.region,
    latitude = s.lat, longitude = s.lng, has_coordinates = s.has_coordinates
FROM src_geo s
WHERE d.zip_prefix = s.zip_prefix
  AND (d.city IS DISTINCT FROM s.city OR d.state IS DISTINCT FROM s.state
       OR d.latitude IS DISTINCT FROM s.lat OR d.longitude IS DISTINCT FROM s.lng);

INSERT INTO dw.dim_geography
SELECT nextval('dw.seq_geo_key'), s.zip_prefix, s.city, s.state, s.state_name, s.region,
       s.lat, s.lng, s.has_coordinates
FROM src_geo s
WHERE NOT EXISTS (SELECT 1 FROM dw.dim_geography d WHERE d.zip_prefix = s.zip_prefix)
ORDER BY s.zip_prefix;

-- @step dim_product
-- SCD Type 1: thêm sản phẩm mới xuất hiện trong batch, ghi đè thuộc tính thay đổi
CREATE OR REPLACE TEMP TABLE src_product AS
SELECT p.*,
       p.length_cm * p.height_cm * p.width_cm AS volume_cm3,
       CASE WHEN p.weight_g IS NULL THEN 'Unknown'
            WHEN p.weight_g <  500  THEN 'Light (<0.5kg)'
            WHEN p.weight_g < 2000  THEN 'Medium (0.5-2kg)'
            WHEN p.weight_g < 10000 THEN 'Heavy (2-10kg)'
            ELSE 'Bulky (>=10kg)' END AS weight_class,
       CASE WHEN p.length_cm IS NULL THEN 'Unknown'
            WHEN p.length_cm * p.height_cm * p.width_cm <  2000 THEN 'S'
            WHEN p.length_cm * p.height_cm * p.width_cm < 10000 THEN 'M'
            WHEN p.length_cm * p.height_cm * p.width_cm < 50000 THEN 'L'
            ELSE 'XL' END AS size_class
FROM stg.products p;

UPDATE dw.dim_product d
SET category_name_pt = s.category_name_pt, category_name = s.category_name, category_group = s.category_group,
    name_length = s.name_length, description_length = s.description_length, photos_qty = s.photos_qty,
    weight_g = s.weight_g, length_cm = s.length_cm, height_cm = s.height_cm, width_cm = s.width_cm,
    volume_cm3 = s.volume_cm3, weight_class = s.weight_class, size_class = s.size_class,
    batch_id = getvariable('batch_id')
FROM src_product s
WHERE d.product_id = s.product_id
  AND (d.category_name IS DISTINCT FROM s.category_name OR d.weight_g IS DISTINCT FROM s.weight_g
       OR d.photos_qty IS DISTINCT FROM s.photos_qty OR d.volume_cm3 IS DISTINCT FROM s.volume_cm3);

INSERT INTO dw.dim_product
SELECT nextval('dw.seq_product_key'), s.product_id, s.category_name_pt, s.category_name, s.category_group,
       s.name_length, s.description_length, s.photos_qty, s.weight_g, s.length_cm, s.height_cm, s.width_cm,
       s.volume_cm3, s.weight_class, s.size_class, getvariable('batch_id')
FROM src_product s
WHERE s.product_id IN (SELECT product_id FROM b_new_items)
  AND NOT EXISTS (SELECT 1 FROM dw.dim_product d WHERE d.product_id = s.product_id)
ORDER BY s.product_id;

-- @step dim_seller
-- SCD Type 1 cho địa chỉ, Type 0 cho first_sale_date (cohort onboard, không bao giờ ghi đè)
UPDATE dw.dim_seller d
SET zip_prefix = s.zip_prefix, city = s.city, state = s.state,
    region = coalesce(r.region, 'Unknown'), geo_key = coalesce(g.geo_key, -1),
    batch_id = getvariable('batch_id')
FROM stg.sellers s
LEFT JOIN stg.ref_states r   ON r.state = s.state
LEFT JOIN dw.dim_geography g ON g.zip_prefix = s.zip_prefix
WHERE d.seller_id = s.seller_id
  AND (d.zip_prefix IS DISTINCT FROM s.zip_prefix OR d.city IS DISTINCT FROM s.city);

INSERT INTO dw.dim_seller
SELECT nextval('dw.seq_seller_key'), s.seller_id, s.zip_prefix, s.city, s.state,
       coalesce(r.region, 'Unknown'), coalesce(g.geo_key, -1), f.first_sale_date, getvariable('batch_id')
FROM stg.sellers s
JOIN (SELECT i.seller_id, CAST(min(o.purchase_ts) AS DATE) AS first_sale_date
      FROM b_new_items i JOIN b_orders o USING (order_id) GROUP BY i.seller_id) f USING (seller_id)
LEFT JOIN stg.ref_states r   ON r.state = s.state
LEFT JOIN dw.dim_geography g ON g.zip_prefix = s.zip_prefix
WHERE NOT EXISTS (SELECT 1 FROM dw.dim_seller d WHERE d.seller_id = s.seller_id)
ORDER BY s.seller_id;

-- @step dim_customer_scd2
-- Mỗi đơn mang 1 địa chỉ giao hàng của khách (customer_unique_id). Khi địa chỉ khác
-- phiên bản hiện hành -> đóng phiên bản cũ (effective_to) và mở phiên bản mới.
CREATE OR REPLACE TEMP TABLE cust_events AS
SELECT c.customer_unique_id AS uid, o.purchase_ts AS ts, c.zip_prefix, c.city, c.state
FROM b_orders o JOIN stg.customers c USING (customer_id)
WHERE o.is_new;

CREATE OR REPLACE TEMP TABLE cust_changes AS
WITH seq AS (
    SELECT uid, ts, zip_prefix, city, state, 0 AS is_seed FROM cust_events
    UNION ALL
    SELECT customer_unique_id, effective_from, zip_prefix, city, state, 1
    FROM dw.dim_customer
    WHERE is_current AND customer_key <> -1
      AND customer_unique_id IN (SELECT uid FROM cust_events)
), ordered AS (
    SELECT *,
           lag(zip_prefix) OVER w AS prev_zip,
           lag(city)       OVER w AS prev_city,
           lag(state)      OVER w AS prev_state
    FROM seq
    WINDOW w AS (PARTITION BY uid ORDER BY ts, is_seed DESC, zip_prefix, city, state)
)
SELECT uid, ts, zip_prefix, city, state
FROM ordered
WHERE is_seed = 0
  AND (prev_zip IS NULL
       OR zip_prefix IS DISTINCT FROM prev_zip
       OR city       IS DISTINCT FROM prev_city
       OR state      IS DISTINCT FROM prev_state);

CREATE OR REPLACE TEMP TABLE cust_prev AS
SELECT customer_unique_id AS uid, max(version_no) AS max_version, any_value(original_state) AS original_state
FROM dw.dim_customer
WHERE customer_unique_id IN (SELECT uid FROM cust_changes)
GROUP BY customer_unique_id;

-- (a) Đóng phiên bản hiện hành của khách có thay đổi
UPDATE dw.dim_customer d
SET effective_to = f.first_ts, is_current = false
FROM (SELECT uid, min(ts) AS first_ts FROM cust_changes GROUP BY uid) f
WHERE d.customer_unique_id = f.uid AND d.is_current AND d.customer_key <> -1;

-- (b) Thêm các phiên bản mới
INSERT INTO dw.dim_customer
SELECT nextval('dw.seq_customer_key'),
       x.uid, x.zip_prefix, x.city, x.state, coalesce(r.region, 'Unknown'), coalesce(g.geo_key, -1),
       coalesce(p.original_state, first_value(x.state) OVER w)          AS original_state,
       NULL, NULL,                                                       -- current_* gán ở bước (c)
       coalesce(p.max_version, 0) + row_number() OVER w                  AS version_no,
       CASE WHEN p.uid IS NULL AND row_number() OVER w = 1
            THEN TIMESTAMP '1900-01-01' ELSE x.ts END                    AS effective_from,
       coalesce(lead(x.ts) OVER w, TIMESTAMP '9999-12-31')               AS effective_to,
       lead(x.ts) OVER w IS NULL                                         AS is_current,
       getvariable('batch_id')
FROM cust_changes x
LEFT JOIN cust_prev p          ON p.uid = x.uid
LEFT JOIN stg.ref_states r     ON r.state = x.state
LEFT JOIN dw.dim_geography g   ON g.zip_prefix = x.zip_prefix
WINDOW w AS (PARTITION BY x.uid ORDER BY x.ts, x.zip_prefix, x.city, x.state);

-- (c) Type 1 trên mọi phiên bản: current_state/current_region = giá trị của phiên bản hiện hành
CREATE OR REPLACE TEMP TABLE cust_current AS
SELECT customer_unique_id, state, region FROM dw.dim_customer
WHERE is_current AND customer_unique_id IN (SELECT uid FROM cust_changes);

UPDATE dw.dim_customer d
SET current_state = c.state, current_region = c.region
FROM cust_current c
WHERE d.customer_unique_id = c.customer_unique_id
  AND (d.current_state IS DISTINCT FROM c.state OR d.current_region IS DISTINCT FROM c.region);

-- @step order_attributes
-- Thuộc tính cấp đơn: tra cứu khóa point-in-time của khách (SCD2) + hồ sơ thanh toán (junk)
CREATE OR REPLACE TEMP TABLE b_order_attr AS
WITH items AS (
    SELECT order_id, count(*) AS item_count, count(DISTINCT seller_id) AS seller_count,
           sum(price) AS order_value, sum(freight_value) AS freight_value
    FROM stg.order_items GROUP BY order_id
), pay AS (
    SELECT order_id,
           first(payment_type ORDER BY payment_value DESC, payment_sequential) AS primary_payment_type,
           max(installments)                             AS max_installments,
           bool_or(payment_type = 'voucher')             AS has_voucher,
           count(*) > 1                                  AS is_multi_payment,
           sum(payment_value)                            AS payment_value
    FROM stg.payments GROUP BY order_id
)
SELECT
    o.*,
    coalesce(it.item_count, 0)        AS item_count,
    coalesce(it.seller_count, 0)      AS seller_count,
    coalesce(it.order_value, 0)       AS order_value,
    coalesce(it.freight_value, 0)     AS freight_value,
    pay.payment_value,
    coalesce(dc.customer_key, -1)     AS customer_key,
    coalesce(dc.geo_key, -1)          AS customer_geo_key,
    coalesce(pr.profile_key, -1)      AS profile_key
FROM b_orders o
LEFT JOIN items it ON it.order_id = o.order_id
LEFT JOIN pay      ON pay.order_id = o.order_id
LEFT JOIN stg.customers c ON c.customer_id = o.customer_id
LEFT JOIN dw.dim_customer dc
       ON dc.customer_unique_id = c.customer_unique_id
      AND o.purchase_ts >= dc.effective_from AND o.purchase_ts < dc.effective_to
LEFT JOIN dw.dim_order_profile pr
       ON pr.primary_payment_type = pay.primary_payment_type
      AND pr.installment_band = CASE WHEN pay.max_installments <= 1 THEN '1x'
                                     WHEN pay.max_installments <= 3 THEN '2-3x'
                                     WHEN pay.max_installments <= 6 THEN '4-6x'
                                     ELSE '7x+' END
      AND pr.has_voucher      = pay.has_voucher
      AND pr.is_multi_payment = pay.is_multi_payment
      AND pr.is_multi_seller  = (coalesce(it.seller_count, 0) > 1);

-- @step fact_sales
CREATE OR REPLACE MACRO dw.haversine_km(lat1, lng1, lat2, lng2) AS
    2 * 6371 * asin(sqrt(pow(sin(radians(lat2 - lat1) / 2), 2)
                       + cos(radians(lat1)) * cos(radians(lat2)) * pow(sin(radians(lng2 - lng1) / 2), 2)));

INSERT INTO dw.fact_sales
SELECT
    i.order_id, i.order_item_id,
    CAST(strftime(a.purchase_ts, '%Y%m%d') AS INTEGER),
    coalesce(CAST(strftime(i.shipping_limit_ts, '%Y%m%d') AS INTEGER), -1),
    a.customer_key,
    coalesce(p.product_key, -1),
    coalesce(s.seller_key, -1),
    a.customer_geo_key,
    coalesce(s.geo_key, -1),
    a.profile_key,
    1, i.price, i.freight_value, i.price + i.freight_value,
    dw.haversine_km(gc.latitude, gc.longitude, gs.latitude, gs.longitude),
    a.purchase_ts, i.shipping_limit_ts,
    getvariable('batch_id')
FROM b_new_items i
JOIN b_order_attr a          ON a.order_id = i.order_id
LEFT JOIN dw.dim_product p   ON p.product_id = i.product_id
LEFT JOIN dw.dim_seller s    ON s.seller_id = i.seller_id
LEFT JOIN dw.dim_geography gc ON gc.geo_key = a.customer_geo_key
LEFT JOIN dw.dim_geography gs ON gs.geo_key = s.geo_key
WHERE NOT EXISTS (SELECT 1 FROM dw.fact_sales f
                  WHERE f.order_id = i.order_id AND f.order_item_id = i.order_item_id);

-- @step bridge_order_seller
INSERT INTO dw.bridge_order_seller
SELECT order_id, seller_key, item_count, revenue,
       CASE WHEN sum(revenue) OVER (PARTITION BY order_id) > 0
            THEN revenue / sum(revenue) OVER (PARTITION BY order_id)
            ELSE 1.0 / count(*) OVER (PARTITION BY order_id) END
FROM (
    SELECT order_id, seller_key, count(*) AS item_count, sum(item_total) AS revenue
    FROM dw.fact_sales WHERE batch_id = getvariable('batch_id')
    GROUP BY order_id, seller_key
) x
WHERE NOT EXISTS (SELECT 1 FROM dw.bridge_order_seller b WHERE b.order_id = x.order_id);

-- @step fact_payment
INSERT INTO dw.fact_payment
SELECT p.order_id, p.payment_sequential,
       CAST(strftime(a.purchase_ts, '%Y%m%d') AS INTEGER),
       a.customer_key,
       coalesce(t.payment_type_key, -1),
       a.profile_key,
       p.installments, p.payment_value,
       getvariable('batch_id')
FROM stg.payments p
JOIN b_order_attr a ON a.order_id = p.order_id AND a.is_new
LEFT JOIN dw.dim_payment_type t ON t.payment_type = p.payment_type
WHERE NOT EXISTS (SELECT 1 FROM dw.fact_payment f
                  WHERE f.order_id = p.order_id AND f.payment_sequential = p.payment_sequential);

-- @step fact_order_fulfillment
-- Accumulating snapshot: INSERT đơn mới, UPDATE đơn cũ khi có mốc mới (approved/carrier/delivered)
CREATE OR REPLACE MACRO dw.dkey(ts) AS coalesce(CAST(strftime(ts, '%Y%m%d') AS INTEGER), -1);

CREATE OR REPLACE TEMP TABLE b_fulfill AS
SELECT
    order_id, customer_key, customer_geo_key, profile_key,
    dw.dkey(purchase_ts) AS purchase_date_key, dw.dkey(approved_ts) AS approved_date_key,
    dw.dkey(carrier_ts)  AS carrier_date_key,  dw.dkey(delivered_ts) AS delivered_date_key,
    dw.dkey(estimated_ts) AS estimated_date_key,
    order_status, purchase_ts, approved_ts, carrier_ts, delivered_ts, estimated_ts,
    item_count, seller_count, order_value, freight_value, payment_value,
    date_diff('second', purchase_ts, approved_ts) / 3600.0                 AS approval_hours,
    date_diff('second', approved_ts, carrier_ts) / 86400.0                 AS handling_days,
    date_diff('second', carrier_ts, delivered_ts) / 86400.0                AS transit_days,
    date_diff('second', purchase_ts, delivered_ts) / 86400.0               AS lead_time_days,
    date_diff('second', purchase_ts, estimated_ts) / 86400.0               AS promised_days,
    date_diff('second', estimated_ts, delivered_ts) / 86400.0              AS delay_days,
    order_status = 'delivered'                                             AS is_delivered,
    CAST(delivered_ts AS DATE) > CAST(estimated_ts AS DATE)                AS is_late,
    order_status IN ('canceled', 'unavailable')                            AS is_canceled,
    dq_timeline_anomaly
FROM b_order_attr;

CREATE OR REPLACE TEMP TABLE b_fulfill_changed AS
SELECT b.* FROM b_fulfill b
JOIN dw.fact_order_fulfillment f USING (order_id)
WHERE f.order_status IS DISTINCT FROM b.order_status
   OR f.approved_ts  IS DISTINCT FROM b.approved_ts
   OR f.carrier_ts   IS DISTINCT FROM b.carrier_ts
   OR f.delivered_ts IS DISTINCT FROM b.delivered_ts;

UPDATE dw.fact_order_fulfillment f
SET order_status = b.order_status,
    approved_date_key = b.approved_date_key, carrier_date_key = b.carrier_date_key,
    delivered_date_key = b.delivered_date_key,
    approved_ts = b.approved_ts, carrier_ts = b.carrier_ts, delivered_ts = b.delivered_ts,
    approval_hours = b.approval_hours, handling_days = b.handling_days, transit_days = b.transit_days,
    lead_time_days = b.lead_time_days, delay_days = b.delay_days,
    is_delivered = b.is_delivered, is_late = b.is_late, is_canceled = b.is_canceled,
    last_batch_id = getvariable('batch_id'), update_count = f.update_count + 1
FROM b_fulfill_changed b
WHERE f.order_id = b.order_id;

INSERT INTO dw.fact_order_fulfillment
SELECT b.*, getvariable('batch_id'), getvariable('batch_id'), 0
FROM b_fulfill b
WHERE NOT EXISTS (SELECT 1 FROM dw.fact_order_fulfillment f WHERE f.order_id = b.order_id);

-- @step fact_review
-- Review đến theo ngày tạo review (thường SAU khi giao hàng) -> tải theo cửa sổ created_ts
INSERT INTO dw.fact_review
SELECT r.review_id, r.order_id,
       dw.dkey(r.created_ts), dw.dkey(r.answered_ts),
       coalesce(f.purchase_date_key, -1), coalesce(f.customer_key, -1),
       r.review_score,
       r.review_score <= 2, r.review_score >= 4,
       r.comment_title IS NOT NULL, r.comment_message IS NOT NULL,
       coalesce(length(r.comment_message), 0),
       date_diff('second', r.created_ts, r.answered_ts) / 3600.0,
       getvariable('batch_id')
FROM stg.reviews r
LEFT JOIN dw.fact_order_fulfillment f ON f.order_id = r.order_id
WHERE r.created_ts < getvariable('cutoff')
  AND NOT EXISTS (SELECT 1 FROM dw.fact_review x WHERE x.review_id = r.review_id AND x.order_id = r.order_id);

-- @step fact_seller_monthly_snapshot
-- Periodic snapshot (dense): mọi seller đã onboard x mọi tháng đã đóng trong cửa sổ batch.
-- Được xây TỪ CÁC FACT KHÁC trong DW (không đọc lại staging).
CREATE OR REPLACE TEMP TABLE b_months AS
SELECT CAST(ms AS DATE) AS ms, CAST(ms + INTERVAL 1 MONTH AS DATE) AS me,
       CAST(strftime(last_day(ms), '%Y%m%d') AS INTEGER) AS snapshot_date_key
FROM generate_series(TIMESTAMP '2016-09-01', TIMESTAMP '2018-12-01', INTERVAL 1 MONTH) t(ms)
WHERE ms + INTERVAL 1 MONTH >  greatest(getvariable('prev_cutoff'), TIMESTAMP '2016-09-01')
  AND ms + INTERVAL 1 MONTH <= getvariable('cutoff');

INSERT INTO dw.fact_seller_monthly_snapshot
WITH grid AS (
    SELECT m.*, s.seller_key
    FROM b_months m JOIN dw.dim_seller s ON s.first_sale_date < m.me AND s.seller_key <> -1
), sales AS (
    SELECT m.ms, f.seller_key,
           count(DISTINCT f.order_id) AS orders_cnt, count(*) AS items_cnt,
           sum(f.price) AS gmv, sum(f.freight_value) AS freight_value,
           count(DISTINCT f.customer_key) AS customers_cnt,
           count(*) FILTER (WHERE o.is_canceled) AS canceled_items
    FROM dw.fact_sales f
    JOIN b_months m ON f.purchase_ts >= m.ms AND f.purchase_ts < m.me
    JOIN dw.fact_order_fulfillment o USING (order_id)
    GROUP BY m.ms, f.seller_key
), deliv AS (
    SELECT m.ms, b.seller_key,
           sum(b.allocation_weight) AS delivered_w,
           sum(b.allocation_weight) FILTER (WHERE o.is_late) AS late_w
    FROM dw.fact_order_fulfillment o
    JOIN b_months m ON o.delivered_ts >= m.ms AND o.delivered_ts < m.me
    JOIN dw.bridge_order_seller b USING (order_id)
    GROUP BY m.ms, b.seller_key
), rev AS (
    SELECT m.ms, b.seller_key,
           sum(b.allocation_weight)                         AS review_w,
           sum(b.allocation_weight * r.review_score)        AS review_score_sum_w,
           sum(b.allocation_weight) FILTER (WHERE r.is_negative) AS negative_review_w
    FROM dw.fact_review r
    JOIN dw.dim_date d ON d.date_key = r.review_date_key
    JOIN b_months m ON d.full_date >= m.ms AND d.full_date < m.me
    JOIN dw.bridge_order_seller b USING (order_id)
    GROUP BY m.ms, b.seller_key
), sla AS (
    SELECT m.ms, f.seller_key,
           count(*) AS handed_items,
           count(*) FILTER (WHERE o.carrier_ts > f.shipping_limit_ts) AS sla_breach_items
    FROM dw.fact_sales f
    JOIN dw.fact_order_fulfillment o USING (order_id)
    JOIN b_months m ON o.carrier_ts >= m.ms AND o.carrier_ts < m.me
    GROUP BY m.ms, f.seller_key
), backlog AS (
    SELECT m.ms, b.seller_key, count(*) AS open_orders_eom
    FROM dw.fact_order_fulfillment o
    JOIN dw.bridge_order_seller b USING (order_id)
    JOIN b_months m ON o.purchase_ts < m.me
    WHERE NOT o.is_canceled
      AND NOT coalesce(o.delivered_ts < m.me, false)
      AND NOT (o.order_status = 'delivered' AND o.delivered_ts IS NULL)
    GROUP BY m.ms, b.seller_key
), cum AS (
    SELECT m.ms, f.seller_key, sum(f.price) AS cum_gmv
    FROM dw.fact_sales f JOIN b_months m ON f.purchase_ts < m.me
    GROUP BY m.ms, f.seller_key
)
SELECT g.snapshot_date_key, g.seller_key,
       coalesce(s.orders_cnt, 0), coalesce(s.items_cnt, 0), coalesce(s.gmv, 0), coalesce(s.freight_value, 0),
       coalesce(s.customers_cnt, 0),
       coalesce(d.delivered_w, 0), coalesce(d.late_w, 0),
       coalesce(r.review_w, 0), coalesce(r.review_score_sum_w, 0), coalesce(r.negative_review_w, 0),
       coalesce(l.handed_items, 0), coalesce(l.sla_breach_items, 0),
       coalesce(s.canceled_items, 0), coalesce(bk.open_orders_eom, 0), coalesce(c.cum_gmv, 0),
       getvariable('batch_id')
FROM grid g
LEFT JOIN sales   s  ON s.ms  = g.ms AND s.seller_key  = g.seller_key
LEFT JOIN deliv   d  ON d.ms  = g.ms AND d.seller_key  = g.seller_key
LEFT JOIN rev     r  ON r.ms  = g.ms AND r.seller_key  = g.seller_key
LEFT JOIN sla     l  ON l.ms  = g.ms AND l.seller_key  = g.seller_key
LEFT JOIN backlog bk ON bk.ms = g.ms AND bk.seller_key = g.seller_key
LEFT JOIN cum     c  ON c.ms  = g.ms AND c.seller_key  = g.seller_key
WHERE NOT EXISTS (SELECT 1 FROM dw.fact_seller_monthly_snapshot x
                  WHERE x.snapshot_date_key = g.snapshot_date_key AND x.seller_key = g.seller_key);
