"""DSS-2: Cảnh báo sớm đơn có nguy cơ giao trễ (early-warning) + chọn ngưỡng can thiệp theo chi phí.

Thời điểm ra quyết định: khi người bán BÀN GIAO đơn cho hãng vận chuyển (mốc carrier của
accumulating snapshot). Mọi đặc trưng chỉ dùng thông tin có tại thời điểm đó (point-in-time):
  * lịch sử người bán lấy từ periodic snapshot của THÁNG TRƯỚC;
  * "độ tắc nghẽn" của bang nhận = thời gian vận chuyển TB / tỷ lệ trễ của các đơn đã GIAO
    trong 14 ngày trước ngày bàn giao (rolling window) -> bắt được các cú sốc hệ thống;
  -> tránh rò rỉ dữ liệu tương lai (data leakage).

Quyết định: có can thiệp (nâng cấp vận chuyển / nhắc người bán / báo trước khách) hay không.
    lợi ích kỳ vọng(t) = TP(t) * C_late * effectiveness - (TP(t) + FP(t)) * C_action
    -> chọn ngưỡng t* tối đa hóa lợi ích trên tập validation, báo cáo trên tập test.
    Phương án "không can thiệp" (ngưỡng NO_ACTION, lợi ích = 0) luôn nằm trong tập lựa chọn.
"""
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

CAT_FEATURES = ["seller_state", "customer_state", "category_group", "primary_payment_type"]
NUM_FEATURES = [
    "slack_days", "handling_days", "promised_days", "approval_hours",
    "state_transit_14d", "state_late_14d", "state_volume_14d", "distance_km", "total_weight_g", "freight_value", "order_value",
    "freight_ratio", "item_count", "purchase_month", "purchase_dow", "purchase_hour", "is_holiday",
    "seller_hist_orders", "seller_hist_late_rate", "seller_hist_review", "seller_hist_sla_breach",
    "seller_tenure_months",
]
PRIOR_LATE, PRIOR_REVIEW, PRIOR_SLA, K = 0.07, 4.1, 0.08, 10.0
NO_ACTION = 1.01   # ngưỡng lớn hơn mọi xác suất -> không gắn cờ đơn nào

FEATURE_SQL = f"""
WITH hist AS (
    SELECT s.seller_key, d.full_date AS snap_date,
           sum(s.orders_cnt)         OVER w AS cum_orders,
           sum(s.delivered_w)        OVER w AS cum_deliv,
           sum(s.late_w)             OVER w AS cum_late,
           sum(s.review_w)           OVER w AS cum_rw,
           sum(s.review_score_sum_w) OVER w AS cum_rs,
           sum(s.handed_items)       OVER w AS cum_hand,
           sum(s.sla_breach_items)   OVER w AS cum_breach
    FROM dw.fact_seller_monthly_snapshot s
    JOIN dw.dim_date d ON d.date_key = s.snapshot_date_key
    WINDOW w AS (PARTITION BY s.seller_key ORDER BY s.snapshot_date_key ROWS UNBOUNDED PRECEDING)
), daily AS (   -- giao hàng theo ngày x bang nhận
    SELECT CAST(o.delivered_ts AS DATE) AS d, g.state,
           count(*) AS n, sum(o.transit_days) AS s_transit, sum(CAST(o.is_late AS INT)) AS n_late
    FROM dw.fact_order_fulfillment o JOIN dw.dim_geography g ON g.geo_key = o.customer_geo_key
    WHERE o.is_delivered AND o.transit_days IS NOT NULL AND NOT o.dq_timeline_anomaly
    GROUP BY 1, 2
), cal AS (
    SELECT CAST(t.d AS DATE) AS d, s.state
    FROM generate_series(DATE '2016-09-01', DATE '2018-10-31', INTERVAL 1 DAY) t(d)
    CROSS JOIN (SELECT DISTINCT state FROM daily) s
), roll AS (   -- cửa sổ trượt 14 ngày KẾT THÚC TRƯỚC ngày d (không gồm ngày d)
    SELECT c.d, c.state,
           sum(coalesce(x.n, 0))         OVER w AS n14,
           sum(coalesce(x.s_transit, 0)) OVER w AS t14,
           sum(coalesce(x.n_late, 0))    OVER w AS l14
    FROM cal c LEFT JOIN daily x ON x.d = c.d AND x.state = c.state
    WINDOW w AS (PARTITION BY c.state ORDER BY c.d ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING)
), main_cat AS (
    SELECT f.order_id, first(p.category_group ORDER BY f.price DESC, f.order_item_id) AS category_group
    FROM dw.fact_sales f JOIN dw.dim_product p USING (product_key) GROUP BY 1
)
SELECT
    o.order_id, o.purchase_ts, o.carrier_ts, o.is_late,
    r.seller_state, r.customer_state, mc.category_group, pr.primary_payment_type,
    date_diff('second', o.carrier_ts, o.estimated_ts) / 86400.0      AS slack_days,
    o.handling_days, o.promised_days, o.approval_hours,
    coalesce(rl.t14 / nullif(rl.n14, 0), 9.0)                          AS state_transit_14d,
    coalesce(rl.l14 / nullif(rl.n14, 0), 0.07)                         AS state_late_14d,
    coalesce(rl.n14, 0)                                                AS state_volume_14d, r.distance_km, r.total_weight_g,
    o.freight_value, o.order_value,
    o.freight_value / nullif(o.order_value, 0)                         AS freight_ratio,
    o.item_count,
    month(o.purchase_ts) AS purchase_month, isodow(o.purchase_ts) AS purchase_dow,
    hour(o.purchase_ts)  AS purchase_hour,
    CAST(dd.is_holiday OR dd.is_black_friday AS INT)                   AS is_holiday,
    coalesce(h.cum_orders, 0)                                          AS seller_hist_orders,
    (coalesce(h.cum_late, 0) + {PRIOR_LATE} * {K}) / (coalesce(h.cum_deliv, 0) + {K})     AS seller_hist_late_rate,
    (coalesce(h.cum_rs, 0) + {PRIOR_REVIEW} * {K}) / (coalesce(h.cum_rw, 0) + {K})        AS seller_hist_review,
    (coalesce(h.cum_breach, 0) + {PRIOR_SLA} * {K}) / (coalesce(h.cum_hand, 0) + {K})     AS seller_hist_sla_breach,
    date_diff('month', ds.first_sale_date, CAST(o.purchase_ts AS DATE))                  AS seller_tenure_months
FROM dw.fact_order_fulfillment o
JOIN mart.v_order_route r       ON r.order_id = o.order_id
JOIN dw.bridge_order_seller b   ON b.order_id = o.order_id
JOIN dw.dim_seller ds           ON ds.seller_key = b.seller_key
JOIN dw.dim_order_profile pr    ON pr.profile_key = o.profile_key
JOIN dw.dim_date dd             ON dd.date_key = o.purchase_date_key
LEFT JOIN main_cat mc           ON mc.order_id = o.order_id
LEFT JOIN roll rl               ON rl.state = r.customer_state AND rl.d = CAST(o.carrier_ts AS DATE)
LEFT JOIN hist h                ON h.seller_key = b.seller_key
                               AND h.snap_date = last_day(CAST(o.purchase_ts AS DATE) - INTERVAL 1 MONTH)
WHERE o.is_delivered AND o.delivered_ts IS NOT NULL AND o.carrier_ts IS NOT NULL
  AND NOT o.dq_timeline_anomaly
ORDER BY o.purchase_ts, o.order_id
"""

SPLITS = {
    "train": ("2017-01-01", "2018-01-01"),
    "valid": ("2018-01-01", "2018-03-01"),
    "test":  ("2018-03-01", "2018-09-01"),
}


def load_features(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    df = con.sql(FEATURE_SQL).df()
    df["is_late"] = df["is_late"].astype(int)
    for c in CAT_FEATURES:
        df[c] = df[c].fillna("NA").astype("category")
    return df


def split(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {k: df[(df.purchase_ts >= a) & (df.purchase_ts < b)] for k, (a, b) in SPLITS.items()}


def make_models() -> dict[str, Pipeline | HistGradientBoostingClassifier]:
    logit = Pipeline([
        ("prep", ColumnTransformer([
            ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=20), CAT_FEATURES),
            ("num", Pipeline([("sc", StandardScaler())]), NUM_FEATURES),
        ])),
        ("clf", LogisticRegression(max_iter=2000, class_weight="balanced", C=0.5)),
    ])
    gbm = HistGradientBoostingClassifier(
        categorical_features="from_dtype", learning_rate=0.05, max_iter=400,
        max_leaf_nodes=31, l2_regularization=1.0, early_stopping=True, validation_fraction=0.15,
        random_state=42)
    return {"Logistic Regression": logit, "Gradient Boosting": gbm}


def _X(df: pd.DataFrame) -> pd.DataFrame:
    return df[CAT_FEATURES + NUM_FEATURES].fillna({c: 0 for c in NUM_FEATURES})


def fit_and_score(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Huấn luyện 2 mô hình; trả về (bảng metric, điểm rủi ro theo đơn ở valid+test, các model)."""
    parts = split(df)
    models = make_models()
    rows, scored = [], []
    for name, m in models.items():
        m.fit(_X(parts["train"]), parts["train"]["is_late"])
        for part in ("valid", "test"):
            d = parts[part]
            p = m.predict_proba(_X(d))[:, 1]
            y = d["is_late"].to_numpy()
            top = np.argsort(-p)[: max(1, len(p) // 10)]
            rows.append({
                "model": name, "split": part, "n": len(d), "late_rate": y.mean(),
                "roc_auc": roc_auc_score(y, p), "pr_auc": average_precision_score(y, p),
                "brier": brier_score_loss(y, p) if name != "Logistic Regression" else np.nan,
                "lift_top10": y[top].mean() / y.mean(),
                "recall_top10": y[top].sum() / y.sum(),
            })
            scored.append(pd.DataFrame({"model": name, "split": part, "order_id": d["order_id"].values,
                                        "purchase_ts": d["purchase_ts"].values, "is_late": y, "risk": p}))
    return pd.DataFrame(rows), pd.concat(scored, ignore_index=True), models


def decision_curve(scored: pd.DataFrame, c_late: float = 25.0, c_action: float = 4.0,
                   effectiveness: float = 0.5, thresholds=None) -> pd.DataFrame:
    """Lợi ích ròng kỳ vọng (BRL) theo ngưỡng — đầu vào cho bước CHOICE của DSS."""
    if thresholds is None:
        thresholds = [*np.round(np.arange(0.02, 0.81, 0.01), 2), NO_ACTION]
    y, p = scored["is_late"].to_numpy(), scored["risk"].to_numpy()
    out = []
    for t in thresholds:
        flag = p >= t
        tp, fp = int((flag & (y == 1)).sum()), int((flag & (y == 0)).sum())
        out.append({"threshold": t, "flagged": int(flag.sum()), "flag_rate": flag.mean(),
                    "tp": tp, "fp": fp, "precision": tp / max(tp + fp, 1), "recall": tp / max(y.sum(), 1),
                    "net_benefit": tp * c_late * effectiveness - (tp + fp) * c_action})
    return pd.DataFrame(out)


def best_threshold(curve: pd.DataFrame) -> float:
    """Ngưỡng có lợi ích ròng lớn nhất; trả về NO_ACTION nếu không ngưỡng nào có lãi."""
    best = curve.loc[curve["net_benefit"].idxmax()]
    return float(best["threshold"]) if best["net_benefit"] > 0 else NO_ACTION


def feature_importance(model: HistGradientBoostingClassifier, df: pd.DataFrame, n_repeats: int = 3) -> pd.DataFrame:
    from sklearn.inspection import permutation_importance
    d = split(df)["test"].sample(min(8000, len(split(df)["test"])), random_state=0)
    r = permutation_importance(model, _X(d), d["is_late"], scoring="roc_auc", n_repeats=n_repeats,
                               random_state=0)
    return (pd.DataFrame({"feature": CAT_FEATURES + NUM_FEATURES, "importance": r.importances_mean})
            .sort_values("importance", ascending=False).reset_index(drop=True))
