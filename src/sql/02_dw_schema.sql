-- =====================================================================
-- 02_dw_schema.sql  —  DDL kho dữ liệu (Kimball dimensional model)
-- ---------------------------------------------------------------------
-- dw   : dimension + fact (lớp trình bày, star schema)
-- etl  : metadata vận hành (log batch, watermark, kết quả kiểm tra DQ)
-- mart : view/aggregate phục vụ dashboard & DSS (xem 04_mart.sql)
--
-- Ghi chú thiết kế:
--  * Mọi dimension dùng surrogate key (INTEGER), khóa tự nhiên giữ lại để tra cứu.
--  * Khóa -1 = thành viên "Unknown / Chưa xảy ra" (tránh NULL FK trong fact).
--  * Ràng buộc FOREIGN KEY không khai báo vật lý (DuckDB cấm UPDATE dòng được
--    tham chiếu, cản trở SCD1/SCD6); toàn vẹn tham chiếu được kiểm tra bằng
--    bộ test DQ sau mỗi batch (etl.dq_result) — thực hành phổ biến trong DW.
-- =====================================================================
CREATE SCHEMA IF NOT EXISTS dw;
CREATE SCHEMA IF NOT EXISTS etl;
CREATE SCHEMA IF NOT EXISTS mart;

CREATE SEQUENCE IF NOT EXISTS dw.seq_geo_key      START 1;
CREATE SEQUENCE IF NOT EXISTS dw.seq_customer_key START 1;
CREATE SEQUENCE IF NOT EXISTS dw.seq_product_key  START 1;
CREATE SEQUENCE IF NOT EXISTS dw.seq_seller_key   START 1;

-- ============================ DIMENSIONS ============================

-- Role-playing: purchase / approved / carrier / delivered / estimated / review / snapshot date
CREATE TABLE IF NOT EXISTS dw.dim_date (
    date_key        INTEGER PRIMARY KEY,      -- YYYYMMDD, -1 = chưa xảy ra
    full_date       DATE,
    day_of_month    SMALLINT,
    day_of_week     SMALLINT,                 -- 1 = Thứ Hai ... 7 = Chủ Nhật (ISO)
    day_name        VARCHAR,
    is_weekend      BOOLEAN,
    week_of_year    SMALLINT,
    month_num       SMALLINT,
    month_name      VARCHAR,
    year_month      VARCHAR,                  -- '2017-11'
    quarter_num     SMALLINT,
    year_quarter    VARCHAR,                  -- '2017-Q4'
    year_num        SMALLINT,
    is_month_end    BOOLEAN,
    is_holiday      BOOLEAN,
    holiday_name    VARCHAR,
    is_black_friday BOOLEAN
);

-- Conformed geography, role-playing: customer_geo / seller_geo. Phân cấp region > state > city > zip
CREATE TABLE IF NOT EXISTS dw.dim_geography (
    geo_key         INTEGER PRIMARY KEY,
    zip_prefix      VARCHAR UNIQUE,
    city            VARCHAR,
    state           VARCHAR,
    state_name      VARCHAR,
    region          VARCHAR,
    latitude        DOUBLE,
    longitude       DOUBLE,
    has_coordinates BOOLEAN
);

-- SCD Type 6 (hybrid 1+2+3) trên khách hàng:
--   zip/city/state/region  : Type 2 (thêm dòng, giữ lịch sử khi khách đổi địa chỉ)
--   current_state/region   : Type 1 (ghi đè trên MỌI phiên bản -> phân tích theo địa chỉ hiện tại)
--   original_state         : Type 0 (cố định từ lần đầu xuất hiện)
CREATE TABLE IF NOT EXISTS dw.dim_customer (
    customer_key        INTEGER PRIMARY KEY,
    customer_unique_id  VARCHAR NOT NULL,
    zip_prefix          VARCHAR,
    city                VARCHAR,
    state               VARCHAR,
    region              VARCHAR,
    geo_key             INTEGER,
    original_state      VARCHAR,
    current_state       VARCHAR,
    current_region      VARCHAR,
    version_no          SMALLINT,
    effective_from      TIMESTAMP,
    effective_to        TIMESTAMP,              -- 9999-12-31 cho phiên bản hiện hành
    is_current          BOOLEAN,
    batch_id            INTEGER
);

-- SCD Type 1. Phân cấp category_group > category > product
CREATE TABLE IF NOT EXISTS dw.dim_product (
    product_key         INTEGER PRIMARY KEY,
    product_id          VARCHAR UNIQUE,
    category_name_pt    VARCHAR,
    category_name       VARCHAR,
    category_group      VARCHAR,
    name_length         INTEGER,
    description_length  INTEGER,
    photos_qty          INTEGER,
    weight_g            INTEGER,
    length_cm           INTEGER,
    height_cm           INTEGER,
    width_cm            INTEGER,
    volume_cm3          INTEGER,
    weight_class        VARCHAR,                -- Light / Medium / Heavy / Bulky
    size_class          VARCHAR,                -- S / M / L / XL
    batch_id            INTEGER
);

-- SCD Type 1 (địa chỉ) + Type 0 (first_sale_date = cohort onboard)
CREATE TABLE IF NOT EXISTS dw.dim_seller (
    seller_key          INTEGER PRIMARY KEY,
    seller_id           VARCHAR UNIQUE,
    zip_prefix          VARCHAR,
    city                VARCHAR,
    state               VARCHAR,
    region              VARCHAR,
    geo_key             INTEGER,
    first_sale_date     DATE,
    batch_id            INTEGER
);

CREATE TABLE IF NOT EXISTS dw.dim_payment_type (
    payment_type_key    INTEGER PRIMARY KEY,
    payment_type        VARCHAR UNIQUE,
    payment_type_label  VARCHAR,
    is_card             BOOLEAN
);

-- Junk dimension: gom các cờ/thuộc tính ít giá trị của đơn hàng (5 x 4 x 2 x 2 x 2 = 160 tổ hợp)
CREATE TABLE IF NOT EXISTS dw.dim_order_profile (
    profile_key           INTEGER PRIMARY KEY,
    primary_payment_type  VARCHAR,
    installment_band      VARCHAR,              -- '1x' | '2-3x' | '4-6x' | '7x+'
    has_voucher           BOOLEAN,
    is_multi_payment      BOOLEAN,
    is_multi_seller       BOOLEAN,
    profile_label         VARCHAR
);

-- ============================== FACTS ===============================

-- (1) Transaction fact — grain: 1 dòng sản phẩm trong đơn (order line)
CREATE TABLE IF NOT EXISTS dw.fact_sales (
    order_id                VARCHAR,           -- degenerate dimension
    order_item_id           INTEGER,           -- degenerate dimension
    purchase_date_key       INTEGER,
    shipping_limit_date_key INTEGER,
    customer_key            INTEGER,
    product_key             INTEGER,
    seller_key              INTEGER,
    customer_geo_key        INTEGER,           -- role-playing dim_geography
    seller_geo_key          INTEGER,           -- role-playing dim_geography
    profile_key             INTEGER,           -- junk dimension
    quantity                SMALLINT,          -- additive (=1)
    price                   DECIMAL(12,2),     -- additive
    freight_value           DECIMAL(12,2),     -- additive
    item_total              DECIMAL(12,2),     -- additive
    distance_km             DOUBLE,            -- non-additive (dùng AVG)
    purchase_ts             TIMESTAMP,
    shipping_limit_ts       TIMESTAMP,
    batch_id                INTEGER,
    PRIMARY KEY (order_id, order_item_id)
);

-- (2) Accumulating snapshot — grain: 1 đơn hàng; cập nhật khi mỗi mốc (milestone) xảy ra
CREATE TABLE IF NOT EXISTS dw.fact_order_fulfillment (
    order_id            VARCHAR PRIMARY KEY,   -- degenerate dimension
    customer_key        INTEGER,
    customer_geo_key    INTEGER,
    profile_key         INTEGER,
    purchase_date_key   INTEGER,               -- 5 vai trò của dim_date
    approved_date_key   INTEGER,
    carrier_date_key    INTEGER,
    delivered_date_key  INTEGER,
    estimated_date_key  INTEGER,
    order_status        VARCHAR,
    purchase_ts         TIMESTAMP,
    approved_ts         TIMESTAMP,
    carrier_ts          TIMESTAMP,
    delivered_ts        TIMESTAMP,
    estimated_ts        TIMESTAMP,
    item_count          INTEGER,
    seller_count        INTEGER,
    order_value         DECIMAL(12,2),
    freight_value       DECIMAL(12,2),
    payment_value       DECIMAL(12,2),
    approval_hours      DOUBLE,                -- lag: purchase -> approved
    handling_days       DOUBLE,                -- lag: approved -> carrier (người bán xử lý)
    transit_days        DOUBLE,                -- lag: carrier -> customer (vận chuyển)
    lead_time_days      DOUBLE,                -- purchase -> delivered
    promised_days       DOUBLE,                -- purchase -> estimated (cam kết)
    delay_days          DOUBLE,                -- delivered - estimated (>0 là trễ)
    is_delivered        BOOLEAN,
    is_late             BOOLEAN,
    is_canceled         BOOLEAN,
    dq_timeline_anomaly BOOLEAN,
    first_batch_id      INTEGER,
    last_batch_id       INTEGER,
    update_count        INTEGER                -- số lần dòng được cập nhật qua các batch
);

-- (3) Transaction fact — grain: 1 lần thanh toán (đơn có thể trả bằng thẻ + voucher)
CREATE TABLE IF NOT EXISTS dw.fact_payment (
    order_id            VARCHAR,
    payment_sequential  INTEGER,
    purchase_date_key   INTEGER,
    customer_key        INTEGER,
    payment_type_key    INTEGER,
    profile_key         INTEGER,
    installments        SMALLINT,
    payment_value       DECIMAL(12,2),
    batch_id            INTEGER,
    PRIMARY KEY (order_id, payment_sequential)
);

-- (4) Transaction fact — grain: 1 đánh giá
CREATE TABLE IF NOT EXISTS dw.fact_review (
    review_id           VARCHAR,
    order_id            VARCHAR,
    review_date_key     INTEGER,
    answer_date_key     INTEGER,
    purchase_date_key   INTEGER,
    customer_key        INTEGER,
    review_score        SMALLINT,
    is_negative         BOOLEAN,               -- score <= 2
    is_positive         BOOLEAN,               -- score >= 4
    has_title           BOOLEAN,
    has_comment         BOOLEAN,
    comment_length      INTEGER,
    response_hours      DOUBLE,
    batch_id            INTEGER,
    PRIMARY KEY (review_id, order_id)
);

-- Bridge (nhiều-nhiều) đơn hàng <-> người bán, trọng số = tỷ trọng doanh thu.
-- Dùng để phân bổ các số đo cấp đơn (điểm review, trễ hạn) về từng seller.
CREATE TABLE IF NOT EXISTS dw.bridge_order_seller (
    order_id            VARCHAR,
    seller_key          INTEGER,
    item_count          INTEGER,
    revenue             DECIMAL(12,2),
    allocation_weight   DOUBLE,                -- SUM(weight) theo order_id = 1
    PRIMARY KEY (order_id, seller_key)
);

-- (5) Periodic snapshot — grain: 1 seller x 1 tháng (chụp tại ngày cuối tháng)
CREATE TABLE IF NOT EXISTS dw.fact_seller_monthly_snapshot (
    snapshot_date_key   INTEGER,               -- ngày cuối tháng
    seller_key          INTEGER,
    orders_cnt          INTEGER,               -- flow, additive
    items_cnt           INTEGER,
    gmv                 DECIMAL(14,2),
    freight_value       DECIMAL(14,2),
    customers_cnt       INTEGER,               -- distinct -> non-additive
    delivered_w         DOUBLE,                -- số đơn giao trong tháng (đã phân bổ trọng số)
    late_w              DOUBLE,
    review_w            DOUBLE,                -- tử/mẫu để tính điểm TB (giữ tính cộng được)
    review_score_sum_w  DOUBLE,
    negative_review_w   DOUBLE,
    handed_items        INTEGER,               -- item giao cho hãng vận chuyển trong tháng
    sla_breach_items    INTEGER,               -- trong đó trễ shipping_limit
    canceled_items      INTEGER,
    open_orders_eom     INTEGER,               -- stock: semi-additive (KHÔNG cộng theo thời gian)
    cum_gmv             DECIMAL(16,2),         -- lũy kế: semi-additive
    batch_id            INTEGER,
    PRIMARY KEY (snapshot_date_key, seller_key)
);

-- ============================ ETL METADATA ===========================
CREATE TABLE IF NOT EXISTS etl.batch_log (
    batch_id        INTEGER PRIMARY KEY,
    batch_type      VARCHAR,                   -- INITIAL | INCREMENTAL
    window_start    TIMESTAMP,
    window_end      TIMESTAMP,
    started_at      TIMESTAMP,
    finished_at     TIMESTAMP,
    duration_sec    DOUBLE,
    status          VARCHAR,
    message         VARCHAR
);

CREATE TABLE IF NOT EXISTS etl.load_stats (
    batch_id        INTEGER,
    table_name      VARCHAR,
    rows_inserted   BIGINT,
    rows_updated    BIGINT,
    PRIMARY KEY (batch_id, table_name)
);

CREATE TABLE IF NOT EXISTS etl.watermark (
    name            VARCHAR PRIMARY KEY,
    value           TIMESTAMP
);

CREATE TABLE IF NOT EXISTS etl.dq_result (
    batch_id        INTEGER,
    check_id        VARCHAR,
    category        VARCHAR,                   -- PROFILING | CLEANSING | RI | RECONCILIATION | BUSINESS
    description     VARCHAR,
    affected_rows   BIGINT,
    severity        VARCHAR,                   -- INFO | WARN | ERROR
    status          VARCHAR,                   -- PASS | FIXED | FLAGGED | FAIL
    PRIMARY KEY (batch_id, check_id)
);
