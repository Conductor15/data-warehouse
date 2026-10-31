import time

import plotly.express as px
import streamlit as st

from common import q, setup, sql_in
from olap import load_queries

setup("OLAP Explorer", "🧊")

HIERARCHIES = {
    "Thời gian": ["year_num", "year_quarter", "year_month"],
    "Sản phẩm": ["category_group", "category_name"],
    "Địa lý khách hàng": ["customer_region", "customer_state", "customer_city"],
    "Địa lý người bán": ["seller_region", "seller_state"],
    "Thanh toán (junk dim)": ["primary_payment_type", "installment_band"],
    "Lịch": ["day_name"],
}
MEASURES = {
    "Doanh thu (R$)": "round(sum(price), 0)",
    "Số đơn": "count(DISTINCT order_id)",
    "Số sản phẩm": "sum(quantity)",
    "Phí vận chuyển (R$)": "round(sum(freight_value), 0)",
    "Giá trị đơn TB (R$)": "round(sum(price) / count(DISTINCT order_id), 1)",
    "Khoảng cách TB (km)": "round(avg(distance_km), 0)",
    "Số khách hàng": "count(DISTINCT customer_unique_id)",
}

tab1, tab2 = st.tabs(["🔧 Pivot builder (roll-up / drill-down / slice / dice / pivot)", "📚 Thư viện truy vấn OLAP (Q01–Q14)"])

with tab1:
    c1, c2, c3 = st.columns([2, 2, 2])
    row_h = c1.selectbox("Chiều trên HÀNG", list(HIERARCHIES), index=1)
    levels = HIERARCHIES[row_h]
    depth = c1.select_slider("Mức chi tiết (⬆ roll-up / ⬇ drill-down)", options=list(range(1, len(levels) + 1)),
                             value=1, format_func=lambda i: " › ".join(levels[:i]))
    col_h = c2.selectbox("Chiều trên CỘT (pivot)", ["(không)"] + [h for h in HIERARCHIES if h != row_h], index=1)
    col_level = None
    if col_h != "(không)":
        col_level = c2.selectbox("Mức của chiều cột", HIERARCHIES[col_h])
    measure = c3.selectbox("Số đo (measure)", list(MEASURES))
    totals = c3.checkbox("Thêm dòng tổng (ROLLUP)", value=True)

    st.markdown("**Bộ lọc** — 1 điều kiện = *slice*, nhiều điều kiện = *dice*")
    f1, f2, f3, f4 = st.columns(4)
    fy = f1.multiselect("Năm", [2016, 2017, 2018], default=[2017, 2018])
    fr = f2.multiselect("Vùng KH", ["Sudeste", "Sul", "Nordeste", "Centro-Oeste", "Norte"])
    fg = f3.multiselect("Nhóm ngành", q("SELECT DISTINCT category_group FROM dw.dim_product ORDER BY 1").category_group.tolist())
    fp = f4.multiselect("Thanh toán", ["credit_card", "boleto", "voucher", "debit_card"])

    where = ["1=1"]
    if fy: where.append(f"year_num IN ({', '.join(map(str, fy))})")
    if fr: where.append(f"customer_region IN ({sql_in(fr)})")
    if fg: where.append(f"category_group IN ({sql_in(fg)})")
    if fp: where.append(f"primary_payment_type IN ({sql_in(fp)})")
    row_cols = levels[:depth]
    group = ", ".join(row_cols + ([col_level] if col_level else []))
    gb = f"ROLLUP ({', '.join(row_cols)})" if totals and not col_level else group
    sql = (f"SELECT {group}, {MEASURES[measure]} AS value\nFROM mart.v_sales\n"
           f"WHERE {' AND '.join(where)}\nGROUP BY {gb}\nORDER BY {group}")

    t = time.perf_counter()
    df = q(sql)
    ms = (time.perf_counter() - t) * 1000
    with st.expander("SQL sinh tự động", expanded=False):
        st.code(sql, language="sql")
    st.caption(f"{len(df):,} dòng · {ms:.0f} ms")

    if col_level:
        piv = df.pivot_table(index=row_cols, columns=col_level, values="value", aggfunc="sum")
        st.dataframe(piv.style.format("{:,.0f}").background_gradient(cmap="Blues", axis=None),
                     width="stretch", height=480)
        top_cols = df.groupby(col_level)["value"].sum().nlargest(8).index
        plot = df[df[col_level].isin(top_cols)].copy()
        plot["row"] = plot[row_cols].astype(str).agg(" › ".join, axis=1)
        fig = px.bar(plot, x="row", y="value", color=col_level, barmode="stack",
                     labels={"value": measure, "row": ""}, height=420)
        st.plotly_chart(fig, width="stretch")
    else:
        show = df.copy()
        for c in row_cols:
            show[c] = show[c].fillna("Σ TỔNG").astype(str)
        st.dataframe(show, width="stretch", hide_index=True, height=420)
        plot = df.dropna(subset=row_cols).copy()
        plot["row"] = plot[row_cols].astype(str).agg(" › ".join, axis=1)
        fig = px.bar(plot.head(60), x="row", y="value", labels={"value": measure, "row": ""}, height=380,
                     color_discrete_sequence=["#1f4e79"])
        st.plotly_chart(fig, width="stretch")

with tab2:
    qs = load_queries()
    pick = st.selectbox("Chọn truy vấn", qs, format_func=lambda x: f"{x.qid} · {x.operation} — {x.question}")
    st.markdown(f"**Phép toán OLAP:** `{pick.operation}`  \n**Câu hỏi nghiệp vụ:** {pick.question}")
    st.code(pick.sql, language="sql")
    t = time.perf_counter()
    res = q(pick.sql)
    st.caption(f"{len(res):,} dòng · {(time.perf_counter() - t) * 1000:.0f} ms")
    st.dataframe(res, width="stretch", hide_index=True)
