import plotly.express as px
import streamlit as st

from common import PALETTE, brl, q, setup, sql_in

setup("Phân tích bán hàng", "🛒")

dims = q("""SELECT DISTINCT year_num, customer_region, category_group FROM mart.agg_sales_month_category_state""")
f1, f2, f3 = st.columns(3)
years = f1.multiselect("Năm (slice)", sorted(dims.year_num.unique()), default=[2017, 2018])
regions = f2.multiselect("Vùng khách hàng (dice)", sorted(dims.customer_region.unique()))
groups = f3.multiselect("Nhóm ngành (dice)", sorted(dims.category_group.unique()))

cond = ["1=1"]
if years:
    cond.append(f"year_num IN ({', '.join(map(str, years))})")
if regions:
    cond.append(f"customer_region IN ({sql_in(regions)})")
if groups:
    cond.append(f"category_group IN ({sql_in(groups)})")
w = " AND ".join(cond)

k = q(f"SELECT sum(revenue) r, sum(orders) o, sum(freight) f FROM mart.agg_sales_month_category_state WHERE {w}").iloc[0]
c = st.columns(4)
c[0].metric("Doanh thu", brl(k.r or 0))
c[1].metric("Số đơn (xấp xỉ)", f"{(k.o or 0):,.0f}", help="Cộng từ aggregate theo tháng×danh mục×bang — đơn nhiều danh mục được đếm nhiều lần")
c[2].metric("Phí vận chuyển", brl(k.f or 0))
c[3].metric("Phí VC / doanh thu", f"{100 * (k.f or 0) / max(k.r or 1, 1):.1f}%")

a, b = st.columns(2)
m = q(f"""SELECT year_month, category_group, sum(revenue) revenue FROM mart.agg_sales_month_category_state
          WHERE {w} GROUP BY 1, 2 ORDER BY 1""")
fig = px.area(m, x="year_month", y="revenue", color="category_group", title="Doanh thu theo tháng & nhóm ngành",
              color_discrete_sequence=px.colors.qualitative.Safe, labels={"revenue": "R$", "year_month": ""})
fig.update_layout(height=420, legend=dict(font=dict(size=10)))
a.plotly_chart(fig, width="stretch")

top = q(f"""SELECT category_name, sum(revenue) revenue, sum(orders) orders FROM mart.agg_sales_month_category_state
            WHERE {w} GROUP BY 1 ORDER BY 2 DESC LIMIT 15""")
fig = px.bar(top.iloc[::-1], x="revenue", y="category_name", orientation="h", title="Top 15 danh mục",
             color_discrete_sequence=[PALETTE[0]], labels={"revenue": "R$", "category_name": ""})
fig.update_layout(height=420)
b.plotly_chart(fig, width="stretch")

a, b = st.columns(2)
dow = q(f"""SELECT d.day_of_week, d.day_name, extract('hour' FROM f.purchase_ts) AS hr, count(DISTINCT f.order_id) AS orders
            FROM dw.fact_sales f JOIN dw.dim_date d ON d.date_key = f.purchase_date_key
            WHERE d.year_num IN ({', '.join(map(str, years or [2017, 2018]))})
            GROUP BY ALL ORDER BY 1, 3""")
fig = px.density_heatmap(dow, x="hr", y="day_name", z="orders", histfunc="sum", title="Mật độ đặt hàng: thứ × giờ",
                         category_orders={"day_name": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]},
                         color_continuous_scale="Blues", labels={"hr": "Giờ", "day_name": ""})
fig.update_layout(height=380)
a.plotly_chart(fig, width="stretch")

pay = q(f"""SELECT pt.payment_type_label AS loai, sum(p.payment_value) AS gia_tri, avg(p.installments) AS tra_gop
            FROM dw.fact_payment p JOIN dw.dim_payment_type pt USING (payment_type_key)
            JOIN dw.dim_date d ON d.date_key = p.purchase_date_key
            WHERE d.year_num IN ({', '.join(map(str, years or [2017, 2018]))}) GROUP BY 1""")
fig = px.pie(pay, names="loai", values="gia_tri", hole=0.5, title="Cơ cấu giá trị thanh toán",
             color_discrete_sequence=PALETTE)
fig.update_layout(height=380)
b.plotly_chart(fig, width="stretch")

st.subheader("Khách hàng quay lại (cohort)")
coh = q("""
    WITH firsts AS (
        SELECT c.customer_unique_id, min(f.purchase_ts) AS first_ts
        FROM dw.fact_order_fulfillment f JOIN dw.dim_customer c USING (customer_key) GROUP BY 1),
    orders AS (
        SELECT c.customer_unique_id, f.purchase_ts FROM dw.fact_order_fulfillment f JOIN dw.dim_customer c USING (customer_key))
    SELECT strftime(fi.first_ts, '%Y-%m') AS cohort,
           date_diff('month', date_trunc('month', fi.first_ts), date_trunc('month', o.purchase_ts)) AS m,
           count(DISTINCT o.customer_unique_id) AS customers
    FROM firsts fi JOIN orders o USING (customer_unique_id)
    WHERE fi.first_ts >= '2017-01-01' AND fi.first_ts < '2018-01-01'
    GROUP BY 1, 2 ORDER BY 1, 2""")
size = coh[coh.m == 0].set_index("cohort")["customers"]
coh["retention"] = coh.apply(lambda r: r.customers / size[r.cohort], axis=1)
piv = coh[(coh.m > 0) & (coh.m <= 12)].pivot(index="cohort", columns="m", values="retention")
fig = px.imshow(piv * 100, text_auto=".1f", aspect="auto", color_continuous_scale="Oranges",
                labels=dict(x="Tháng sau lần mua đầu", y="Cohort", color="% quay lại"))
fig.update_layout(height=420)
st.plotly_chart(fig, width="stretch")
st.caption("Tỷ lệ quay lại < 1%/tháng: Olist là marketplace thu hút khách mới; trải nghiệm giao hàng là đòn bẩy giữ chân → động lực cho các mô-đun DSS.")
