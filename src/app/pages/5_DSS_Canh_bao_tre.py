import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from common import PALETTE, q, setup
from dss import late_risk as lr

setup("DSS-2 · Cảnh báo sớm đơn có nguy cơ giao trễ", "🚨")
st.markdown("""
**Thời điểm quyết định:** khi người bán bàn giao đơn cho hãng vận chuyển. **Hành động:** nâng cấp dịch vụ vận chuyển,
thông báo chủ động cho khách, nhắc người bán. **Mô hình:** Gradient Boosting (so sánh Logistic Regression) trên đặc trưng
*point-in-time* từ kho (lịch sử người bán ở periodic snapshot tháng trước, tắc nghẽn 14 ngày gần nhất của bang nhận).
""")

metrics = q("SELECT * FROM mart.dss_late_risk_metrics")
st.dataframe(metrics.style.format({"late_rate": "{:.1%}", "roc_auc": "{:.3f}", "pr_auc": "{:.3f}", "brier": "{:.4f}",
                                   "lift_top10": "{:.2f}x", "recall_top10": "{:.1%}"}), hide_index=True, width="stretch")

with st.sidebar:
    st.header("Tham số chi phí (what-if)")
    model = st.selectbox("Mô hình", ["Gradient Boosting", "Logistic Regression"])
    c_late = st.number_input("Chi phí 1 đơn trễ (R$) — bồi thường, mất khách", 5.0, 200.0, 25.0, 5.0)
    c_act = st.number_input("Chi phí 1 lần can thiệp (R$)", 0.5, 50.0, 4.0, 0.5)
    eff = st.slider("Hiệu quả can thiệp (% đơn trễ được cứu)", 0.1, 1.0, 0.5, 0.05)
    st.caption(f"Ngưỡng hòa vốn về precision = C_action / (C_late × hiệu quả) = **{c_act / (c_late * eff):.1%}**")

scores = q("SELECT * FROM mart.dss_late_risk_scores WHERE model = ?", (model,))
valid, test = scores[scores.split == "valid"], scores[scores.split == "test"]
cv = lr.decision_curve(valid, c_late, c_act, eff)
t_star = lr.best_threshold(cv)
ct = lr.decision_curve(test, c_late, c_act, eff)
row = ct[ct.threshold == t_star].iloc[0]
no_action = t_star == lr.NO_ACTION
if no_action:
    st.warning("Với bộ chi phí này, không ngưỡng nào có lợi ích ròng dương → khuyến nghị **không can thiệp**.")

c = st.columns(5)
c[0].metric("Ngưỡng tối ưu t* (chọn trên validation)", "Không can thiệp" if no_action else f"{t_star:.2f}")
c[1].metric("Tỷ lệ đơn bị gắn cờ (test)", f"{row.flag_rate:.1%}")
c[2].metric("Precision", f"{row.precision:.1%}")
c[3].metric("Recall", f"{row.recall:.1%}")
c[4].metric("Lợi ích ròng 6 tháng (test)", f"R$ {row.net_benefit:,.0f}")

a, b = st.columns(2)
fig = go.Figure()
fig.add_scatter(x=cv.threshold, y=cv.net_benefit, name="Validation (01–02/2018)", line=dict(color=PALETTE[1], width=3))
fig.add_scatter(x=ct.threshold, y=ct.net_benefit, name="Test (03–08/2018)", line=dict(color=PALETTE[0], width=3))
if not no_action:
    fig.add_vline(x=t_star, line_dash="dash", line_color=PALETTE[2], annotation_text="t*")
fig.add_hline(y=0, line_color="#999")
lo = min(cv[cv.threshold >= 0.08].net_benefit.min(), ct[ct.threshold >= 0.08].net_benefit.min())
fig.update_xaxes(range=[0.08, 0.8])
fig.update_yaxes(range=[lo * 1.1, max(cv.net_benefit.max(), ct.net_benefit.max()) * 1.4])
fig.update_layout(title="Đường cong quyết định: lợi ích ròng (R$) theo ngưỡng", xaxis_title="Ngưỡng rủi ro",
                  yaxis_title="R$", height=400, legend=dict(orientation="h", y=-0.2))
a.plotly_chart(fig, width="stretch")

imp = q("SELECT * FROM mart.dss_late_risk_importance ORDER BY importance DESC LIMIT 12")
fig = px.bar(imp.iloc[::-1], x="importance", y="feature", orientation="h", color_discrete_sequence=[PALETTE[0]],
             title="Mức quan trọng đặc trưng (permutation, giảm ROC-AUC)")
fig.update_layout(height=400)
b.plotly_chart(fig, width="stretch")

st.subheader("Danh sách cảnh báo (watch-list) theo ngày bàn giao")
flagged = test[test.risk >= t_star]
busiest = flagged.carrier_ts.dt.date.value_counts().idxmax() if len(flagged) else test.carrier_ts.max().date()
day = st.date_input("Ngày bàn giao cho hãng vận chuyển (mặc định: ngày có nhiều cảnh báo nhất)", value=busiest,
                    min_value=test.carrier_ts.min().date(), max_value=test.carrier_ts.max().date())
wl = test[(test.carrier_ts.dt.date == day) & (test.risk >= t_star)].sort_values("risk", ascending=False)
st.caption(f"{len(wl)} đơn vượt ngưỡng trong ngày {day} — cột *is_late* là kết quả thực tế (để kiểm chứng).")
st.dataframe(wl[["order_id", "risk", "slack_days", "seller_state", "customer_state", "category_group", "is_late"]]
             .style.format({"risk": "{:.1%}", "slack_days": "{:.1f}"}).background_gradient(subset=["risk"], cmap="Reds"),
             hide_index=True, width="stretch")
