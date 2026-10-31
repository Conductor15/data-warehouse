-- =====================================================================
-- 01_staging.sql  —  EXTRACT + CLEAN (vùng Staging)
-- ---------------------------------------------------------------------
-- Tầng 1 (landing, stg.raw_*): nạp nguyên trạng CSV, MỌI cột là VARCHAR
--   -> không mất thông tin (vd. số 0 đầu mã zip), có thể audit lại nguồn.
-- Tầng 2 (clean, stg.*): ép kiểu, chuẩn hóa, làm sạch theo bộ luật DQ.
-- Placeholder ${RAW_DIR}, ${REF_DIR} được pipeline.py thay thế lúc chạy.
-- =====================================================================
CREATE SCHEMA IF NOT EXISTS stg;

-- ---------- Tầng 1: landing (raw, all VARCHAR) ----------
CREATE OR REPLACE TABLE stg.raw_orders      AS SELECT * FROM read_csv('${RAW_DIR}/olist_orders_dataset.csv',        header=true, all_varchar=true);
CREATE OR REPLACE TABLE stg.raw_order_items AS SELECT * FROM read_csv('${RAW_DIR}/olist_order_items_dataset.csv',   header=true, all_varchar=true);
CREATE OR REPLACE TABLE stg.raw_payments    AS SELECT * FROM read_csv('${RAW_DIR}/olist_order_payments_dataset.csv',header=true, all_varchar=true);
CREATE OR REPLACE TABLE stg.raw_reviews     AS SELECT * FROM read_csv('${RAW_DIR}/olist_order_reviews_dataset.csv', header=true, all_varchar=true);
CREATE OR REPLACE TABLE stg.raw_customers   AS SELECT * FROM read_csv('${RAW_DIR}/olist_customers_dataset.csv',     header=true, all_varchar=true);
CREATE OR REPLACE TABLE stg.raw_products    AS SELECT * FROM read_csv('${RAW_DIR}/olist_products_dataset.csv',      header=true, all_varchar=true);
CREATE OR REPLACE TABLE stg.raw_sellers     AS SELECT * FROM read_csv('${RAW_DIR}/olist_sellers_dataset.csv',       header=true, all_varchar=true);
CREATE OR REPLACE TABLE stg.raw_geolocation AS SELECT * FROM read_csv('${RAW_DIR}/olist_geolocation_dataset.csv',   header=true, all_varchar=true);

-- Dữ liệu tham chiếu (master data do nhóm biên soạn)
CREATE OR REPLACE TABLE stg.ref_states         AS SELECT * FROM read_csv('${REF_DIR}/brazil_states.csv',  header=true, all_varchar=true);
CREATE OR REPLACE TABLE stg.ref_holidays       AS SELECT CAST(holiday_date AS DATE) AS holiday_date, holiday_name
                                                 FROM read_csv('${REF_DIR}/br_holidays.csv', header=true, all_varchar=true);
CREATE OR REPLACE TABLE stg.ref_category_group AS SELECT * FROM read_csv('${REF_DIR}/category_group.csv', header=true, all_varchar=true);

-- Macro chuẩn hóa tên thành phố: bỏ dấu, chữ thường, gộp khoảng trắng
-- ("São Paulo", "sao  paulo" -> "sao paulo")  — luật DQ R08
CREATE OR REPLACE MACRO stg.norm_city(s) AS
    regexp_replace(lower(trim(strip_accents(s))), '\s+', ' ', 'g');

-- Mã zip prefix có 5 chữ số; nguồn customers/sellers làm mất số 0 đầu — luật DQ R01
CREATE OR REPLACE MACRO stg.norm_zip(z) AS lpad(trim(z), 5, '0');

-- ---------- Tầng 2: clean ----------
-- Orders: ép kiểu timestamp + gắn cờ bất thường về thứ tự mốc thời gian (R03)
CREATE OR REPLACE TABLE stg.orders AS
SELECT
    order_id,
    customer_id,
    lower(trim(order_status))                               AS order_status,
    TRY_CAST(order_purchase_timestamp      AS TIMESTAMP)    AS purchase_ts,
    TRY_CAST(order_approved_at             AS TIMESTAMP)    AS approved_ts,
    TRY_CAST(order_delivered_carrier_date  AS TIMESTAMP)    AS carrier_ts,
    TRY_CAST(order_delivered_customer_date AS TIMESTAMP)    AS delivered_ts,
    TRY_CAST(order_estimated_delivery_date AS TIMESTAMP)    AS estimated_ts,
    (TRY_CAST(order_delivered_carrier_date AS TIMESTAMP)  < TRY_CAST(order_purchase_timestamp AS TIMESTAMP))
        OR (TRY_CAST(order_delivered_customer_date AS TIMESTAMP) < TRY_CAST(order_delivered_carrier_date AS TIMESTAMP))
                                                            AS dq_timeline_anomaly
FROM stg.raw_orders;

CREATE OR REPLACE TABLE stg.order_items AS
SELECT
    order_id,
    CAST(order_item_id AS INTEGER)                  AS order_item_id,
    product_id,
    seller_id,
    TRY_CAST(shipping_limit_date AS TIMESTAMP)      AS shipping_limit_ts,
    CAST(price AS DECIMAL(12,2))                    AS price,
    CAST(freight_value AS DECIMAL(12,2))            AS freight_value
FROM stg.raw_order_items;

-- Payments: 'not_defined' (3 dòng, giá trị 0) -> 'unknown' (R11); installments 0 -> 1
CREATE OR REPLACE TABLE stg.payments AS
SELECT
    order_id,
    CAST(payment_sequential AS INTEGER)                              AS payment_sequential,
    CASE WHEN payment_type = 'not_defined' THEN 'unknown' ELSE payment_type END AS payment_type,
    greatest(CAST(payment_installments AS INTEGER), 1)              AS installments,
    CAST(payment_value AS DECIMAL(12,2))                             AS payment_value
FROM stg.raw_payments;

-- Reviews: khóa tự nhiên là (review_id, order_id) vì review_id bị lặp giữa các đơn (R09)
CREATE OR REPLACE TABLE stg.reviews AS
SELECT
    review_id,
    order_id,
    CAST(review_score AS SMALLINT)                       AS review_score,
    nullif(trim(review_comment_title), '')               AS comment_title,
    nullif(trim(review_comment_message), '')             AS comment_message,
    TRY_CAST(review_creation_date    AS TIMESTAMP)       AS created_ts,
    TRY_CAST(review_answer_timestamp AS TIMESTAMP)       AS answered_ts
FROM stg.raw_reviews
QUALIFY row_number() OVER (PARTITION BY review_id, order_id ORDER BY review_answer_timestamp DESC) = 1;

CREATE OR REPLACE TABLE stg.customers AS
SELECT
    customer_id,
    customer_unique_id,
    stg.norm_zip(customer_zip_code_prefix)  AS zip_prefix,
    stg.norm_city(customer_city)            AS city,
    upper(trim(customer_state))             AS state
FROM stg.raw_customers;

CREATE OR REPLACE TABLE stg.sellers AS
SELECT
    seller_id,
    stg.norm_zip(seller_zip_code_prefix)    AS zip_prefix,
    stg.norm_city(seller_city)              AS city,
    upper(trim(seller_state))               AS state
FROM stg.raw_sellers;

-- Products: danh mục NULL -> 'unknown' (R04); sửa lỗi chính tả bản dịch qua bảng tham chiếu;
-- thiếu kích thước -> NULL + size_class 'unknown' (R05)
CREATE OR REPLACE TABLE stg.products AS
SELECT
    p.product_id,
    coalesce(p.product_category_name, 'unknown')                      AS category_name_pt,
    coalesce(g.category_name, 'unknown')                              AS category_name,
    coalesce(g.category_group, 'Unknown')                             AS category_group,
    TRY_CAST(p.product_name_lenght        AS INTEGER)                 AS name_length,
    TRY_CAST(p.product_description_lenght AS INTEGER)                 AS description_length,
    TRY_CAST(p.product_photos_qty         AS INTEGER)                 AS photos_qty,
    TRY_CAST(p.product_weight_g           AS INTEGER)                 AS weight_g,
    TRY_CAST(p.product_length_cm          AS INTEGER)                 AS length_cm,
    TRY_CAST(p.product_height_cm          AS INTEGER)                 AS height_cm,
    TRY_CAST(p.product_width_cm           AS INTEGER)                 AS width_cm
FROM stg.raw_products p
LEFT JOIN stg.ref_category_group g ON g.category_name_pt = p.product_category_name;

-- Geolocation: ~1M dòng cho ~19K zip -> gộp về 1 dòng/zip (R06).
-- Loại tọa độ nằm ngoài khung lãnh thổ Brazil trước khi lấy median (R07).
CREATE OR REPLACE TABLE stg.geolocation AS
WITH g AS (
    SELECT
        stg.norm_zip(geolocation_zip_code_prefix)  AS zip_prefix,
        CAST(geolocation_lat AS DOUBLE)            AS lat,
        CAST(geolocation_lng AS DOUBLE)            AS lng,
        stg.norm_city(geolocation_city)            AS city,
        upper(geolocation_state)                   AS state
    FROM stg.raw_geolocation
), coords AS (
    SELECT
        zip_prefix,
        median(lat) FILTER (WHERE lat BETWEEN -34 AND 5.3 AND lng BETWEEN -74 AND -34)  AS lat,
        median(lng) FILTER (WHERE lat BETWEEN -34 AND 5.3 AND lng BETWEEN -74 AND -34)  AS lng
    FROM g
    GROUP BY zip_prefix
), city_n AS (
    SELECT zip_prefix, city, count(*) AS n FROM g WHERE city IS NOT NULL GROUP BY ALL
), state_n AS (
    SELECT zip_prefix, state, count(*) AS n FROM g WHERE state IS NOT NULL GROUP BY ALL
)
-- Giá trị xuất hiện nhiều nhất; hòa thì lấy theo thứ tự chữ cái (mode() chọn tùy ý khi hòa -> kết quả không tái lập)
SELECT
    c.zip_prefix, c.lat, c.lng,
    (SELECT first(x.city ORDER BY x.n DESC, x.city) FROM city_n x WHERE x.zip_prefix = c.zip_prefix)    AS city,
    (SELECT first(x.state ORDER BY x.n DESC, x.state) FROM state_n x WHERE x.zip_prefix = c.zip_prefix) AS state
FROM coords c;
