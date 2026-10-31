"""Thành phần Hệ hỗ trợ quyết định (DSS) xây trên kho dữ liệu Olist.

Ba mô hình (model base) dùng chung data base là kho DuckDB:
    delivery_promise : mô hình tối ưu cam kết ngày giao (what-if, goal seeking, sensitivity)
    late_risk        : mô hình dự báo rủi ro giao trễ + chọn ngưỡng theo chi phí
    seller_mcdm      : chấm điểm & xếp hạng người bán bằng AHP + TOPSIS
"""
