import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import PALETTE, pct, q, setup
from dss import delivery_promise as dp

setup("DSS-1 · Tối ưu cam kết ngày giao hàng", "🎯")
st.markdown("""
**Bài toán:** Olist hiển thị ngày giao dự kiến khi khách đặt hàng. Hứa *xa* → mất sức cạnh tranh; hứa *gần* → giao trễ → review sụp đổ.
**Quyết định:** chọn **mức phục vụ α** (xác suất giao đúng hạn mục tiêu) và chính sách sinh cam kết.
**Mô hình:** `cam kết = ⌈Q_α(thời gian giao | đặc trưng)⌉`, huấn luyện cuốn chiếu (walk-forward) mỗi tháng trên 6 tháng trước đó, bỏ 1 tháng đệm (đơn chưa giao xong).
""")

orders = q("SELECT * FROM mart.dss_promise_orders")
bt = q("SELECT * FROM mart.dss_promise_backtest")
rmap = q("SELECT * FROM mart.dss_review_by_delay").set_index("delay_bucket")["mean_review"]

with st.sidebar:
    st.header("Tham số what-if")
    elasticity = st.slider("Độ co giãn nhu cầu theo ngày cam kết (%/ngày) — GIẢ ĐỊNH", 0.0, 5.0, 2.0, 0.5) / 100
    target = st.slider("Mục tiêu tỷ lệ đúng hạn (goal seeking)", 0.80, 0.98, 0.92, 0.01)
    policy = st.radio("Chính sách", ["ml", "route"], format_func=lambda x: {
        "ml": "Hồi quy quantile GBM (cấp đơn)", "route": "Bảng quantile theo tuyến (dễ giải thích)"}[x])


@st.cache_data
def frontier(elasticity: float) -> pd.DataFrame:
    rows = []
    base = orders.set_index("order_id")
    for (pol, a), g in bt.groupby(["policy", "alpha"]):
        t = base.loc[g.order_id]
        r = dp.evaluate_promises(t, g.set_index("order_id").promise_days.loc[t.index], rmap, elasticity)
        rows.append({"policy": pol, "alpha": a, **r})
    olist = dp.evaluate_promises(base, base.promised_days, rmap, elasticity)
    rows.append({"policy": "olist", "alpha": np.nan, **olist})
    return pd.DataFrame(rows)


fr = frontier(elasticity)
ol = fr[fr.policy == "olist"].iloc[0]
cur = fr[fr.policy == policy].sort_values("alpha")
ok = cur[cur.on_time_rate >= target]
choice = ok.iloc[0] if len(ok) else cur.iloc[-1]

c = st.columns(5)
c[0].metric("α tối thiểu đạt mục tiêu", f"{choice.alpha:.3f}")
c[1].metric("Cam kết TB", f"{choice.avg_promise_days:.1f} ngày", f"{choice.avg_promise_days - ol.avg_promise_days:+.1f} so với Olist", delta_color="inverse")
c[2].metric("Tỷ lệ đúng hạn", pct(choice.on_time_rate), f"{100 * (choice.on_time_rate - ol.on_time_rate):+.1f} điểm %")
c[3].metric("Review kỳ vọng", f"{choice.expected_review:.2f} ★", f"{choice.expected_review - ol.expected_review:+.2f}")
c[4].metric("Chỉ số nhu cầu", f"{choice.demand_index:.3f}", f"{100 * (choice.demand_index - 1):+.1f}% so với Olist")

fig = go.Figure()
for pol, name, color in (("route", "Bảng quantile theo tuyến", PALETTE[1]), ("ml", "Hồi quy quantile GBM", PALETTE[0])):
    d = fr[fr.policy == pol].sort_values("alpha")
    fig.add_scatter(x=d.avg_promise_days, y=d.on_time_rate, mode="lines+markers", name=name,
                    line=dict(color=color, width=3), text=[f"α={a:.3f}" for a in d.alpha],
                    hovertemplate="%{text}<br>cam kết %{x:.1f} ngày<br>đúng hạn %{y:.1%}")
fig.add_scatter(x=[ol.avg_promise_days], y=[ol.on_time_rate], mode="markers+text", name="Olist hiện tại",
                marker=dict(size=16, color=PALETTE[3], symbol="star"), text=["Olist"], textposition="top left")
fig.add_scatter(x=[choice.avg_promise_days], y=[choice.on_time_rate], mode="markers", name="Phương án chọn",
                marker=dict(size=16, color=PALETTE[2], symbol="diamond"))
fig.add_hline(y=target, line_dash="dot", line_color=PALETTE[2], annotation_text=f"mục tiêu {target:.0%}")
fig.update_layout(title="Đường biên đánh đổi: số ngày cam kết vs tỷ lệ giao đúng hạn (backtest 03–08/2018)",
                  xaxis_title="Số ngày cam kết TB (thấp = hấp dẫn hơn)", yaxis_title="Tỷ lệ giao đúng hạn",
                  yaxis_tickformat=".0%", height=460, legend=dict(orientation="h", y=-0.2))
st.plotly_chart(fig, width="stretch")
st.caption("Điểm nằm phía TRÊN-TRÁI của ngôi sao Olist = phương án trội (Pareto): hứa ngắn hơn mà vẫn đúng hạn hơn.")

a, b = st.columns(2)
with a:
    st.subheader("Độ bền theo tháng")
    g = bt[(bt.policy == policy) & (bt.alpha == choice.alpha)].merge(orders, on="order_id")
    g["month"] = pd.to_datetime(g.purchase_ts).dt.strftime("%Y-%m")
    mon = g.groupby("month").apply(lambda x: pd.Series({
        "Olist cam kết": x.promised_days.mean(), "Olist đúng hạn": (x.lead_time_days <= x.promised_days).mean(),
        "DSS cam kết": x.promise_days.mean(), "DSS đúng hạn": (x.lead_time_days <= x.promise_days).mean()}),
        include_groups=False).reset_index()
    st.dataframe(mon.style.format({"Olist cam kết": "{:.1f}", "DSS cam kết": "{:.1f}",
                                   "Olist đúng hạn": "{:.1%}", "DSS đúng hạn": "{:.1%}"}),
                 hide_index=True, width="stretch")
with b:
    st.subheader("Độ nhạy theo mục tiêu đúng hạn")
    sens = []
    for tg in [0.85, 0.88, 0.90, 0.92, 0.94, 0.96]:
        ok_ = cur[cur.on_time_rate >= tg]
        best = ok_.iloc[0] if len(ok_) else cur.iloc[-1]
        sens.append({"mục tiêu": tg, "α": best.alpha, "cam kết (ngày)": best.avg_promise_days,
                     "đúng hạn": best.on_time_rate, "review": best.expected_review, "nhu cầu": best.demand_index})
    st.dataframe(pd.DataFrame(sens).style.format({"mục tiêu": "{:.0%}", "α": "{:.3f}", "cam kết (ngày)": "{:.1f}",
                                                   "đúng hạn": "{:.1%}", "review": "{:.2f}", "nhu cầu": "{:.3f}"}),
                 hide_index=True, width="stretch")
    el = []
    for e in [0.0, 0.01, 0.02, 0.03, 0.05]:
        f = frontier(e)
        d = f[(f.policy == policy) & (f.alpha == choice.alpha)]
        el.append(f"ε={e:.0%}: {d.demand_index.iloc[0]:.3f}")
    st.caption("Mỗi +2 điểm % đúng hạn phải trả bằng ~1–2 ngày cam kết. Chỉ số nhu cầu của phương án đang chọn "
               "theo giả định co giãn ε: " + " · ".join(el) + " (ε chỉ đổi độ lớn, không đổi chiều của kết luận).")

st.subheader("Bảng cam kết khuyến nghị theo tuyến (chính sách giải thích được) — áp dụng cho tháng 09/2018")
src = q("""SELECT r.order_id, r.purchase_ts, r.route, r.seller_region || ' → ' || r.customer_region AS region_route,
                  r.lead_time_days, r.promised_days, r.is_late
           FROM mart.v_order_route r WHERE r.is_delivered AND r.lead_time_days IS NOT NULL
             AND r.purchase_ts >= '2018-02-01' AND r.purchase_ts < '2018-08-01'""")
pol = dp.fit_policy(src, float(choice.alpha))
tbl = pol.table[pol.table.level == "route"].merge(
    src.groupby("route").agg(olist_promise=("promised_days", "mean"), p50=("lead_time_days", "median")),
    left_on="key", right_index=True).sort_values("n_train", ascending=False)
tbl.columns = ["Tuyến", "Cấp", "Số đơn huấn luyện", "Cam kết đề xuất (ngày)", "Olist cam kết TB", "Thời gian giao trung vị"]
st.dataframe(tbl.head(40).style.format({"Olist cam kết TB": "{:.1f}", "Thời gian giao trung vị": "{:.1f}"}),
             hide_index=True, width="stretch")
