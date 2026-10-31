"""DSS-3: Đánh giá & phân loại danh mục người bán bằng ra quyết định đa tiêu chí (MCDM).

    1) AHP (Saaty): nhà quản lý so sánh cặp 6 tiêu chí -> vector trọng số (eigenvector chính),
       kiểm tra tính nhất quán CR = CI / RI < 0.10.
    2) TOPSIS: xếp hạng seller theo khoảng cách tới phương án lý tưởng / phản lý tưởng.
    3) Phân tích độ nhạy: nhiễu trọng số Monte Carlo (Dirichlet quanh trọng số AHP)
       -> xác suất mỗi seller giữ vị trí Top-K (độ vững của khuyến nghị).
    4) Luật hành động: Chiến lược / Duy trì / Theo dõi / Can thiệp.
Dữ liệu đầu vào: periodic snapshot dw.fact_seller_monthly_snapshot (6 tháng gần nhất).
"""
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

CRITERIA = ["gmv", "growth", "on_time_rate", "avg_review", "sla_breach_rate", "cancel_rate"]
CRITERIA_VN = {
    "gmv": "Doanh thu (GMV)", "growth": "Tăng trưởng 3T/3T", "on_time_rate": "Tỷ lệ giao đúng hạn",
    "avg_review": "Điểm review TB", "sla_breach_rate": "Tỷ lệ trễ bàn giao (SLA)", "cancel_rate": "Tỷ lệ hủy",
}
BENEFIT = {"gmv": True, "growth": True, "on_time_rate": True, "avg_review": True,
           "sla_breach_rate": False, "cancel_rate": False}
RANDOM_INDEX = {1: 0, 2: 0, 3: 0.58, 4: 0.90, 5: 1.12, 6: 1.24, 7: 1.32, 8: 1.41, 9: 1.45}

# Ma trận so sánh cặp mặc định (thang Saaty 1-9) — quan điểm "trải nghiệm khách hàng là ưu tiên"
DEFAULT_PAIRWISE = np.array([
    # gmv  growth  on_time  review  sla   cancel
    [1,    2,      1/2,     1/3,    1,    1],     # gmv
    [1/2,  1,      1/3,     1/4,    1/2,  1/2],   # growth
    [2,    3,      1,       1/2,    2,    2],     # on_time
    [3,    4,      2,       1,      3,    3],     # review
    [1,    2,      1/2,     1/3,    1,    1],     # sla
    [1,    2,      1/2,     1/3,    1,    1],     # cancel
], dtype=float)


def ahp_weights(A: np.ndarray) -> dict:
    """Trọng số AHP bằng phương pháp eigenvector chính + chỉ số nhất quán."""
    vals, vecs = np.linalg.eig(A)
    i = int(np.argmax(vals.real))
    w = np.abs(vecs[:, i].real)
    w = w / w.sum()
    n = A.shape[0]
    lam = float(vals[i].real)
    ci = (lam - n) / (n - 1)
    cr = ci / RANDOM_INDEX[n] if RANDOM_INDEX[n] else 0.0
    return {"weights": w, "lambda_max": lam, "CI": ci, "CR": cr, "consistent": cr < 0.10}


def pairwise_from_upper(upper: dict[tuple[int, int], float], n: int) -> np.ndarray:
    A = np.ones((n, n))
    for (i, j), v in upper.items():
        A[i, j], A[j, i] = v, 1 / v
    return A


def load_seller_metrics(con: duckdb.DuckDBPyConnection, end_month: str = "2018-08",
                        months: int = 6, min_orders: int = 20) -> pd.DataFrame:
    """Tổng hợp KPI 6 tháng gần nhất từ periodic snapshot (đúng cách: cộng tử/mẫu rồi mới chia)."""
    return con.sql(f"""
        WITH s AS (
            SELECT f.*, d.full_date,
                   date_diff('month', d.full_date, last_day(CAST('{end_month}-01' AS DATE))) AS m_ago
            FROM dw.fact_seller_monthly_snapshot f
            JOIN dw.dim_date d ON d.date_key = f.snapshot_date_key
        )
        SELECT ds.seller_id, ds.state AS seller_state, ds.region AS seller_region, ds.first_sale_date,
               sum(orders_cnt)                                              AS orders,
               CAST(sum(gmv) AS DOUBLE)                                     AS gmv,
               CAST((coalesce(sum(gmv) FILTER (WHERE m_ago < 3), 0) + 100)
                  / (coalesce(sum(gmv) FILTER (WHERE m_ago BETWEEN 3 AND 5), 0) + 100) AS DOUBLE) AS growth,
               1 - sum(late_w) / nullif(sum(delivered_w), 0)               AS on_time_rate,
               sum(review_score_sum_w) / nullif(sum(review_w), 0)          AS avg_review,
               sum(sla_breach_items) * 1.0 / nullif(sum(handed_items), 0)  AS sla_breach_rate,
               sum(canceled_items) * 1.0 / nullif(sum(items_cnt), 0)       AS cancel_rate,
               sum(review_w)                                                AS reviews
        FROM s JOIN dw.dim_seller ds ON ds.seller_key = s.seller_key
        WHERE m_ago BETWEEN 0 AND {months - 1}
        GROUP BY ALL
        HAVING sum(orders_cnt) >= {min_orders}
    """).df()


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["growth"] = np.log(d["growth"].clip(0.05, 20))          # log để giảm ảnh hưởng ngoại lai
    d["gmv"] = np.log1p(d["gmv"])
    fill = {"on_time_rate": d["on_time_rate"].median(), "avg_review": d["avg_review"].median(),
            "sla_breach_rate": d["sla_breach_rate"].median(), "cancel_rate": 0.0}
    return d.fillna(fill)


def topsis(X: np.ndarray, w: np.ndarray, benefit: np.ndarray) -> np.ndarray:
    # Chuẩn hóa min-max (các tiêu chí log có thể âm nên không dùng chuẩn hóa vector)
    rng = X.max(0) - X.min(0)
    R = (X - X.min(0)) / np.where(rng == 0, 1, rng)
    V = R * w
    ideal = np.where(benefit, V.max(0), V.min(0))
    anti = np.where(benefit, V.min(0), V.max(0))
    d_pos = np.sqrt(((V - ideal) ** 2).sum(1))
    d_neg = np.sqrt(((V - anti) ** 2).sum(1))
    return d_neg / (d_pos + d_neg)


def score(df: pd.DataFrame, weights: np.ndarray) -> pd.DataFrame:
    d = prepare(df)
    X = d[CRITERIA].to_numpy(float)
    benefit = np.array([BENEFIT[c] for c in CRITERIA])
    out = df.copy()
    out["topsis"] = topsis(X, weights, benefit)
    out["rank"] = out["topsis"].rank(ascending=False, method="min").astype(int)
    return out.sort_values("rank").reset_index(drop=True)


def classify(scored: pd.DataFrame, top_pct: float = 0.15, bottom_pct: float = 0.15) -> pd.DataFrame:
    n = len(scored)
    out = scored.copy()
    top_cut, bot_cut = int(np.ceil(n * top_pct)), int(np.floor(n * (1 - bottom_pct)))
    red_flag = (out["avg_review"] < 3.5) | (out["on_time_rate"] < 0.85) | (out["sla_breach_rate"] > 0.25)

    def action(r):
        if r["rank"] <= top_cut and not red_flag[r.name]:
            return "1. Chiến lược — ưu tiên hiển thị, hỗ trợ mở rộng"
        if r["rank"] > bot_cut or red_flag[r.name]:
            return "4. Can thiệp — cảnh báo, kế hoạch cải thiện 30 ngày"
        if r["rank"] <= n // 2:
            return "2. Duy trì"
        return "3. Theo dõi"
    out["action"] = out.apply(action, axis=1)
    out["red_flag"] = red_flag
    return out


def sensitivity(df: pd.DataFrame, weights: np.ndarray, n_sims: int = 500, concentration: float = 50.0,
                top_k: int = 20, seed: int = 7) -> tuple[pd.DataFrame, pd.Series]:
    """Monte Carlo: w' ~ Dirichlet(concentration * w). Trả về P(top-K) mỗi seller và Spearman rho."""
    rng = np.random.default_rng(seed)
    d = prepare(df)
    X = d[CRITERIA].to_numpy(float)
    benefit = np.array([BENEFIT[c] for c in CRITERIA])
    base = pd.Series(topsis(X, weights, benefit)).rank(ascending=False)
    in_top = np.zeros(len(df))
    rhos = []
    for _ in range(n_sims):
        w = rng.dirichlet(concentration * weights)
        r = pd.Series(topsis(X, w, benefit)).rank(ascending=False)
        in_top += (r <= top_k).to_numpy()
        rhos.append(base.corr(r, method="spearman"))
    res = df[["seller_id"]].copy()
    res["p_top_k"] = in_top / n_sims
    return res, pd.Series(rhos, name="spearman_rho")
