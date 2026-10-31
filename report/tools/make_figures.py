"""Sinh biểu đồ (PNG) và số liệu (stats.json) cho báo cáo — mọi con số lấy trực tiếp từ kho.

    python report/tools/make_figures.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("LOKY_MAX_CPU_COUNT", "4")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import roc_curve

from dss import delivery_promise as dp
from dss import late_risk as lr
from dss import seller_mcdm as sm
from olap import connect, load_queries

FIG = ROOT / "report" / "figures"
FIG.mkdir(parents=True, exist_ok=True)
C = ["#1f4e79", "#2e86ab", "#f18f01", "#c73e1d", "#3b8b5a", "#7d5ba6", "#8c8c8c"]
plt.rcParams.update({"font.family": ["Segoe UI", "Segoe UI Symbol", "DejaVu Sans"], "font.size": 10, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.grid": True, "grid.alpha": 0.25, "figure.dpi": 160,
                     "pdf.fonttype": 42})
con = connect()
S: dict = {}


def save(fig, name):
    fig.tight_layout()
    fig.savefig(FIG / f"{name}.png", bbox_inches="tight", dpi=300)  # LaTeX dùng PNG 300 dpi
    plt.close(fig)


def df(sql):
    return con.sql(sql).df()


def scalar(sql):
    return con.sql(sql).fetchall()[0][0]


# ------------------------------------------------------------------ số liệu tổng quan
S["rows"] = df("""SELECT schema_name || '.' || table_name AS t, estimated_size AS n
                  FROM duckdb_tables() WHERE schema_name IN ('dw','stg','mart','etl')""").set_index("t")["n"].to_dict()
S["raw_rows"] = {t: int(scalar(f"SELECT count(*) FROM stg.{t}")) for t in
                 ["raw_orders", "raw_order_items", "raw_payments", "raw_reviews", "raw_customers",
                  "raw_products", "raw_sellers", "raw_geolocation"]}
S["kpi"] = df("""SELECT sum(price) gmv, count(DISTINCT order_id) orders, count(DISTINCT customer_key) cust_versions,
                 count(DISTINCT seller_key) sellers, count(DISTINCT product_key) products FROM dw.fact_sales""").iloc[0].to_dict()
S["kpi"]["customers"] = int(scalar("SELECT count(DISTINCT customer_unique_id) FROM dw.dim_customer WHERE customer_key>0"))
S["deliv"] = df("""SELECT count(*) n, avg(CAST(is_late AS INT)) late, avg(lead_time_days) lead, avg(promised_days) prom,
                   avg(handling_days) handling, avg(transit_days) transit, avg(approval_hours) appr, avg(review_score) review
                   FROM mart.v_fulfillment WHERE is_delivered AND NOT dq_timeline_anomaly""").iloc[0].to_dict()
S["batch"] = df("SELECT * FROM etl.batch_log ORDER BY batch_id").assign(
    window_start=lambda d: d.window_start.astype(str), window_end=lambda d: d.window_end.astype(str),
    started_at=lambda d: d.started_at.astype(str), finished_at=lambda d: d.finished_at.astype(str)).to_dict("records")
S["load"] = df("SELECT * FROM etl.load_stats ORDER BY batch_id, table_name").to_dict("records")
S["dq"] = df("SELECT * FROM etl.dq_result WHERE batch_id = (SELECT max(batch_id) FROM etl.dq_result) ORDER BY check_id").to_dict("records")
S["scd"] = df("""SELECT count(*) rows, count(DISTINCT customer_unique_id) uids, count(*) FILTER (WHERE version_no > 1) new_versions,
                 count(DISTINCT customer_unique_id) FILTER (WHERE version_no > 1) changed_uids,
                 count(DISTINCT customer_unique_id) FILTER (WHERE original_state <> current_state) moved_state
                 FROM dw.dim_customer WHERE customer_key > 0""").iloc[0].to_dict()
S["acc"] = df("SELECT update_count, count(*) n FROM dw.fact_order_fulfillment GROUP BY 1 ORDER BY 1").to_dict("records")
S["scd_example"] = df("""SELECT customer_unique_id, version_no, zip_prefix, city, state, original_state, current_state,
                 CAST(effective_from AS VARCHAR) effective_from, CAST(effective_to AS VARCHAR) effective_to, is_current
                 FROM dw.dim_customer WHERE customer_unique_id = (
                   SELECT customer_unique_id FROM dw.dim_customer GROUP BY 1 HAVING count(DISTINCT state) > 1 ORDER BY 1 LIMIT 1)
                 ORDER BY version_no""").to_dict("records")

# ------------------------------------------------------------------ OLAP: chạy cả bộ, đo thời gian
olap = {}
for qq in load_queries():
    r = con.sql(qq.sql).df()   # lần chạy khởi động, không tính giờ
    ts = []
    for _ in range(5):
        t = time.perf_counter(); con.sql(qq.sql).df(); ts.append((time.perf_counter() - t) * 1000)
    olap[qq.qid] = {"op": qq.operation, "question": qq.question, "ms": float(np.median(ts)),
                    "rows": len(r), "head": r.head(12).astype(str).to_dict("records"), "cols": list(r.columns)}
S["olap"] = olap

# Hiệu năng aggregate navigation: cùng câu hỏi trên fact chi tiết vs bảng tổng hợp
base_sql = """SELECT d.year_quarter, g.region, p.category_group, count(DISTINCT f.order_id) o, sum(f.price) r
              FROM dw.fact_sales f JOIN dw.dim_date d ON d.date_key=f.purchase_date_key
              JOIN dw.dim_product p ON p.product_key=f.product_key JOIN dw.dim_geography g ON g.geo_key=f.customer_geo_key
              WHERE d.year_num=2018 GROUP BY ALL"""
agg_sql = """SELECT year_quarter, customer_region, category_group, sum(orders) o, sum(revenue) r
             FROM mart.agg_sales_month_category_state WHERE year_num=2018 GROUP BY ALL"""
def bench(sql, n=15):
    ts = []
    for _ in range(n):
        t = time.perf_counter(); con.sql(sql).fetchall(); ts.append((time.perf_counter() - t) * 1000)
    return float(np.median(ts))
S["bench"] = {"base_ms": bench(base_sql), "agg_ms": bench(agg_sql),
              "agg_rows": int(scalar("SELECT count(*) FROM mart.agg_sales_month_category_state"))}

# ------------------------------------------------------------------ F1 doanh thu theo tháng
m = df("""SELECT year_month, sum(revenue) rev, sum(orders) o FROM mart.agg_sales_month_category_state
          WHERE year_month BETWEEN '2017-01' AND '2018-08' GROUP BY 1 ORDER BY 1""")
fig, ax = plt.subplots(figsize=(8.5, 3.2))
ax.bar(m.year_month, m.rev / 1e3, color=C[0], label="Doanh thu (nghìn R$)")
ax2 = ax.twinx(); ax2.plot(m.year_month, m.o, color=C[2], marker="o", lw=2, label="Số đơn"); ax2.grid(False)
ax.set_xticks(range(len(m))); ax.set_xticklabels(m.year_month, rotation=60, fontsize=8)
ax.set_ylabel("nghìn R$"); ax2.set_ylabel("đơn")
ax.annotate("Black Friday\n11/2017", xy=(10, m.rev[10] / 1e3), xytext=(6.2, 950), arrowprops=dict(arrowstyle="->"), fontsize=9)
fig.legend(loc="upper left", bbox_to_anchor=(0.07, 0.95), fontsize=8)
save(fig, "f1_monthly")

# ------------------------------------------------------------------ F2 trễ vs review
rv = df("""SELECT delay_bucket, count(*) n, avg(review_score) r, avg(CAST(review_score <= 2 AS INT)) neg
           FROM mart.v_fulfillment WHERE is_delivered AND delay_bucket <> 'N/A' GROUP BY 1 ORDER BY 1""")
S["review_bucket"] = rv.to_dict("records")
fig, ax = plt.subplots(figsize=(7.5, 3))
cols = [C[4], C[4], C[2], C[3], C[3]]
b = ax.bar([x[3:] for x in rv.delay_bucket], rv.r, color=cols)
for i, (r_, n_) in enumerate(zip(rv.r, rv.neg)):
    ax.text(i, r_ + 0.08, f"{r_:.2f}★\n{n_:.0%} xấu", ha="center", fontsize=8.5)
ax.set_ylim(0, 5.2); ax.set_ylabel("Điểm review TB"); ax.set_xlabel("Ngày giao thực tế so với ngày cam kết")
save(fig, "f2_review_delay")

# ------------------------------------------------------------------ F3 phân rã lag
qd = pd.DataFrame(S["olap"]["Q13"]["head"]).apply(pd.to_numeric, errors="ignore")
fig, ax = plt.subplots(figsize=(7.5, 3))
x = np.arange(len(qd))
bot = np.zeros(len(qd))
for col, lab, c in (("duyet_gio", "Duyệt (ngày)", C[6]), ("xu_ly_ngay", "Người bán xử lý", C[1]), ("van_chuyen_ngay", "Vận chuyển", C[0])):
    v = qd[col].astype(float) / (24 if col == "duyet_gio" else 1)
    ax.bar(x, v, bottom=bot, color=c, label=lab); bot += v
ax.plot(x, qd.cam_ket_ngay.astype(float), color=C[2], ls="--", marker="D", label="Cam kết")
ax2 = ax.twinx(); ax2.plot(x, qd.tre_pct.astype(float), color=C[3], marker="o", label="% trễ"); ax2.grid(False); ax2.set_ylabel("% trễ")
ax.set_xticks(x); ax.set_xticklabels(qd.year_quarter); ax.set_ylabel("ngày")
fig.legend(loc="upper right", bbox_to_anchor=(0.88, 0.98), fontsize=8, ncol=5)
ax.set_ylim(0, 36)
save(fig, "f3_lag")

# ------------------------------------------------------------------ DSS-1 frontier
orders = df("SELECT * FROM mart.dss_promise_orders").set_index("order_id")
bt = df("SELECT * FROM mart.dss_promise_backtest")
rmap = df("SELECT * FROM mart.dss_review_by_delay").set_index("delay_bucket")["mean_review"]
rows = []
for (pol, a), g in bt.groupby(["policy", "alpha"]):
    t = orders.loc[g.order_id]
    rows.append({"policy": pol, "alpha": a, **dp.evaluate_promises(t, g.set_index("order_id").promise_days.loc[t.index], rmap)})
fr = pd.DataFrame(rows)
ol = dp.evaluate_promises(orders, orders.promised_days, rmap)
S["dss1"] = {"olist": ol, "frontier": fr.to_dict("records"), "n_test": len(orders)}
for tg in (0.911, 0.92, 0.95):
    for pol in ("ml", "route"):
        d = fr[(fr.policy == pol) & (fr.on_time_rate >= tg)].sort_values("alpha")
        if len(d):
            S["dss1"][f"{pol}_at_{tg}"] = d.iloc[0].to_dict()
# so sánh tại cùng số ngày cam kết với Olist (nội suy)
for pol in ("ml", "route"):
    d = fr[fr.policy == pol].sort_values("avg_promise_days")
    S["dss1"][f"{pol}_ontime_at_olist_promise"] = float(np.interp(ol["avg_promise_days"], d.avg_promise_days, d.on_time_rate))
    d2 = fr[fr.policy == pol].sort_values("on_time_rate")
    S["dss1"][f"{pol}_promise_at_olist_ontime"] = float(np.interp(ol["on_time_rate"], d2.on_time_rate, d2.avg_promise_days))
fig, ax = plt.subplots(figsize=(7.2, 3.6))
for pol, lab, c in (("route", "Bảng quantile theo tuyến", C[1]), ("ml", "Hồi quy quantile GBM", C[0])):
    d = fr[fr.policy == pol].sort_values("alpha")
    ax.plot(d.avg_promise_days, d.on_time_rate * 100, marker="o", ms=3.5, color=c, lw=2, label=lab)
    for _, r in d[d.alpha.isin([0.7, 0.85, 0.9, 0.95])].iterrows():
        ax.annotate(f"α={r.alpha:.2f}", (r.avg_promise_days, r.on_time_rate * 100), fontsize=7, xytext=(4, -10), textcoords="offset points", color=c)
ax.scatter([ol["avg_promise_days"]], [ol["on_time_rate"] * 100], marker="*", s=260, color=C[3], zorder=5, label="Olist hiện tại")
ax.set_xlabel("Số ngày cam kết trung bình (thấp hơn = hấp dẫn hơn)"); ax.set_ylabel("% giao đúng hạn")
ax.legend(fontsize=8, loc="lower right")
save(fig, "f4_frontier")

# monthly robustness at alpha giving >= olist on-time
a_sel = S["dss1"]["ml_at_0.92"]["alpha"]
g = bt[(bt.policy == "ml") & (bt.alpha == a_sel)].merge(orders.reset_index(), on="order_id")
g["month"] = pd.to_datetime(g.purchase_ts).dt.strftime("%Y-%m")
mon = g.groupby("month").apply(lambda x: pd.Series({
    "olist_promise": x.promised_days.mean(), "olist_on_time": (x.lead_time_days <= x.promised_days).mean(),
    "new_promise": x.promise_days.mean(), "new_on_time": (x.lead_time_days <= x.promise_days).mean()}), include_groups=False)
S["dss1"]["monthly"] = mon.reset_index().to_dict("records"); S["dss1"]["alpha_sel"] = a_sel
S["dss1"]["review_map"] = rmap.to_dict()

# ------------------------------------------------------------------ DSS-2
met = df("SELECT * FROM mart.dss_late_risk_metrics"); S["dss2_metrics"] = met.to_dict("records")
sc = df("SELECT * FROM mart.dss_late_risk_scores")
imp = df("SELECT * FROM mart.dss_late_risk_importance ORDER BY importance DESC"); S["dss2_imp"] = imp.head(10).to_dict("records")
fig, axs = plt.subplots(1, 3, figsize=(10.5, 3.1))
for name, c in (("Gradient Boosting", C[0]), ("Logistic Regression", C[1])):
    t = sc[(sc.model == name) & (sc.split == "test")]
    fpr, tpr, _ = roc_curve(t.is_late, t.risk)
    auc = met[(met.model == name) & (met.split == "test")].roc_auc.iloc[0]
    axs[0].plot(fpr, tpr, color=c, lw=2, label=f"{name} (AUC={auc:.3f})")
axs[0].plot([0, 1], [0, 1], ls=":", color="#999"); axs[0].set_xlabel("FPR"); axs[0].set_ylabel("TPR"); axs[0].legend(fontsize=7)
axs[0].set_title("ROC trên tập test (03–08/2018)", fontsize=9)
gb_v = sc[(sc.model == "Gradient Boosting") & (sc.split == "valid")]
gb_t = sc[(sc.model == "Gradient Boosting") & (sc.split == "test")]
cv = lr.decision_curve(gb_v); ct = lr.decision_curve(gb_t); t_star = lr.best_threshold(cv)
row = ct[ct.threshold == t_star].iloc[0].to_dict()
S["dss2"] = {"t_star": t_star, **row, "breakeven_precision": 4 / (25 * 0.5)}
axs[1].plot(cv.threshold, cv.net_benefit, color=C[1], lw=2, label="Validation")
axs[1].plot(ct.threshold, ct.net_benefit, color=C[0], lw=2, label="Test")
axs[1].axvline(t_star, color=C[2], ls="--"); axs[1].axhline(0, color="#999")
axs[1].set_xlim(0.08, 0.8); axs[1].set_ylim(-3000, 3500); axs[1].set_xlabel("Ngưỡng rủi ro"); axs[1].set_ylabel("Lợi ích ròng (R$)")
axs[1].legend(fontsize=7); axs[1].set_title(f"Đường cong quyết định, t*={t_star:.2f}", fontsize=9)
i10 = imp.head(8).iloc[::-1]
axs[2].barh(i10.feature, i10.importance, color=C[0]); axs[2].set_title("Permutation importance (ΔAUC)", fontsize=9)
axs[2].tick_params(axis="y", labelsize=8)
save(fig, "f5_dss2")
# lợi ích theo độ nhạy chi phí
sens = []
for cl in (15, 25, 40):
    for ca in (2, 4, 8):
        cvv = lr.decision_curve(gb_v, cl, ca, 0.5); tt = lr.best_threshold(cvv)
        r = lr.decision_curve(gb_t, cl, ca, 0.5, thresholds=[tt]).iloc[0]
        sens.append({"c_late": cl, "c_action": ca, "t": tt, "flag_rate": r.flag_rate, "precision": r.precision, "net": r.net_benefit})
S["dss2_sens"] = sens

# ------------------------------------------------------------------ DSS-3
seller = df("SELECT * FROM mart.dss_seller_metrics")
ahp = sm.ahp_weights(sm.DEFAULT_PAIRWISE)
res = sm.classify(sm.score(seller, ahp["weights"]))
p, rho = sm.sensitivity(seller, ahp["weights"], n_sims=500)
S["dss3"] = {"weights": dict(zip(sm.CRITERIA, ahp["weights"].round(4).tolist())), "lambda": ahp["lambda_max"],
             "CI": ahp["CI"], "CR": ahp["CR"], "n": len(seller), "actions": res.action.value_counts().sort_index().to_dict(),
             "rho_median": float(rho.median()), "rho_p05": float(rho.quantile(0.05)),
             "top": res.merge(p, on="seller_id").head(8)[["rank", "seller_id", "seller_state", "orders", "gmv", "on_time_rate",
                                                        "avg_review", "sla_breach_rate", "topsis", "p_top_k", "action"]].to_dict("records"),
             "action_profile": res.groupby("action")[["orders", "gmv", "on_time_rate", "avg_review", "sla_breach_rate"]].mean().reset_index().to_dict("records"),
             "pairwise": sm.DEFAULT_PAIRWISE.round(3).tolist()}
fig, axs = plt.subplots(1, 2, figsize=(10, 3.3), gridspec_kw={"width_ratios": [1, 1.5]})
w = pd.Series(ahp["weights"], index=[sm.CRITERIA_VN[c] for c in sm.CRITERIA]).sort_values()
axs[0].barh(w.index, w.values, color=[C[3] if "Tỷ lệ trễ" in i or "hủy" in i else C[0] for i in w.index])
for i, v in enumerate(w.values): axs[0].text(v + 0.005, i, f"{v:.1%}", va="center", fontsize=8)
axs[0].set_title(f"Trọng số AHP (CR = {ahp['CR']:.4f})", fontsize=9); axs[0].tick_params(axis="y", labelsize=8)
cmap = {a: c for a, c in zip(sorted(res.action.unique()), [C[4], C[1], C[2], C[3]])}
for a, gg in res.groupby("action"):
    axs[1].scatter(gg.on_time_rate * 100, gg.avg_review, s=np.sqrt(gg.gmv) / 3, alpha=0.6, color=cmap[a], label=a.split("—")[0])
axs[1].set_xlabel("% giao đúng hạn"); axs[1].set_ylabel("Điểm review TB"); axs[1].legend(fontsize=7, loc="lower left")
axs[1].set_title("Danh mục 444 người bán (kích thước ∝ √GMV)", fontsize=9)
save(fig, "f6_dss3")

# ------------------------------------------------------------------ ETL loads
ls = pd.DataFrame(S["load"])
fig, axs = plt.subplots(1, 2, figsize=(10, 3.4))
ins = ls[ls.batch_id > 1].pivot(index="batch_id", columns="table_name", values="rows_inserted")
ins = ins[[c for c in ["dw.fact_sales", "dw.fact_order_fulfillment", "dw.fact_payment", "dw.fact_review", "dw.dim_customer", "dw.dim_product"]]]
ins.columns = [c.replace("dw.", "") for c in ins.columns]
ins.plot(kind="bar", ax=axs[0], color=C[:6], width=0.85, rot=0); axs[0].set_title("INSERT theo batch incremental", fontsize=9)
axs[0].legend(fontsize=7, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.2), frameon=False); axs[0].set_xlabel("batch")
up = ls[ls.batch_id > 1].pivot(index="batch_id", columns="table_name", values="rows_updated")[["dw.fact_order_fulfillment", "dw.dim_customer"]]
b1 = axs[1].bar(up.index, up["dw.fact_order_fulfillment"], color=C[0], label="fact_order_fulfillment (accumulating, trục trái)")
ax3 = axs[1].twinx()
l1, = ax3.plot(up.index, up["dw.dim_customer"], color=C[2], marker="o", label="dim_customer (SCD2, trục phải)")
ax3.grid(False); ax3.spines["right"].set_visible(True)
axs[1].set_title("UPDATE theo batch", fontsize=9); axs[1].set_xlabel("batch"); axs[1].set_xticks(up.index)
axs[1].legend([b1, l1], [b1.get_label(), l1.get_label()], fontsize=7, ncol=1, loc="upper center",
              bbox_to_anchor=(0.5, -0.2), frameon=False)
save(fig, "f7_etl")

(FIG / "stats.json").write_text(json.dumps(S, default=lambda o: o.item() if hasattr(o, "item") else str(o),
                                           ensure_ascii=False, indent=1), encoding="utf-8")
print("Đã tạo hình & stats.json")
