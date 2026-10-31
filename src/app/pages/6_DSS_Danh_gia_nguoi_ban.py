import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from common import PALETTE, q, setup
from dss import seller_mcdm as sm

setup("DSS-3 · Đánh giá & phân loại người bán (AHP + TOPSIS)", "🏪")
st.markdown("""
**Quyết định:** người bán nào được **ưu tiên hiển thị / hỗ trợ mở rộng**, ai cần **can thiệp**?
**Phương pháp:** nhà quản lý so sánh cặp các tiêu chí (AHP, thang Saaty 1–9) → trọng số; TOPSIS xếp hạng
444 người bán có ≥ 20 đơn trong 6 tháng (03–08/2018) từ *periodic snapshot*; Monte Carlo kiểm tra độ vững.
""")

df = q("SELECT * FROM mart.dss_seller_metrics")
names = [sm.CRITERIA_VN[c] for c in sm.CRITERIA]
SAATY = [1 / k for k in range(9, 1, -1)] + list(range(1, 10))  # thang Saaty đầy đủ 1/9 .. 9
fmt = lambda v: f"1/{round(1 / v)}" if v < 1 else f"{int(v)}"

st.subheader("① So sánh cặp tiêu chí (AHP)")
upper = {}
cols = st.columns(3)
k = 0
for i in range(len(sm.CRITERIA)):
    for j in range(i + 1, len(sm.CRITERIA)):
        default = sm.DEFAULT_PAIRWISE[i, j]
        default = min(SAATY, key=lambda s: abs(np.log(s) - np.log(default)))
        upper[(i, j)] = cols[k % 3].select_slider(f"{names[i]}  vs  {names[j]}", options=SAATY, value=default,
                                                  format_func=fmt, key=f"p{i}{j}")
        k += 1
A = sm.pairwise_from_upper(upper, len(sm.CRITERIA))
ahp = sm.ahp_weights(A)

a, b = st.columns([2, 1])
wdf = pd.DataFrame({"Tiêu chí": names, "Trọng số": ahp["weights"],
                    "Loại": ["Lợi ích" if sm.BENEFIT[c] else "Chi phí" for c in sm.CRITERIA]})
fig = px.bar(wdf, x="Trọng số", y="Tiêu chí", orientation="h", color="Loại", text=wdf["Trọng số"].map("{:.1%}".format),
             color_discrete_map={"Lợi ích": PALETTE[0], "Chi phí": PALETTE[3]}, title="Trọng số AHP (eigenvector chính)")
fig.update_layout(height=320)
a.plotly_chart(fig, width="stretch")
b.metric("λ_max", f"{ahp['lambda_max']:.3f}")
b.metric("CI", f"{ahp['CI']:.4f}")
b.metric("CR (cần < 0.10)", f"{ahp['CR']:.4f}", "Nhất quán ✓" if ahp["consistent"] else "KHÔNG nhất quán ✗",
         delta_color="normal" if ahp["consistent"] else "inverse")
if not ahp["consistent"]:
    st.error("Ma trận so sánh cặp không nhất quán (CR ≥ 0.10). Hãy điều chỉnh lại các phán đoán trước khi dùng kết quả.")

st.subheader("② Xếp hạng TOPSIS & khuyến nghị hành động")
res = sm.classify(sm.score(df, ahp["weights"]))
cnt = res.action.value_counts().sort_index()
cc = st.columns(len(cnt))
for i, (k_, v) in enumerate(cnt.items()):
    cc[i].metric(k_.split("—")[0], f"{v} người bán")

fig = px.scatter(res, x="on_time_rate", y="avg_review", size="gmv", color="action", hover_data=["seller_id", "rank", "orders"],
                 color_discrete_sequence=[PALETTE[4], PALETTE[1], PALETTE[2], PALETTE[3]],
                 category_orders={"action": sorted(res.action.unique())},
                 labels={"on_time_rate": "Tỷ lệ giao đúng hạn", "avg_review": "Điểm review TB"},
                 title="Bản đồ danh mục người bán (kích thước = GMV)")
fig.update_layout(height=470, xaxis_tickformat=".0%", legend=dict(orientation="h", y=-0.2))
st.plotly_chart(fig, width="stretch")

show = res[["rank", "seller_id", "seller_state", "orders", "gmv", "growth", "on_time_rate", "avg_review",
            "sla_breach_rate", "cancel_rate", "topsis", "action"]]
st.dataframe(show.style.format({"gmv": "R$ {:,.0f}", "growth": "{:.2f}x", "on_time_rate": "{:.1%}", "avg_review": "{:.2f}",
                                "sla_breach_rate": "{:.1%}", "cancel_rate": "{:.1%}", "topsis": "{:.3f}"}),
             hide_index=True, width="stretch", height=380)

st.subheader("③ Phân tích độ nhạy (Monte Carlo trên trọng số)")
s1, s2 = st.columns(2)
conc = s1.slider("Độ tập trung Dirichlet (nhỏ = nhiễu mạnh)", 10, 200, 50, 10)
topk = s2.slider("Top-K", 10, 50, 20, 5)
p, rho = sm.sensitivity(df, ahp["weights"], n_sims=300, concentration=conc, top_k=topk)
x = res.merge(p, on="seller_id").sort_values("rank").head(topk + 10)
a, b = st.columns([2, 1])
fig = px.bar(x, x="seller_id", y="p_top_k", color="p_top_k", color_continuous_scale="Blues",
             title=f"Xác suất giữ vị trí Top-{topk} khi trọng số dao động", labels={"p_top_k": "P(Top-K)", "seller_id": ""})
fig.update_layout(height=360, xaxis=dict(showticklabels=False))
a.plotly_chart(fig, width="stretch")
b.metric("Spearman ρ trung vị (thứ hạng gốc vs nhiễu)", f"{rho.median():.3f}")
b.metric("Spearman ρ phân vị 5%", f"{rho.quantile(0.05):.3f}")
b.caption("ρ gần 1 → khuyến nghị vững trước sai số trong phán đoán của nhà quản lý.")
