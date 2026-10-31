-- =====================================================================
-- 05_dq_checks.sql  —  Kiểm soát chất lượng dữ liệu sau mỗi batch
-- 4 nhóm: CLEANSING (luật làm sạch đã áp dụng), RI (toàn vẹn tham chiếu),
--         RECONCILIATION (đối soát nguồn <-> kho), BUSINESS (luật nghiệp vụ)
-- RI/RECONCILIATION có lỗi -> status FAIL, pipeline dừng (fail-fast).
-- =====================================================================
CREATE OR REPLACE TEMP TABLE dq AS
WITH c AS (SELECT getvariable('cutoff') AS cutoff)

-- ---------------- CLEANSING ----------------
SELECT 'R01' AS check_id, 'CLEANSING' AS category,
       'Zip prefix mất số 0 đầu (customers+sellers) -> lpad 5 ký tự' AS description,
       (SELECT count(*) FROM stg.raw_customers WHERE length(customer_zip_code_prefix) < 5)
     + (SELECT count(*) FROM stg.raw_sellers   WHERE length(seller_zip_code_prefix)   < 5) AS affected_rows,
       'INFO' AS severity, 'FIXED' AS status
UNION ALL SELECT 'R02', 'CLEANSING', 'Đơn delivered nhưng thiếu ngày giao -> giữ trạng thái, loại khỏi tính lead time',
       (SELECT count(*) FROM stg.orders WHERE order_status = 'delivered' AND delivered_ts IS NULL), 'WARN', 'FLAGGED'
UNION ALL SELECT 'R03', 'CLEANSING', 'Mốc thời gian sai thứ tự (carrier < purchase hoặc delivered < carrier) -> gắn cờ dq_timeline_anomaly',
       (SELECT count(*) FROM stg.orders WHERE dq_timeline_anomaly), 'WARN', 'FLAGGED'
UNION ALL SELECT 'R04', 'CLEANSING', 'Sản phẩm thiếu danh mục -> ''unknown''',
       (SELECT count(*) FROM stg.raw_products WHERE product_category_name IS NULL), 'INFO', 'FIXED'
UNION ALL SELECT 'R05', 'CLEANSING', 'Sản phẩm thiếu trọng lượng/kích thước -> size_class ''Unknown''',
       (SELECT count(*) FROM stg.products WHERE weight_g IS NULL OR length_cm IS NULL), 'INFO', 'FIXED'
UNION ALL SELECT 'R06', 'CLEANSING', 'Geolocation trùng nhiều tọa độ/zip -> gộp median về 1 dòng/zip',
       (SELECT count(*) FROM stg.raw_geolocation) - (SELECT count(*) FROM stg.geolocation), 'INFO', 'FIXED'
UNION ALL SELECT 'R07', 'CLEANSING', 'Tọa độ nằm ngoài lãnh thổ Brazil -> loại trước khi gộp',
       (SELECT count(*) FROM stg.raw_geolocation
         WHERE NOT (CAST(geolocation_lat AS DOUBLE) BETWEEN -34 AND 5.3
                AND CAST(geolocation_lng AS DOUBLE) BETWEEN -74 AND -34)), 'INFO', 'FIXED'
UNION ALL SELECT 'R08', 'CLEANSING', 'Tên thành phố có dấu/khác hoa thường -> chuẩn hóa (vd. "são paulo" -> "sao paulo")',
       (SELECT count(*) FROM stg.raw_geolocation WHERE geolocation_city <> stg.norm_city(geolocation_city)), 'INFO', 'FIXED'
UNION ALL SELECT 'R09', 'CLEANSING', 'review_id lặp lại giữa nhiều đơn -> khóa tự nhiên (review_id, order_id)',
       (SELECT count(*) FROM (SELECT review_id FROM stg.reviews GROUP BY 1 HAVING count(*) > 1)), 'INFO', 'FIXED'
UNION ALL SELECT 'R10', 'CLEANSING', 'Đơn không có dòng sản phẩm (đa số unavailable/canceled) -> chỉ nạp vào fact_order_fulfillment',
       (SELECT count(*) FROM stg.orders o WHERE NOT EXISTS (SELECT 1 FROM stg.order_items i WHERE i.order_id = o.order_id)), 'INFO', 'FLAGGED'
UNION ALL SELECT 'R11', 'CLEANSING', 'payment_type = not_defined -> ''unknown''',
       (SELECT count(*) FROM stg.raw_payments WHERE payment_type = 'not_defined'), 'INFO', 'FIXED'
UNION ALL SELECT 'R12', 'CLEANSING', 'Zip không có tọa độ trong geolocation -> has_coordinates = false',
       (SELECT count(*) FROM dw.dim_geography WHERE NOT has_coordinates AND geo_key <> -1), 'INFO', 'FLAGGED'

-- ---------------- REFERENTIAL INTEGRITY ----------------
UNION ALL SELECT 'RI01', 'RI', 'fact_sales: FK mồ côi (date/product/seller/customer/geo/profile)',
       (SELECT count(*) FROM dw.fact_sales f
         WHERE NOT EXISTS (SELECT 1 FROM dw.dim_date d WHERE d.date_key = f.purchase_date_key)
            OR NOT EXISTS (SELECT 1 FROM dw.dim_date d WHERE d.date_key = f.shipping_limit_date_key)
            OR NOT EXISTS (SELECT 1 FROM dw.dim_product p WHERE p.product_key = f.product_key)
            OR NOT EXISTS (SELECT 1 FROM dw.dim_seller s WHERE s.seller_key = f.seller_key)
            OR NOT EXISTS (SELECT 1 FROM dw.dim_customer c WHERE c.customer_key = f.customer_key)
            OR NOT EXISTS (SELECT 1 FROM dw.dim_geography g WHERE g.geo_key = f.customer_geo_key)
            OR NOT EXISTS (SELECT 1 FROM dw.dim_geography g WHERE g.geo_key = f.seller_geo_key)
            OR NOT EXISTS (SELECT 1 FROM dw.dim_order_profile p WHERE p.profile_key = f.profile_key)), 'ERROR', NULL
UNION ALL SELECT 'RI02', 'RI', 'fact_order_fulfillment: FK mồ côi (5 vai trò date, customer, geo, profile)',
       (SELECT count(*) FROM dw.fact_order_fulfillment f
         WHERE NOT EXISTS (SELECT 1 FROM dw.dim_date d WHERE d.date_key = f.purchase_date_key)
            OR NOT EXISTS (SELECT 1 FROM dw.dim_date d WHERE d.date_key = f.approved_date_key)
            OR NOT EXISTS (SELECT 1 FROM dw.dim_date d WHERE d.date_key = f.carrier_date_key)
            OR NOT EXISTS (SELECT 1 FROM dw.dim_date d WHERE d.date_key = f.delivered_date_key)
            OR NOT EXISTS (SELECT 1 FROM dw.dim_date d WHERE d.date_key = f.estimated_date_key)
            OR NOT EXISTS (SELECT 1 FROM dw.dim_customer c WHERE c.customer_key = f.customer_key)
            OR NOT EXISTS (SELECT 1 FROM dw.dim_geography g WHERE g.geo_key = f.customer_geo_key)
            OR NOT EXISTS (SELECT 1 FROM dw.dim_order_profile p WHERE p.profile_key = f.profile_key)), 'ERROR', NULL
UNION ALL SELECT 'RI03', 'RI', 'fact_payment / fact_review / bridge / snapshot: FK mồ côi',
       (SELECT count(*) FROM dw.fact_payment f
         WHERE NOT EXISTS (SELECT 1 FROM dw.dim_payment_type t WHERE t.payment_type_key = f.payment_type_key)
            OR NOT EXISTS (SELECT 1 FROM dw.dim_customer c WHERE c.customer_key = f.customer_key))
     + (SELECT count(*) FROM dw.fact_review f
         WHERE NOT EXISTS (SELECT 1 FROM dw.dim_date d WHERE d.date_key = f.review_date_key)
            OR NOT EXISTS (SELECT 1 FROM dw.dim_date d WHERE d.date_key = f.answer_date_key))
     + (SELECT count(*) FROM dw.bridge_order_seller b
         WHERE NOT EXISTS (SELECT 1 FROM dw.dim_seller s WHERE s.seller_key = b.seller_key))
     + (SELECT count(*) FROM dw.fact_seller_monthly_snapshot f
         WHERE NOT EXISTS (SELECT 1 FROM dw.dim_seller s WHERE s.seller_key = f.seller_key)), 'ERROR', NULL
UNION ALL SELECT 'RI04', 'RI', 'Fact trỏ tới thành viên Unknown (-1) của product/seller/customer',
       (SELECT count(*) FROM dw.fact_sales WHERE product_key = -1 OR seller_key = -1 OR customer_key = -1), 'WARN', NULL

-- ---------------- RECONCILIATION (nguồn as-of cutoff <-> kho) ----------------
UNION ALL SELECT 'RC01', 'RECONCILIATION', 'Số dòng fact_sales = số order_items của đơn có purchase < cutoff',
       abs((SELECT count(*) FROM dw.fact_sales)
         - (SELECT count(*) FROM stg.order_items i JOIN stg.orders o USING (order_id), c WHERE o.purchase_ts < c.cutoff)), 'ERROR', NULL
UNION ALL SELECT 'RC02', 'RECONCILIATION', 'Tổng doanh thu (price) fact_sales = nguồn (sai lệch tính bằng cent)',
       CAST(abs((SELECT sum(price) FROM dw.fact_sales)
              - (SELECT sum(price) FROM stg.order_items i JOIN stg.orders o USING (order_id), c WHERE o.purchase_ts < c.cutoff)) * 100 AS BIGINT), 'ERROR', NULL
UNION ALL SELECT 'RC03', 'RECONCILIATION', 'Số dòng fact_order_fulfillment = số đơn có purchase < cutoff',
       abs((SELECT count(*) FROM dw.fact_order_fulfillment)
         - (SELECT count(*) FROM stg.orders, c WHERE purchase_ts < c.cutoff)), 'ERROR', NULL
UNION ALL SELECT 'RC04', 'RECONCILIATION', 'Tổng payment_value fact_payment = nguồn (cent)',
       CAST(abs((SELECT sum(payment_value) FROM dw.fact_payment)
              - (SELECT sum(payment_value) FROM stg.payments p JOIN stg.orders o USING (order_id), c WHERE o.purchase_ts < c.cutoff)) * 100 AS BIGINT), 'ERROR', NULL
UNION ALL SELECT 'RC05', 'RECONCILIATION', 'Bridge: tổng trọng số phân bổ mỗi đơn = 1',
       (SELECT count(*) FROM (SELECT order_id FROM dw.bridge_order_seller GROUP BY 1 HAVING abs(sum(allocation_weight) - 1) > 1e-9)), 'ERROR', NULL
UNION ALL SELECT 'RC06', 'RECONCILIATION', 'SCD2: mỗi khách đúng 1 phiên bản hiện hành & các khoảng hiệu lực không chồng lấn',
       (SELECT count(*) FROM (SELECT customer_unique_id FROM dw.dim_customer GROUP BY 1 HAVING count(*) FILTER (WHERE is_current) <> 1))
     + (SELECT count(*) FROM (SELECT effective_to, lead(effective_from) OVER (PARTITION BY customer_unique_id ORDER BY version_no) AS nxt
                              FROM dw.dim_customer) WHERE nxt IS NOT NULL AND nxt <> effective_to), 'ERROR', NULL
UNION ALL SELECT 'RC07', 'RECONCILIATION', 'Periodic snapshot: tổng GMV các tháng đã chụp = GMV fact_sales cùng kỳ (cent)',
       CAST(abs(coalesce((SELECT sum(gmv) FROM dw.fact_seller_monthly_snapshot), 0)
              - coalesce((SELECT sum(f.price) FROM dw.fact_sales f
                          WHERE f.purchase_ts < (SELECT max(full_date) + 1 FROM dw.dim_date d
                                                 JOIN dw.fact_seller_monthly_snapshot s ON s.snapshot_date_key = d.date_key)), 0)) * 100 AS BIGINT), 'ERROR', NULL

-- ---------------- BUSINESS RULES ----------------
UNION ALL SELECT 'BR01', 'BUSINESS', 'review_score ngoài miền [1,5]',
       (SELECT count(*) FROM dw.fact_review WHERE review_score NOT BETWEEN 1 AND 5), 'ERROR', NULL
UNION ALL SELECT 'BR02', 'BUSINESS', 'Giá/phí vận chuyển âm',
       (SELECT count(*) FROM dw.fact_sales WHERE price < 0 OR freight_value < 0), 'ERROR', NULL
UNION ALL SELECT 'BR03', 'BUSINESS', 'Tổng thanh toán lệch tổng đơn > 1 BRL (lãi trả góp/voucher) — chỉ thông tin',
       (SELECT count(*) FROM dw.fact_order_fulfillment
         WHERE item_count > 0 AND abs(payment_value - (order_value + freight_value)) > 1), 'INFO', NULL;

INSERT OR REPLACE INTO etl.dq_result
SELECT getvariable('batch_id'), check_id, category, description, affected_rows, severity,
       coalesce(status, CASE WHEN affected_rows = 0 THEN 'PASS'
                             WHEN severity = 'ERROR' THEN 'FAIL'
                             ELSE 'FLAGGED' END)
FROM dq;
