"""Chạy các mô hình DSS ở chế độ batch và GHI KẾT QUẢ NGƯỢC VÀO KHO (schema mart.dss_*).

    python src/dss/build_dss.py

Dashboard đọc các bảng này để phản hồi tức thì; các phần what-if rẻ (AHP/TOPSIS,
ngưỡng chi phí, độ co giãn nhu cầu) được tính lại trực tiếp trên giao diện.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("LOKY_MAX_CPU_COUNT", "4")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from dss import delivery_promise as dp
from dss import late_risk as lr
from dss import seller_mcdm as sm
from olap import connect

TEST_MONTHS = ["2018-03", "2018-04", "2018-05", "2018-06", "2018-07", "2018-08"]
ALPHAS = [round(a, 3) for a in np.arange(0.60, 0.99, 0.025)]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    con = connect(read_only=False)

    # ---------------- DSS-1: cam kết ngày giao ----------------
    t = time.perf_counter()
    orders = dp.load_orders(con)
    month = orders["purchase_ts"].dt.strftime("%Y-%m")
    test = orders[month.isin(TEST_MONTHS)].reset_index(drop=True)
    rows = []
    for kind in ("route", "ml"):
        for a in ALPHAS:
            wf = dp.walk_forward(orders, a, kind, TEST_MONTHS)
            wf["policy"], wf["alpha"] = kind, a
            rows.append(wf)
        log(f"DSS-1 walk-forward '{kind}' xong ({len(ALPHAS)} mức alpha)")
    bt = pd.concat(rows, ignore_index=True)
    con.execute("CREATE OR REPLACE TABLE mart.dss_promise_backtest AS SELECT * FROM bt")
    con.execute("""CREATE OR REPLACE TABLE mart.dss_promise_orders AS
                   SELECT * FROM test""")
    train_review = orders[orders["purchase_ts"] < "2018-03-01"]
    rmap = dp.review_by_bucket(train_review).rename("mean_review").reset_index()
    rmap.columns = ["delay_bucket", "mean_review"]
    rmap["delay_bucket"] = rmap["delay_bucket"].astype(str)
    con.execute("CREATE OR REPLACE TABLE mart.dss_review_by_delay AS SELECT * FROM rmap")
    log(f"DSS-1 xong trong {time.perf_counter() - t:.0f}s — {len(bt):,} dòng backtest")

    # ---------------- DSS-2: rủi ro giao trễ ----------------
    t = time.perf_counter()
    feats = lr.load_features(con)
    metrics, scored, models = lr.fit_and_score(feats)
    imp = lr.feature_importance(models["Gradient Boosting"], feats)
    scored = scored.merge(feats[["order_id", "customer_state", "seller_state", "category_group",
                                 "slack_days", "carrier_ts"]], on="order_id", how="left")
    for c in ("customer_state", "seller_state", "category_group"):
        scored[c] = scored[c].astype(str)
    con.execute("CREATE OR REPLACE TABLE mart.dss_late_risk_metrics AS SELECT * FROM metrics")
    con.execute("CREATE OR REPLACE TABLE mart.dss_late_risk_scores AS SELECT * FROM scored")
    con.execute("CREATE OR REPLACE TABLE mart.dss_late_risk_importance AS SELECT * FROM imp")
    log(f"DSS-2 xong trong {time.perf_counter() - t:.0f}s\n{metrics.round(3).to_string()}")

    # ---------------- DSS-3: danh mục người bán ----------------
    seller = sm.load_seller_metrics(con)
    con.execute("CREATE OR REPLACE TABLE mart.dss_seller_metrics AS SELECT * FROM seller")
    log(f"DSS-3: {len(seller)} người bán đủ điều kiện xếp hạng")

    con.execute("CHECKPOINT")
    con.close()
    log("HOÀN TẤT build DSS.")


if __name__ == "__main__":
    main()
