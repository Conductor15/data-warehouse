import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from common import PALETTE, pct, q, setup

setup("Hiệu suất giao hàng (Accumulating snapshot)", "🚚")

k = q("""SELECT count(*) n, avg(CAST(is_late AS INT)) late, avg(lead_time_days) lead, avg(promised_days) prom,
                avg(handling_days) handling, avg(transit_days) transit, avg(approval_hours) appr
         FROM dw.fact_order_fulfillment WHERE is_delivered AND NOT dq_timeline_anomaly""").iloc[0]
c = st.columns(6)
c[0].metric("Đơn đã giao", f"{k.n:,.0f}")
c[1].metric("Tỷ lệ trễ", pct(k.late))
c[2].metric("Thời gian giao TB", f"{k.lead:.1f} ngày")
c[3].metric("Cam kết TB", f"{k.prom:.1f} ngày", delta=f"dư {k.prom - k.lead:.1f} ngày", delta_color="off")
c[4].metric("Người bán xử lý", f"{k.handling:.1f} ngày")
c[5].metric("Vận chuyển", f"{k.transit:.1f} ngày")

m = q("""SELECT year_month, avg(approval_hours) / 24 AS duyet, avg(handling_days) AS xu_ly, avg(transit_days) AS van_chuyen,
                avg(promised_days) AS cam_ket, avg(CAST(is_late AS INT)) AS tre
         FROM mart.v_fulfillment WHERE is_delivered AND NOT dq_timeline_anomaly
           AND year_month BETWEEN '2017-01' AND '2018-08' GROUP BY 1 ORDER BY 1""")
fig = go.Figure()
for col, name, color in (("duyet", "Duyệt đơn", PALETTE[6]), ("xu_ly", "Người bán xử lý", PALETTE[1]),
                         ("van_chuyen", "Vận chuyển", PALETTE[0])):
    fig.add_bar(x=m.year_month, y=m[col], name=name, marker_color=color)
fig.add_scatter(x=m.year_month, y=m.cam_ket, name="Cam kết (ngày)", mode="lines", line=dict(color=PALETTE[2], width=3, dash="dash"))
fig.add_scatter(x=m.year_month, y=m.tre * 100, name="Tỷ lệ trễ (%)", yaxis="y2", mode="lines+markers", line=dict(color=PALETTE[3], width=2))
fig.update_layout(barmode="stack", title="Phân rã chu trình đơn hàng (lag giữa các mốc) & tỷ lệ trễ",
                  yaxis=dict(title="ngày"), yaxis2=dict(title="% trễ", overlaying="y", side="right", showgrid=False),
                  height=430, legend=dict(orientation="h", y=-0.18))
st.plotly_chart(fig, width="stretch")
st.caption("Đỉnh trễ 11/2017 (Black Friday) và 02–03/2018: thời gian vận chuyển tăng vọt trong khi cam kết không đổi → "
           "cam kết tĩnh không phản ứng với tắc nghẽn → động lực cho DSS-1 và DSS-2.")

a, b = st.columns(2)
heat = q("""SELECT seller_region, customer_region, count(*) n, avg(CAST(is_late AS INT)) late, avg(lead_time_days) lead
            FROM mart.v_order_route WHERE is_delivered GROUP BY 1, 2""")
piv = heat.pivot(index="seller_region", columns="customer_region", values="late") * 100
fig = px.imshow(piv, text_auto=".1f", color_continuous_scale="Reds", aspect="auto",
                labels=dict(x="Vùng khách hàng (nơi nhận)", y="Vùng người bán (nơi gửi)", color="% trễ"),
                title="Tỷ lệ trễ theo tuyến vùng → vùng")
fig.update_layout(height=400)
a.plotly_chart(fig, width="stretch")

st_ = q("""SELECT customer_state, count(*) n, avg(CAST(is_late AS INT)) late, avg(lead_time_days) lead, avg(promised_days) prom
           FROM mart.v_fulfillment WHERE is_delivered GROUP BY 1 HAVING count(*) > 100""")
fig = px.scatter(st_, x="lead", y="late", size="n", text="customer_state", color="prom",
                 color_continuous_scale="Viridis", title="Bang khách hàng: thời gian giao vs tỷ lệ trễ",
                 labels={"lead": "Thời gian giao TB (ngày)", "late": "Tỷ lệ trễ", "prom": "Cam kết TB"})
fig.update_traces(textposition="top center")
fig.update_layout(height=400, yaxis_tickformat=".0%")
b.plotly_chart(fig, width="stretch")

rv = q("""SELECT round(delay_days) AS d, count(*) n, avg(review_score) r FROM mart.v_fulfillment
          WHERE is_delivered AND delay_days BETWEEN -30 AND 20 AND review_score IS NOT NULL GROUP BY 1 ORDER BY 1""")
fig = px.scatter(rv, x="d", y="r", size="n", title="Điểm review TB theo số ngày lệch so với cam kết (âm = giao sớm)",
                 labels={"d": "Ngày giao thực tế − ngày cam kết", "r": "Điểm review TB"}, color_discrete_sequence=[PALETTE[0]])
fig.add_vline(x=0, line_dash="dash", line_color=PALETTE[3])
fig.update_layout(height=380)
st.plotly_chart(fig, width="stretch")

st.subheader("Vòng đời cập nhật accumulating snapshot qua các batch ETL")
u = q("""SELECT update_count AS so_lan_cap_nhat, count(*) AS so_don FROM dw.fact_order_fulfillment GROUP BY 1 ORDER BY 1""")
st.dataframe(u, hide_index=True)
