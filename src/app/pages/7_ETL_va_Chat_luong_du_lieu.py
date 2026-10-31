import plotly.express as px
import streamlit as st

from common import PALETTE, q, setup

setup("ETL & Chất lượng dữ liệu", "🛠️")

st.subheader("Nhật ký batch ETL")
log = q("""SELECT batch_id, batch_type, CAST(window_start AS DATE) AS tu, CAST(window_end AS DATE) AS den,
                  round(duration_sec, 1) AS giay, status, message FROM etl.batch_log ORDER BY batch_id""")
st.dataframe(log, hide_index=True, width="stretch")

st.subheader("Số dòng thêm mới / cập nhật theo bảng và batch")
ls = q("SELECT batch_id, replace(table_name, 'dw.', '') AS bang, rows_inserted, rows_updated FROM etl.load_stats")
a, b = st.columns(2)
fig = px.bar(ls[ls.batch_id > 1], x="batch_id", y="rows_inserted", color="bang", title="Dòng INSERT ở các batch incremental",
             color_discrete_sequence=px.colors.qualitative.Safe)
fig.update_layout(height=380)
a.plotly_chart(fig, width="stretch")
fig = px.bar(ls[ls.rows_updated > 0], x="batch_id", y="rows_updated", color="bang",
             title="Dòng UPDATE (accumulating snapshot & SCD2)", color_discrete_sequence=[PALETTE[0], PALETTE[2]])
fig.update_layout(height=380)
b.plotly_chart(fig, width="stretch")

st.subheader("Kết quả kiểm tra chất lượng dữ liệu (batch cuối)")
dq = q("""SELECT check_id, category, description, affected_rows, severity, status FROM etl.dq_result
          WHERE batch_id = (SELECT max(batch_id) FROM etl.dq_result) ORDER BY category, check_id""")
color = {"PASS": "background-color:#e3f4e8", "FIXED": "background-color:#e6eef8",
         "FLAGGED": "background-color:#fff4dc", "FAIL": "background-color:#fbe0dc"}
st.dataframe(dq.style.map(lambda v: color.get(v, ""), subset=["status"]), hide_index=True, width="stretch", height=560)

st.subheader("Kích thước kho dữ liệu")
sizes = q("""SELECT schema_name || '.' || table_name AS bang, estimated_size AS so_dong, column_count AS so_cot
             FROM duckdb_tables() WHERE schema_name IN ('dw', 'mart', 'etl') ORDER BY schema_name, table_name""")
st.dataframe(sizes, hide_index=True, width="stretch")

st.subheader("Minh họa SCD Type 2/6 — khách hàng đã đổi địa chỉ")
ex = q("""SELECT customer_unique_id, version_no, zip_prefix, city, state, original_state, current_state,
                 effective_from, effective_to, is_current
          FROM dw.dim_customer WHERE customer_unique_id IN (
              SELECT customer_unique_id FROM dw.dim_customer GROUP BY 1 HAVING count(DISTINCT state) > 1 LIMIT 4)
          ORDER BY customer_unique_id, version_no""")
st.dataframe(ex, hide_index=True, width="stretch")
