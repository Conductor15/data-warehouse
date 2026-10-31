"""DSS-1: Tối ưu cam kết ngày giao hàng theo tuyến (seller_state -> customer_state).

Bài toán quyết định
    Olist hiển thị "ngày giao dự kiến" cho khách lúc đặt hàng. Hứa quá xa -> mất
    sức cạnh tranh/chuyển đổi; hứa quá gần -> giao trễ -> điểm review sụp đổ.
Biến quyết định   : mức phục vụ alpha (xác suất giao đúng hạn mục tiêu) + đệm (buffer) ngày
Biến không kiểm soát: phân phối thời gian giao thực tế của từng tuyến
Biến kết quả      : tỷ lệ đúng hạn, số ngày cam kết TB, điểm review kỳ vọng, chỉ số nhu cầu
Mô hình
    promise_r = ceil(Q_alpha(lead_time | tuyến r)) + buffer
    Tuyến ít dữ liệu (< min_n) dùng phân phối gộp cấp vùng -> toàn quốc (hierarchical fallback).
    E[review] = sum_k P(bucket_k) * mean_review(bucket_k)   (học từ kho)
    demand_index = mean(exp(-elasticity * (promise_new - promise_olist)))  (GIẢ ĐỊNH, cho phép chỉnh)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import duckdb
import numpy as np
import pandas as pd

DELAY_BINS = [-np.inf, -10, 0, 3, 7, np.inf]
DELAY_LABELS = ["Sớm >=10 ngày", "Sớm/đúng hạn <10 ngày", "Trễ 1-3 ngày", "Trễ 4-7 ngày", "Trễ >7 ngày"]


def load_orders(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Đơn 1 người bán, đã giao, có đủ mốc thời gian, không bất thường."""
    return con.sql("""
        SELECT r.order_id, r.purchase_ts, r.route, r.seller_state, r.customer_state,
               r.seller_region, r.customer_region,
               r.seller_region || ' → ' || r.customer_region AS region_route,
               r.lead_time_days, r.promised_days, r.delay_days, r.is_late, r.review_score,
               CAST(r.order_value AS DOUBLE) AS order_value, CAST(r.freight_value AS DOUBLE) AS freight_value,
               r.distance_km, r.total_weight_g
        FROM mart.v_order_route r
        JOIN dw.fact_order_fulfillment f USING (order_id)
        WHERE r.is_delivered AND r.lead_time_days IS NOT NULL AND NOT f.dq_timeline_anomaly
        ORDER BY r.purchase_ts, r.order_id
    """).df()


def delay_bucket(delay: pd.Series | np.ndarray) -> pd.Categorical:
    return pd.cut(delay, DELAY_BINS, labels=DELAY_LABELS, right=True)


def review_by_bucket(train: pd.DataFrame) -> pd.Series:
    """Điểm review TB theo mức trễ so với cam kết — 'tri thức' học từ kho."""
    t = train.dropna(subset=["review_score"])
    return t.groupby(delay_bucket(t["delay_days"]), observed=False)["review_score"].mean()


@dataclass
class PromisePolicy:
    alpha: float
    buffer_days: int
    min_n: int
    table: pd.DataFrame  # route -> promise_days, level, n_train

    def promise_for(self, df: pd.DataFrame) -> pd.Series:
        m = self.table.set_index("key")["promise_days"]
        p = df["route"].map(m)
        p = p.fillna(df["region_route"].map(m))
        return p.fillna(m["__GLOBAL__"])


def fit_policy(train: pd.DataFrame, alpha: float, buffer_days: int = 0, min_n: int = 30) -> PromisePolicy:
    rows = []
    for level, col in (("route", "route"), ("region", "region_route")):
        g = train.groupby(col)["lead_time_days"]
        q = g.quantile(alpha)
        n = g.size()
        for key in q.index:
            if n[key] >= min_n:
                rows.append((key, level, int(n[key]), math.ceil(q[key]) + buffer_days))
    rows.append(("__GLOBAL__", "global", len(train),
                 math.ceil(train["lead_time_days"].quantile(alpha)) + buffer_days))
    table = pd.DataFrame(rows, columns=["key", "level", "n_train", "promise_days"])
    return PromisePolicy(alpha, buffer_days, min_n, table)


def evaluate(policy: PromisePolicy | None, test: pd.DataFrame, review_map: pd.Series,
             elasticity: float = 0.02) -> dict:
    """Đánh giá chính sách trên dữ liệu kiểm thử (policy=None -> chính sách hiện tại của Olist)."""
    promise = test["promised_days"] if policy is None else policy.promise_for(test)
    delay = test["lead_time_days"] - promise
    on_time = (delay <= 0).mean()
    probs = pd.Series(delay_bucket(delay)).value_counts(normalize=True).reindex(DELAY_LABELS).fillna(0)
    exp_review = float((probs * review_map.reindex(DELAY_LABELS)).sum())
    exp_negative_share = float(probs[DELAY_LABELS[3:]].sum())
    demand = float(np.exp(-elasticity * (promise - test["promised_days"])).mean())
    return {
        "alpha": None if policy is None else policy.alpha,
        "buffer": None if policy is None else policy.buffer_days,
        "avg_promise_days": float(promise.mean()),
        "on_time_rate": float(on_time),
        "late_gt3_share": float((delay > 3).mean()),
        "expected_review": exp_review,
        "severe_late_share": exp_negative_share,
        "demand_index": demand,
    }


def frontier(train: pd.DataFrame, test: pd.DataFrame, alphas=None, buffer_days: int = 0,
             elasticity: float = 0.02, min_n: int = 30) -> pd.DataFrame:
    """Đường biên hiệu quả: số ngày cam kết TB vs tỷ lệ đúng hạn khi thay đổi alpha."""
    alphas = alphas if alphas is not None else np.round(np.arange(0.50, 0.995, 0.025), 3)
    rm = review_by_bucket(train)
    return pd.DataFrame([evaluate(fit_policy(train, a, buffer_days, min_n), test, rm, elasticity)
                         for a in alphas])


def goal_seek(train: pd.DataFrame, valid: pd.DataFrame, target_on_time: float,
              buffer_days: int = 0, min_n: int = 30, tol: float = 1e-3) -> tuple[float, dict]:
    """Tìm alpha NHỎ NHẤT (cam kết ngắn nhất) đạt tỷ lệ đúng hạn mục tiêu trên tập validation (chia đôi)."""
    rm = review_by_bucket(train)
    lo, hi = 0.30, 0.999
    best = None
    while hi - lo > tol:
        mid = (lo + hi) / 2
        res = evaluate(fit_policy(train, mid, buffer_days, min_n), valid, rm)
        if res["on_time_rate"] >= target_on_time:
            hi, best = mid, res
        else:
            lo = mid
    if best is None:
        best = evaluate(fit_policy(train, hi, buffer_days, min_n), valid, rm)
    return hi, best


def monthly_backtest(policy: PromisePolicy, test: pd.DataFrame) -> pd.DataFrame:
    """Độ bền theo tháng: chính sách đề xuất vs Olist trên từng tháng của tập kiểm thử."""
    t = test.assign(month=test["purchase_ts"].dt.strftime("%Y-%m"),
                    promise_new=policy.promise_for(test))
    return t.groupby("month").apply(lambda g: pd.Series({
        "orders": len(g),
        "olist_promise": g["promised_days"].mean(),
        "olist_on_time": (g["lead_time_days"] <= g["promised_days"]).mean(),
        "new_promise": g["promise_new"].mean(),
        "new_on_time": (g["lead_time_days"] <= g["promise_new"]).mean(),
    }), include_groups=False).reset_index()


# ---------------------------------------------------------------------------
# Walk-forward backtest: mỗi tháng M huấn luyện lại trên [M-7, M-1) — bỏ 1 tháng "gap"
# vì tại thời điểm quyết định, nhiều đơn của tháng gần nhất CHƯA giao xong (right-censoring).
# ---------------------------------------------------------------------------
ML_FEATURES = ["seller_state", "customer_state", "distance_km", "total_weight_g",
               "freight_value", "order_value", "moy", "dow"]


def _ml_frame(df: pd.DataFrame) -> pd.DataFrame:
    X = df[["seller_state", "customer_state", "distance_km", "total_weight_g",
            "freight_value", "order_value"]].copy()
    X["moy"] = df["purchase_ts"].dt.month
    X["dow"] = df["purchase_ts"].dt.dayofweek
    for c in ("seller_state", "customer_state"):
        X[c] = X[c].astype("category")
    return X[ML_FEATURES]


def walk_forward(df: pd.DataFrame, alpha: float, kind: str, test_months: list[str],
                 train_months: int = 6, gap: int = 1) -> pd.DataFrame:
    """kind = 'route' (bảng quantile theo tuyến, dễ giải thích) | 'ml' (hồi quy quantile GBM cấp đơn)."""
    from sklearn.ensemble import HistGradientBoostingRegressor

    month = df["purchase_ts"].dt.to_period("M")
    out = []
    for m in pd.PeriodIndex(test_months, freq="M"):
        tr = df[(month >= m - train_months - gap) & (month < m - gap)]
        te = df[month == m]
        if kind == "route":
            p = fit_policy(tr, alpha).promise_for(te).to_numpy()
        else:
            reg = HistGradientBoostingRegressor(loss="quantile", quantile=alpha, max_iter=200,
                                                categorical_features="from_dtype", random_state=0)
            reg.fit(_ml_frame(tr), tr["lead_time_days"])
            p = np.ceil(reg.predict(_ml_frame(te)))
        out.append(pd.DataFrame({"order_id": te["order_id"].to_numpy(), "promise_days": p}))
    return pd.concat(out, ignore_index=True)


def evaluate_promises(test: pd.DataFrame, promise: pd.Series, review_map: pd.Series,
                      elasticity: float = 0.02) -> dict:
    """Như evaluate() nhưng nhận sẵn vector cam kết (dùng cho kết quả walk-forward lưu trong kho)."""
    delay = test["lead_time_days"].to_numpy() - promise.to_numpy()
    probs = pd.Series(delay_bucket(delay)).value_counts(normalize=True).reindex(DELAY_LABELS).fillna(0)
    return {
        "avg_promise_days": float(promise.mean()),
        "on_time_rate": float((delay <= 0).mean()),
        "late_gt3_share": float((delay > 3).mean()),
        "expected_review": float((probs * review_map.reindex(DELAY_LABELS)).sum()),
        "demand_index": float(np.exp(-elasticity * (promise.to_numpy() - test["promised_days"].to_numpy())).mean()),
    }


def route_table(policy: PromisePolicy, train: pd.DataFrame, test: pd.DataFrame) -> pd.DataFrame:
    """Bảng khuyến nghị cam kết theo tuyến (để đưa vào hệ thống vận hành)."""
    t = test.assign(promise_new=policy.promise_for(test),
                    olist_ok=test["lead_time_days"] <= test["promised_days"])
    t["new_ok"] = t["lead_time_days"] <= t["promise_new"]
    agg = t.groupby("route").agg(
        orders_test=("order_id", "size"),
        olist_promise=("promised_days", "mean"),
        actual_p50=("lead_time_days", "median"),
        recommended=("promise_new", "first"),
        olist_on_time=("olist_ok", "mean"),
        new_on_time=("new_ok", "mean"),
    )
    agg["days_saved"] = agg["olist_promise"] - agg["recommended"]
    lvl = policy.table.set_index("key")["level"]
    agg["source_level"] = [lvl.get(r, "region/global") for r in agg.index]
    return agg.sort_values("orders_test", ascending=False).reset_index()
