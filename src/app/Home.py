"""Trang chủ dashboard — Tổng quan điều hành.  Chạy:  streamlit run src/app/app.py"""
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from common import PALETTE, brl, pct, q, setup

setup("Olist DW & DSS — Tổng quan điều hành", "📦")
st.markdown('<p class="note">Kho dữ liệu thương mại điện tử Olist (Brazil, 09/2016 – 10/2018) · '
            'Star schema 5 fact / 7 dimension trên DuckDB · Dữ liệu cập nhật qua 11 batch ETL</p>',
            unsafe_allow_html=True)

years = q("SELECT DISTINCT year_num FROM dw.dim_date d JOIN dw.fact_sales f ON f.purchase_date_key = d.date_key ORDER BY 1")
year = st.sidebar.selectbox("Năm", ["Tất cả"] + years["year_num"].astype(str).tolist(), index=0)
where = "" if year == "Tất cả" else f"WHERE d.year_num = {int(year)}"

k = q(f"""
    SELECT sum(f.price) AS gmv, count(DISTINCT f.order_id) AS orders,
           count(DISTINCT c.customer_unique_id) AS customers, count(DISTINCT f.seller_key) AS sellers,
           sum(f.freight_value) AS freight
    FROM dw.fact_sales f JOIN dw.dim_date d ON d.date_key = f.purchase_date_key
    JOIN dw.dim_customer c ON c.customer_key = f.customer_key {where}""").iloc[0]
o = q(f"""
    SELECT avg(CAST(is_late AS INT)) FILTER (WHERE is_delivered) AS late,
           avg(lead_time_days) FILTER (WHERE is_delivered) AS lead,
           avg(promised_days) FILTER (WHERE is_delivered) AS promised,
           avg(review_score) AS review
    FROM mart.v_fulfillment d {where}""").iloc[0]

c = st.columns(7)
c[0].metric("Doanh thu (GMV)", brl(k.gmv))
c[1].metric("Đơn hàng", f"{k.orders:,.0f}")
c[2].metric("Khách hàng", f"{k.customers:,.0f}")
c[3].metric("Người bán", f"{k.sellers:,.0f}")
c[4].metric("Giá trị đơn TB", f"R$ {k.gmv / k.orders:,.0f}")
c[5].metric("Giao đúng hạn", pct(1 - o.late), help="Tỷ lệ đơn đã giao không trễ so với ngày dự kiến")
c[6].metric("Điểm review TB", f"{o.review:.2f} ★")

left, right = st.columns([3, 2])
with left:
    m = q("""SELECT year_month, sum(revenue) AS revenue, sum(orders) AS orders
             FROM mart.agg_sales_month_category_state
             WHERE year_month BETWEEN '2017-01' AND '2018-08' GROUP BY 1 ORDER BY 1""")
    fig = go.Figure()
    fig.add_bar(x=m.year_month, y=m.revenue, name="Doanh thu (R$)", marker_color=PALETTE[0])
    fig.add_scatter(x=m.year_month, y=m.orders, name="Số đơn", yaxis="y2", mode="lines+markers",
                    line=dict(color=PALETTE[2], width=3))
    fig.update_layout(title="Doanh thu & số đơn theo tháng", height=380, legend=dict(orientation="h", y=-0.2),
                      yaxis2=dict(overlaying="y", side="right", showgrid=False), margin=dict(t=50, b=10))
    st.plotly_chart(fig, width="stretch")
with right:
    cg = q(f"""SELECT category_group, sum(revenue) AS revenue FROM mart.agg_sales_month_category_state d
               {where.replace('d.year_num', 'year_num')} GROUP BY 1 ORDER BY 2""")
    fig = px.bar(cg, x="revenue", y="category_group", orientation="h", title="Doanh thu theo nhóm ngành",
                 color_discrete_sequence=[PALETTE[1]], labels={"revenue": "R$", "category_group": ""})
    fig.update_layout(height=380, margin=dict(t=50, b=10))
    st.plotly_chart(fig, width="stretch")

left, right = st.columns(2)
with left:
    st_ = q(f"""SELECT customer_state AS state, sum(revenue) AS revenue, sum(orders) AS orders
                FROM mart.agg_sales_month_category_state d {where.replace('d.year_num', 'year_num')}
                GROUP BY 1 ORDER BY 2 DESC""")
    fig = px.treemap(st_, path=["state"], values="revenue", title="Phân bố doanh thu theo bang khách hàng",
                     color="revenue", color_continuous_scale="Blues")
    fig.update_layout(height=360, margin=dict(t=50, b=10))
    st.plotly_chart(fig, width="stretch")
with right:
    rv = q("""SELECT delay_bucket, count(*) AS orders, avg(review_score) AS review
              FROM mart.v_fulfillment WHERE is_delivered AND delay_bucket <> 'N/A' GROUP BY 1 ORDER BY 1""")
    fig = px.bar(rv, x="delay_bucket", y="review", text=rv.review.round(2),
                 title="Insight: giao trễ làm điểm review sụp đổ", color="review",
                 color_continuous_scale=["#c73e1d", "#f18f01", "#3b8b5a"], range_color=[1.5, 4.5],
                 labels={"delay_bucket": "Mức trễ so với ngày hứa", "review": "Điểm TB"})
    fig.update_layout(height=360, margin=dict(t=50, b=10), coloraxis_showscale=False)
    st.plotly_chart(fig, width="stretch")

st.info("👉 Dùng menu bên trái: **Phân tích bán hàng**, **Hiệu suất giao hàng**, **OLAP Explorer** "
        "(roll-up / drill-down / slice / dice / pivot), 3 mô-đun **DSS**, và trang **ETL & Chất lượng dữ liệu**.")
