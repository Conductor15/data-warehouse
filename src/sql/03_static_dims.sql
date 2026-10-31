-- =====================================================================
-- 03_static_dims.sql  —  Dimension sinh sẵn (không phụ thuộc batch)
-- dim_date, dim_payment_type, dim_order_profile (junk) + thành viên Unknown
-- Idempotent: chạy lại nhiều lần không sinh trùng.
-- =====================================================================

-- ---- dim_date: lịch 2016-01-01 .. 2020-12-31 + dòng -1 ----
-- (shipping_limit_date có giá trị ngoại lai tới 2020 -> lịch phủ rộng hơn dữ liệu giao dịch)
INSERT INTO dw.dim_date
SELECT
    CAST(strftime(d, '%Y%m%d') AS INTEGER)                       AS date_key,
    CAST(d AS DATE)                                              AS full_date,
    dayofmonth(d), isodow(d), dayname(d),
    isodow(d) IN (6, 7)                                          AS is_weekend,
    weekofyear(d), month(d), monthname(d),
    strftime(d, '%Y-%m')                                         AS year_month,
    quarter(d),
    CAST(year(d) AS VARCHAR) || '-Q' || CAST(quarter(d) AS VARCHAR),
    year(d),
    CAST(d AS DATE) = last_day(CAST(d AS DATE))                  AS is_month_end,
    h.holiday_date IS NOT NULL AND h.holiday_name <> 'Black Friday',
    h.holiday_name,
    coalesce(h.holiday_name = 'Black Friday', false)
FROM generate_series(TIMESTAMP '2016-01-01', TIMESTAMP '2020-12-31', INTERVAL 1 DAY) t(d)
LEFT JOIN stg.ref_holidays h ON h.holiday_date = CAST(d AS DATE)
ON CONFLICT DO NOTHING;

INSERT INTO dw.dim_date VALUES
    (-1, NULL, NULL, NULL, 'N/A', NULL, NULL, NULL, 'N/A', 'N/A', NULL, 'N/A', NULL, NULL, false, NULL, false)
ON CONFLICT DO NOTHING;

-- ---- dim_payment_type ----
INSERT INTO dw.dim_payment_type VALUES
    (1, 'credit_card', 'Thẻ tín dụng',            true),
    (2, 'boleto',      'Boleto (phiếu thanh toán)', false),
    (3, 'voucher',     'Voucher',                   false),
    (4, 'debit_card',  'Thẻ ghi nợ',                true),
    (-1,'unknown',     'Không xác định',            false)
ON CONFLICT DO NOTHING;

-- ---- dim_order_profile (junk): sinh sẵn toàn bộ tổ hợp (Kimball khuyến nghị) ----
INSERT INTO dw.dim_order_profile
SELECT
    row_number() OVER (ORDER BY pt, ib, hv, mp, ms)              AS profile_key,
    pt, ib, hv, mp, ms,
    pt || ' | ' || ib
       || CASE WHEN hv THEN ' | +voucher'     ELSE '' END
       || CASE WHEN mp THEN ' | multi-pay'    ELSE '' END
       || CASE WHEN ms THEN ' | multi-seller' ELSE '' END       AS profile_label
FROM       (VALUES ('credit_card'), ('boleto'), ('voucher'), ('debit_card'), ('unknown')) a(pt)
CROSS JOIN (VALUES ('1x'), ('2-3x'), ('4-6x'), ('7x+'))                                  b(ib)
CROSS JOIN (VALUES (false), (true)) c(hv)
CROSS JOIN (VALUES (false), (true)) d(mp)
CROSS JOIN (VALUES (false), (true)) e(ms)
ON CONFLICT DO NOTHING;

INSERT INTO dw.dim_order_profile VALUES (-1, 'unknown', 'N/A', false, false, false, 'Unknown')
ON CONFLICT DO NOTHING;

-- ---- Thành viên Unknown cho các dimension động ----
INSERT INTO dw.dim_geography VALUES (-1, '00000', 'unknown', 'NA', 'Unknown', 'Unknown', NULL, NULL, false)
ON CONFLICT DO NOTHING;
INSERT INTO dw.dim_product (product_key, product_id, category_name_pt, category_name, category_group, weight_class, size_class)
VALUES (-1, 'UNKNOWN', 'unknown', 'unknown', 'Unknown', 'Unknown', 'Unknown')
ON CONFLICT DO NOTHING;
INSERT INTO dw.dim_seller (seller_key, seller_id, city, state, region, geo_key)
VALUES (-1, 'UNKNOWN', 'unknown', 'NA', 'Unknown', -1)
ON CONFLICT DO NOTHING;
INSERT INTO dw.dim_customer (customer_key, customer_unique_id, city, state, region, geo_key, version_no,
                             effective_from, effective_to, is_current)
VALUES (-1, 'UNKNOWN', 'unknown', 'NA', 'Unknown', -1, 1, TIMESTAMP '1900-01-01', TIMESTAMP '9999-12-31', true)
ON CONFLICT DO NOTHING;
