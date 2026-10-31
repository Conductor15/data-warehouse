"""Kiểm thử tự động kho dữ liệu & DSS.   Chạy:  python -m pytest src/tests -q
(yêu cầu đã chạy pipeline ETL và build_dss trước)"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "etl"))

from dss import seller_mcdm as sm  # noqa: E402
from olap import connect, load_queries  # noqa: E402
from pipeline import split_steps, read_sql  # noqa: E402


@pytest.fixture(scope="module")
def con():
    c = connect(read_only=True)
    yield c
    c.close()


def one(con, sql):
    return con.sql(sql).fetchall()[0][0]


# ---------------- Grain: khóa chính của mỗi fact là duy nhất ----------------
@pytest.mark.parametrize("table,key", [
    ("dw.fact_sales", "order_id, order_item_id"),
    ("dw.fact_order_fulfillment", "order_id"),
    ("dw.fact_payment", "order_id, payment_sequential"),
    ("dw.fact_review", "review_id, order_id"),
    ("dw.fact_seller_monthly_snapshot", "snapshot_date_key, seller_key"),
    ("dw.bridge_order_seller", "order_id, seller_key"),
])
def test_fact_grain_unique(con, table, key):
    assert one(con, f"SELECT count(*) - count(DISTINCT ({key})) FROM {table}") == 0


def test_dq_no_failures(con):
    assert one(con, "SELECT count(*) FROM etl.dq_result WHERE status = 'FAIL'") == 0


def test_all_batches_succeeded(con):
    assert one(con, "SELECT count(*) FROM etl.batch_log WHERE status <> 'SUCCESS'") == 0


# ---------------- Đối soát nguồn <-> kho ----------------
def test_revenue_reconciles_with_source(con):
    src = one(con, "SELECT sum(CAST(price AS DECIMAL(14,2))) FROM stg.raw_order_items")
    dw = one(con, "SELECT sum(price) FROM dw.fact_sales")
    assert src == dw


def test_accumulating_snapshot_final_state_matches_source(con):
    assert one(con, """SELECT count(*) FROM dw.fact_order_fulfillment f JOIN stg.orders s USING (order_id)
                       WHERE f.order_status <> s.order_status
                          OR f.delivered_ts IS DISTINCT FROM s.delivered_ts""") == 0


def test_accumulating_snapshot_was_updated_incrementally(con):
    assert one(con, "SELECT count(*) FROM dw.fact_order_fulfillment WHERE update_count > 0") > 10_000


# ---------------- SCD ----------------
def test_scd2_single_current_version(con):
    assert one(con, """SELECT count(*) FROM (SELECT customer_unique_id FROM dw.dim_customer
                       GROUP BY 1 HAVING count(*) FILTER (WHERE is_current) <> 1)""") == 0


def test_scd2_intervals_contiguous(con):
    assert one(con, """SELECT count(*) FROM (
                         SELECT effective_to, lead(effective_from) OVER (PARTITION BY customer_unique_id ORDER BY version_no) nxt
                         FROM dw.dim_customer) WHERE nxt IS NOT NULL AND nxt <> effective_to""") == 0


def test_scd2_point_in_time_lookup(con):
    # mỗi dòng fact trỏ đúng phiên bản có hiệu lực tại thời điểm mua
    assert one(con, """SELECT count(*) FROM dw.fact_sales f JOIN dw.dim_customer c USING (customer_key)
                       WHERE NOT (f.purchase_ts >= c.effective_from AND f.purchase_ts < c.effective_to)""") == 0


def test_scd6_current_state_consistent(con):
    assert one(con, """SELECT count(*) FROM dw.dim_customer d JOIN dw.dim_customer cur
                       ON cur.customer_unique_id = d.customer_unique_id AND cur.is_current
                       WHERE d.current_state <> cur.state""") == 0


# ---------------- Bridge & snapshot ----------------
def test_bridge_weights_sum_to_one(con):
    assert one(con, """SELECT max(abs(s - 1)) FROM (SELECT sum(allocation_weight) s FROM dw.bridge_order_seller GROUP BY order_id)""") < 1e-9


def test_snapshot_is_dense(con):
    # mọi seller đã onboard đều có dòng ở mọi tháng sau khi onboard
    assert one(con, """SELECT count(*) FROM dw.dim_seller s JOIN dw.dim_date d
                       ON d.is_month_end AND d.full_date >= s.first_sale_date AND d.full_date <= DATE '2018-10-31'
                       WHERE s.seller_key > 0 AND NOT EXISTS (SELECT 1 FROM dw.fact_seller_monthly_snapshot f
                             WHERE f.seller_key = s.seller_key AND f.snapshot_date_key = d.date_key)""") == 0


# ---------------- OLAP ----------------
def test_all_olap_queries_run(con):
    qs = load_queries()
    assert len(qs) == 14
    for q in qs:
        assert len(con.sql(q.sql).df()) > 0, q.qid


def test_rollup_total_equals_sum_of_months(con):
    q = next(x for x in load_queries() if x.qid == "Q01")
    df = con.sql(q.sql).df()
    grand = df[df.nam == "== TỔNG =="].doanh_thu.iloc[0]
    months = df[df.thang != ""].doanh_thu.sum()
    assert abs(grand - months) < 20  # sai số do làm tròn từng dòng


# ---------------- DSS ----------------
def test_ahp_default_matrix_consistent():
    r = sm.ahp_weights(sm.DEFAULT_PAIRWISE)
    assert r["CR"] < 0.1 and abs(r["weights"].sum() - 1) < 1e-9


def test_topsis_closeness_in_unit_interval():
    X = np.random.default_rng(0).random((50, 3))
    c = sm.topsis(X, np.array([0.5, 0.3, 0.2]), np.array([True, False, True]))
    assert ((c >= 0) & (c <= 1)).all()


def test_dss_outputs_written_back(con):
    for t in ("dss_promise_backtest", "dss_late_risk_scores", "dss_seller_metrics"):
        assert one(con, f"SELECT count(*) FROM mart.{t}") > 0


def test_late_risk_model_beats_random(con):
    assert one(con, "SELECT roc_auc FROM mart.dss_late_risk_metrics WHERE model='Gradient Boosting' AND split='test'") > 0.7


def test_etl_step_parser():
    steps = [name for name, _ in split_steps(read_sql("10_batch_load.sql"))]
    assert steps[0] == "asof_source" and "dim_customer_scd2" in steps and steps[-1] == "fact_seller_monthly_snapshot"
