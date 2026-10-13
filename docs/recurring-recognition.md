# Giữ và nhận lại nội dung đã xác nhận

Học thêm là bổ sung bằng chứng. Một người có thể có nhiều mẫu; một nội dung có thể từng được xác nhận cho nhiều người. Mẫu mới cụ thể hơn không xóa liên kết nhóm của mẫu cũ.

| Kiến thức đã duyệt | Nội dung đang đối soát | Xử lý |
| --- | --- | --- |
| “Đơn vị An Bình nộp tiền nước” thuộc A và B | Cùng nội dung, kỳ khác | Giữ nhóm A–B, yêu cầu chọn/xác nhận; không tự chia khoản tiền |
| Sau đó học thêm “Đơn vị An Bình nộp tiền nước Ma KH 001234” thuộc A | Nội dung mới có mã A | Có thể nhận riêng A khi không có bằng chứng xung đột |
| Cả hai kiến thức trên vẫn tồn tại | Nội dung cũ không có mã | Vẫn nhóm A–B; việc A có mẫu riêng không làm B biến mất |
| Mẫu riêng thuộc A, sau đó được xác nhận thêm cho B | Nội dung ấy | Chuyển sang yêu cầu duyệt; đây là phát hiện mơ hồ, không phải quên A |

## Lưu trong database

Schema hiện tại có 16 bảng. `payment_templates` giữ bố cục chung. `transaction_patterns` giữ liên kết khách hàng, mẫu, các số và ví dụ đại diện. Bảng mới `recurring_links` giữ các **biến thể nội dung đã được duyệt**, kể cả khi nhiều biến thể được gộp vào cùng một liên kết mẫu:

- `pattern_id`, `customer_id`, `channel_key`: liên kết được xác nhận và kênh thanh toán.
- `signature_hash`, `signature`: dấu tra cứu và các đoạn chữ/số có thứ tự.
- `raw_example`, `source_file`, `source_row`, `example_date`: ví dụ thực đã duyệt và nguồn, dùng giải thích.
- `seen_count`, `last_period`, `last_seen`: số lần gặp và cập nhật. `last_period` là tháng giao dịch, không phải kỳ hóa đơn.

UNIQUE `(pattern_id, signature_hash)` ngăn nhân bản cùng biến thể trong một liên kết. Index `(channel_key, signature_hash)` tra trực tiếp. Học lại cùng receipt không tăng đếm nhưng có thể khôi phục một liên kết từng bị xóa. Học mới không xóa các quan sát cũ. Các khoản quyết toán thu hộ hàng loạt được lưu gọn ở `collection_memberships` vẫn theo luồng nhóm riêng; không biến danh sách hàng trăm người thành một định danh cá nhân.

Chỉ dữ liệu đã duyệt được đưa vào bộ nhớ này; không lưu mọi dòng đối soát vào PostgreSQL. Sửa/xóa kiến thức do quản trị chủ động thực hiện vẫn có thể gỡ liên kết. Đây là ngoại lệ có chủ đích với quy tắc giữ lịch sử học.

## Tra cứu và quyết định

1. Chuẩn hóa kênh bằng bí danh đã xác nhận. Phân tích nội dung theo bộ trích xuất xác định, không dùng giới hạn ứng viên hoặc thống kê slot toàn kho để tạo chữ ký.
2. Bỏ segment ngày/kỳ và vai trò được nhận diện là biến đổi: tham chiếu ngân hàng, giao thức ngân hàng, tham chiếu giao dịch, mã hóa đơn, số tiền, số lượng, tài khoản bên nhận. Giữ nguyên chuỗi và thứ tự của IDKH, hợp đồng, tài khoản/thẻ người trả, mã chữ–số, số vị trí và **số chưa biết vai trò**. Không cắt, nối, đổi thứ tự hay so gần đúng số.
3. Tra chữ ký theo kênh; đếm **mọi khách hàng** trước khi lấy sáu gợi ý đầu. Giới hạn tìm kiếm vector không được biến nhóm nhiều người thành một người.
4. Nếu chỉ có một chủ sở hữu, kiểm tra ví dụ lưu thực sự tạo lại đúng chữ ký, quan hệ khách hàng/kênh khớp liên kết mẫu, không có phản hồi từ chối hay mâu thuẫn mã rõ ràng. Nội dung phải có dấu hiệu riêng: định danh/số tài khoản/thẻ hoặc ít nhất hai từ ngoài nhóm từ chung. Câu “thanh toán tiền nước” đơn độc không đủ. Thu hộ cần thêm định danh; mẫu quyết toán nhiều người không tự nhận một người.
5. Khi đủ điều kiện, nhận lại mẫu riêng với điểm quy tắc **0,97**, xét ngưỡng tự chấp nhận cấu hình. Đây **không phải xác suất đúng đã hiệu chuẩn 97%**. Giao diện giải thích bằng ví dụ đã duyệt, nguồn, các số đối chiếu và các bước kiểm tra.
6. Cùng chữ ký thuộc nhiều người: giữ nhóm và yêu cầu duyệt. Nếu nội dung có nhiều mã rõ ràng, tiếp tục quy tắc đối chiếu phân bổ nhiều khách hàng hiện có; không suy ra phân bổ chỉ từ nhóm lịch sử.
7. Chưa có chữ ký phù hợp: tiếp tục tìm kiếm định danh, tên/bí danh, mẫu, embedding và nhóm ngữ cảnh như trước. Không có bộ nhớ phù hợp không có nghĩa là không được gợi ý.

Một nhãn đã duyệt vẫn được học dù mã viết trong nội dung khác người nhận. Khi đối soát lại, hiển thị người đã duyệt và mâu thuẫn để kiểm tra; không tự xác nhận mâu thuẫn đó.

Phản hồi từ chối ghi hai phạm vi: nội dung chuẩn hóa và chữ ký ổn định theo kênh. Vì vậy đổi kỳ/tham chiếu không né phản hồi từ chối. Phản hồi không xóa bằng chứng xác nhận cũ và không làm nhóm hai người thành “chỉ có một người” để tự ghép. Xác nhận lại rõ ràng cho người đó gỡ chặn tương ứng. Muốn sửa một xác nhận lịch sử sai, quản trị sửa/xóa liên kết mẫu trong UI.

## Quản lý, cập nhật và phục hồi

UI **Quản lý dữ liệu → Nội dung đã xác nhận** cho tìm kiếm, xem nguồn, khách hàng và nội dung. Bảng này chỉ xem; không có API tùy ý sửa chữ ký. Các thao tác sửa/xóa thực hiện qua luồng liên kết mẫu có xác nhận, kiểm tra revision và nhật ký. SQL dùng tham số và tên bảng từ danh mục cố định.

Database mới: chỉ dùng [create_current.sql](../sql/create_current.sql). Database cũ qua 006: tự chạy [007](../sql/migrations/007_recurring_memory.sql), rồi `scripts/rebuild_recurring_memory.py` khi backend dừng. Xem [lệnh Windows/Linux](migration-and-storage.md). Kho trước v7 cần đủ SQL đến 007 rồi reindex trước khi sử dụng.

Kho mới rỗng được đánh dấu sẵn sàng. Kho đã có mẫu nhưng chưa lập bộ nhớ được đánh dấu `pending`; rebuild dùng `building`, chỉ chuyển `confirmed-recurring-v1` khi hoàn tất. Chưa hoàn tất thì chỉ dùng liên kết như gợi ý, không tự chấp nhận bằng đường bộ nhớ mới. Rebuild giữ dữ liệu cũ, xử lý theo đợt, chạy lại được, không tải encoder/NER và không đọc lịch sử đối soát. Những biến thể không còn ví dụ trong kho cũ cần nhập lại từ FN/CSV đã duyệt; công cụ không thể khôi phục nội dung chưa từng được lưu.

## Kiểm chứng và giới hạn

`tests/test_recurring_memory.py` kiểm tra nhóm A–B rồi mẫu riêng A, thay tháng/tham chiếu, giữ thứ tự số, thêm nhiều mẫu và tên trùng, nhóm lớn vượt giới hạn ứng viên, học lặp, backfill, mâu thuẫn mã, từ chối và ví dụ bị sửa sai.

`scripts/benchmark_retention.py` dùng SQLite riêng: học các dòng FN đầu tiên, đối soát lại, học thêm rồi đối soát lại cùng tập ban đầu. Báo cáo số chủ sở hữu giữ lại, liên kết bị mất, trường hợp tự ghép sai và suy giảm nhận diện khi vẫn chỉ có một chủ sở hữu. Ví dụ:

```bash
.venv/bin/python scripts/benchmark_retention.py --train "Data/Ngan hang thang 7-2026 FN.xlsx" --sheet BIDV --initial 200 --growth 1000 --out runtime/bench_retention_new
```

Windows thay trình Python bằng `.\.venv\Scripts\python.exe`. Chọn thư mục output mới. Công cụ dùng hash embedding và tắt NER, cố ý giới hạn ứng viên ở 1 để kiểm tra đường tra bộ nhớ độc lập. Đây là **kiểm tra giữ kiến thức đã học**, không phải F1 trên tháng chưa học, không đo chất lượng embedding tiếng Việt và không đo tốc độ PostgreSQL.

Không thể bảo đảm tự ghép đúng nếu nội dung thực tế không phân biệt được nhiều khách hàng. Tên riêng cũng có thể trùng mà kho chưa biết; một xác nhận cũ không chứng minh độc quyền ngoài thực tế. Cần tiếp tục benchmark tháng khác và theo dõi các xác nhận sai. Khi xuất hiện bằng chứng mơ hồ, hệ thống giữ liên kết và chuyển sang duyệt.

### Kết quả ngày 13/10/2026

[Báo cáo tổng hợp](benchmarks/retention-2026-10-13.json): 200 dòng BIDV đã duyệt của FN tháng 7, sau đó thêm 1.000 dòng tiếp theo. Trước và sau đều có 175 dòng tự nhận đúng, 25 dòng nhóm nhiều người cần duyệt, 0 tự ghép sai. Giữ 200/200 chủ sở hữu, mất 0 liên kết mẫu và 0 quan sát; 0 suy giảm tự nhận khi vẫn một chủ sở hữu. Có 12 dòng chưa xác nhận/không hợp lệ bị bỏ qua trước khi đủ mẫu. Không đưa nội dung cá nhân vào báo cáo tổng hợp.

Bộ kiểm tra hồi quy toàn repo: **89 passed** trên SQLite/ASGI. Chưa chạy SQL trên PostgreSQL hay xác minh runtime Windows trong đợt này.
