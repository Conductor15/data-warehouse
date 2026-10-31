# Kịch bản thuyết trình (15 phút) và chuẩn bị Q&A

Chia 3 người, mỗi người khoảng 5 phút. Phần demo nên **quay trước** để không phụ thuộc mạng/máy chiếu. Nếu demo trực tiếp thì mở sẵn dashboard.

Số slide dưới đây là số in ở góc trên bên phải mỗi slide (trang bìa không đánh số).

## SV 1 — Bài toán & thiết kế DW (bìa + slide 01–08, ~5 phút)

| Slide | Ý chính cần nói (đừng đọc slide) |
|---|---|
| Bìa | Giới thiệu nhóm, đề tài. |
| 01 | Olist hứa trung bình 23,7 ngày nhưng thực tế giao 12,6 ngày. Dù vậy, khi trễ hơn 7 ngày thì điểm review rơi xuống 1,73★. Từ đó có 3 quyết định cần hỗ trợ. |
| 02 | Profiling bằng SQL trước khi thiết kế. Nhấn mạnh **zip mất số 0** nên landing phải lưu dạng VARCHAR. Các phát hiện về dữ liệu dẫn thẳng tới thiết kế: 250 khách đổi địa chỉ → SCD2; đơn trả nhiều phương thức → tách fact_payment. |
| 03 | Kiến trúc nhiều tầng: staging 2 tầng, star schema, semantic, DSS ghi ngược kết quả vào kho. Lý do chọn DuckDB: lưu trữ dạng cột, chạy được bằng 1 lệnh. |
| 04 | 4 bước Kimball. **Grain** là quyết định quan trọng nhất. Lý do tách fact_sales (dòng sản phẩm) và fulfillment (đơn). |
| 05–06 | Chỉ vào sơ đồ: role-playing date ×5, geography ×2, junk profile, bridge có trọng số. |
| 07 | Mỗi kỹ thuật đều có căn cứ từ dữ liệu. Nêu luôn lý do **không** dùng mini-dimension (96,9% khách chỉ mua 1 lần). |
| 08 | ETL mô phỏng hệ nguồn "as-of": accumulating snapshot được UPDATE 21.392 lần qua 10 batch. Thuật toán SCD2 dùng LAG. Mỗi batch là 1 transaction, lỗi thì rollback. |

## SV 2 — Chất lượng, OLAP, dashboard (slide 09–12, ~5 phút)

| Slide | Ý chính |
|---|---|
| 09 | 26 kiểm tra/batch, đối soát doanh thu tới từng cent, 24 test. Test đáng nói nhất: mỗi dòng fact trỏ đúng phiên bản khách tại thời điểm mua. |
| 10 | 14 truy vấn, mỗi truy vấn gắn với 1 phép OLAP và 1 câu hỏi nghiệp vụ. Ví dụ semi-additive: cộng tồn đơn của 3 tháng ra 14.170, con số này sai. Aggregate table nhanh hơn 8,3×. |
| 11 | **DEMO**: OLAP Explorer chọn Sản phẩm × Thời gian, kéo thanh trượt để drill-down, lọc Sudeste (slice), mở "SQL sinh tự động". |
| 12 | Insight (pha Intelligence): cam kết gần như cố định nên khi có sốc thì tỷ lệ trễ tăng lên 12,9%. Trễ thì đánh giá kém. Chuyển tiếp sang DSS. |

## SV 3 — DSS & kết luận (slide 13–17 + trang kết, ~5 phút)

| Slide | Ý chính |
|---|---|
| 13 | Kiến trúc Turban (dữ liệu = DW, model base, UI, tri thức) và bảng ánh xạ các pha của Simon. |
| 14 | DSS-1: biến quyết định α. Walk-forward bỏ 1 tháng đệm (vì có right-censoring). Kết quả trội Pareto so với Olist: −1,9 ngày cam kết hoặc +2,2 điểm % đúng hạn. **DEMO** goal seeking 92%. Nói thẳng hạn chế của tháng 03/2018. |
| 15 | DSS-2: kể quá trình lặp thiết kế (AUC 0,65 → dời thời điểm quyết định → 0,768). Ngưỡng chọn theo chi phí, precision 45,0% > mức hòa vốn 32%. |
| 16 | DSS-3: **DEMO** kéo 1 cặp so sánh AHP để thấy CR và xếp hạng đổi theo. Monte Carlo cho ρ = 0,974, tức khuyến nghị vững. |
| 17 + kết | Hạn chế trung thực, hướng phát triển, kết luận 1 câu. |

## Câu hỏi thầy có thể hỏi và gợi ý trả lời

**Thiết kế DW**
- *Grain của fact_sales là gì, vì sao?* — 1 dòng sản phẩm trong đơn, mức nguyên tử nhất. Câu hỏi cấp đơn vẫn trả lời được bằng `COUNT(DISTINCT order_id)`.
- *Star hay snowflake? Vì sao?* — Star. Dimension nhỏ nên dư thừa không đáng kể, đổi lại ít join hơn và người dùng dễ hiểu.
- *Vì sao không khai báo FOREIGN KEY?* — DuckDB không cho UPDATE dòng đang được tham chiếu, nên cản trở SCD1/SCD6. Thay vào đó RI được kiểm tra bằng RI01–RI04 sau mỗi batch, và batch nào lỗi thì rollback. Nhiều DW production cũng tắt FK để nạp nhanh.
- *SCD Type 6 khác Type 2 thế nào?* — Type 6 thêm cột current_state (Type 1, ghi đè trên mọi phiên bản) và original_state (Type 0). Nhờ vậy phân tích được cả as-was lẫn as-is (Q11).
- *Bridge table dùng để làm gì?* — Review và giao trễ được đo ở cấp đơn, nhưng một đơn có thể có nhiều người bán. Trọng số = tỷ trọng doanh thu, tổng trọng số mỗi đơn = 1 (RC05).
- *Semi-additive là gì, ví dụ trong kho?* — open_orders_eom, cum_gmv: không được cộng theo thời gian (Q12).
- *Surrogate key để làm gì?* — Để có nhiều phiên bản cho cùng một khóa tự nhiên (SCD2), độc lập với hệ nguồn, và join nhanh bằng số nguyên.
- *Accumulating snapshot được cập nhật thế nào?* — So sánh trạng thái as-of với fact hiện có, UPDATE các mốc mới, tăng update_count.

**ETL**
- *Incremental dựa vào đâu?* — Watermark = cutoff của batch trước. Đơn mới lọc theo purchase_ts, review theo created_ts. Riêng accumulating snapshot thì tính lại cho mọi đơn chưa đóng.
- *Nếu batch lỗi giữa chừng thì sao?* — Cả batch nằm trong 1 transaction, có lỗi thì ROLLBACK, ghi batch_log FAILED, watermark giữ nguyên nên chạy lại được (idempotent).
- *ETL hay ELT?* — Có thể coi là ELT-style: nạp thô vào landing rồi biến đổi bằng SQL ngay trong engine. Về mặt logic vẫn tách staging và presentation như ETL truyền thống.

**DSS**
- *DSS khác dashboard ở điểm nào?* — DSS có mô hình, biến quyết định, what-if, goal seeking, sensitivity và đưa ra khuyến nghị. Dashboard chỉ mô tả hiện trạng.
- *Vì sao dùng quantile mà không dùng dự báo trung bình?* — Cam kết cần đạt xác suất đúng hạn α. Quantile α của phân phối thời gian giao chính là điều đó.
- *Có bị rò rỉ dữ liệu (data leakage) không?* — Không. Lịch sử người bán lấy snapshot của tháng trước, tắc nghẽn tính trên 14 ngày trước ngày bàn giao (không gồm ngày hiện tại), và chia dữ liệu theo thời gian.
- *Kết quả DSS-1 cải thiện ít vậy có ý nghĩa không?* — Chính sách của Olist vốn đã là một mô hình. Với khoảng 6.400 đơn một người bán mỗi tháng trong tập kiểm thử, +2,2 điểm % đúng hạn tương đương khoảng 140 đơn trễ ít hơn mỗi tháng, mỗi đơn trễ kéo review xuống khoảng 2★. Quan trọng hơn, DSS cho nhà quản lý **chủ động** chọn mức phục vụ.
- *CR trong AHP là gì? Nếu > 0,1 thì sao?* — CR = CI/RI. Nếu ≥ 0,1, giao diện cảnh báo và yêu cầu người dùng xem lại các phán đoán so sánh cặp.
- *Các giả định (độ co giãn, chi phí) lấy từ đâu?* — Đây là giả định, đã đưa thành tham số và có phân tích độ nhạy. Kết luận về chiều hướng không đổi khi thay giả định. Cần A/B test để hiệu chỉnh.

## Trước khi nộp (checklist)
- [ ] Điền tên, MSSV, số nhóm, % đóng góp: `report/latex/main.tex` (bìa + bảng phân công) và `slides/slides.html` (slide 1 và nhãn "Trình bày: SV x").
- [ ] Đẩy mã nguồn lên GitHub, điền link vào bìa báo cáo và slide cuối.
- [ ] Quay video 10–15 phút (mọi thành viên đều xuất hiện), upload YouTube unlisted, điền link vào bìa.
- [ ] Build lại PDF: `python report/tools/build_pdf.py`.
- [ ] Nén file ZIP gồm: `Bao_cao_BTL_CO4031.pdf`, `src/`, `data/` (sample + script), `Slide_BTL_CO4031.pdf`.
