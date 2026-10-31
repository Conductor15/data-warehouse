"""Điểm vào dashboard.   Chạy:  streamlit run src/app/app.py"""
import streamlit as st

nav = st.navigation({
    "Phân tích (OLAP)": [
        st.Page("Home.py", title="Tổng quan điều hành", icon="📦", default=True),
        st.Page("pages/1_Phan_tich_ban_hang.py", title="Phân tích bán hàng", icon="🛒", url_path="sales"),
        st.Page("pages/2_Hieu_suat_giao_hang.py", title="Hiệu suất giao hàng", icon="🚚", url_path="delivery"),
        st.Page("pages/3_OLAP_Explorer.py", title="OLAP Explorer", icon="🧊", url_path="olap"),
    ],
    "Hỗ trợ quyết định (DSS)": [
        st.Page("pages/4_DSS_Cam_ket_giao_hang.py", title="DSS-1 · Cam kết ngày giao", icon="🎯", url_path="dss1"),
        st.Page("pages/5_DSS_Canh_bao_tre.py", title="DSS-2 · Cảnh báo giao trễ", icon="🚨", url_path="dss2"),
        st.Page("pages/6_DSS_Danh_gia_nguoi_ban.py", title="DSS-3 · Đánh giá người bán", icon="🏪", url_path="dss3"),
    ],
    "Vận hành": [
        st.Page("pages/7_ETL_va_Chat_luong_du_lieu.py", title="ETL & Chất lượng dữ liệu", icon="🛠️", url_path="etl"),
    ],
})
nav.run()
