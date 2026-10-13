# Nâng cấp độ tin cậy v7

Mục tiêu là ưu tiên precision của các đề xuất đủ mạnh, đồng thời giữ gợi ý hữu ích ở dòng cần duyệt. Đây là mô tả thay đổi core v7. Kết quả benchmark chạy sau đó được ghi riêng tại [interactive-benchmark.md](interactive-benchmark.md).

## Những vấn đề thấy trong dữ liệu

Đã đọc các sheet hỗ trợ trong những workbook hiện có ở `Data/`, gồm FN đã duyệt và ngân hàng gốc. Đã rà soát thêm các nội dung đại diện bằng bộ đọc quy tắc, không học vào PostgreSQL. Đợt rà soát parser ban đầu không chạy embedding/NER; benchmark E5 cô lập bổ sung được mô tả trong báo cáo riêng.

| Trường hợp | Vấn đề cần xử lý |
| --- | --- |
| Nhiều dòng FN cùng một chuyển tiền của ví | Nội dung là khoản quyết toán tổng hợp, không phân biệt từng người. |
| Mã khác nhãn hoặc nhiều mã trong nội dung | Nhãn đã duyệt có giá trị nghiệp vụ; không chặn học theo suy đoán parser. |
| Agribank `MA_GD:…|… ,Nuoc8/2026` | Có trường mã/kỳ/tên theo bố cục; không nên coi tất cả số là mã. |
| SCB `T8N2026`, VP `KY72026` | Dạng kỳ liền cần nhận diện và che khỏi học. |
| MB chuỗi có dấu chấm cuối | Bố cục mã/tên/kỳ hợp lệ cần đọc được với biến thể hậu tố. |
| MaKH là một dãy số rất dài | Không được cắt thành mã sáu số hoặc gộp sai thành IDKH. |
| Tài khoản/tham chiếu sáu số tình cờ bằng IDKH | Cần vai trò và context theo ngân hàng trước khi coi là bằng chứng sở hữu. |
| Dòng cần duyệt đã có tên/mã/tài khoản liên quan | Không nên xóa hết gợi ý chỉ vì chưa đủ để tự ghép. |

## Các thay đổi chính

- Chuẩn hóa IDKH sáu chữ số; phục hồi số 0 chỉ ở cột nhãn được duyệt, giữ nguyên dãy dài trong nội dung.
- Học các nhãn được xác nhận dù khác nội dung, nhiều mã hoặc không có mã. Lưu chẩn đoán để đối soát không tự tin sai vào mã in trong nội dung.
- Context và mẫu theo ngân hàng; khóa `slot-id` chứa kênh, ngữ cảnh và giá trị nguyên chuỗi.
- Giữ thứ tự/vai trò/định dạng mã, không dùng fuzzy numeric matching để chọn người. HD có thể là mã trong bố cục được xác nhận; IDKH rõ được ưu tiên.
- Mở rộng kỳ hóa đơn và trường tên theo bố cục; dùng cùng vùng ngày/kỳ để loại khỏi đặc trưng học. Thiếu năm thì giữ thiếu năm.
- Kết hợp retrieval có hạn mức đa dạng khách hàng và chỗ cho bằng chứng trực tiếp; giữ các điều kiện định danh sau rerank.
- Gợi ý dự phòng từ registry, tài khoản, tên, hợp đồng/context và nội dung lịch sử; không điền khách hàng chỉ bằng mẫu ngân hàng chung.
- Nhiều người cần phân bổ thủ công; tên NER hoặc một tên chung không tự gán cho tất cả.
- `collection_memberships` giữ roster của mẫu quyết toán lớn, có quản lý trên web và giới hạn gợi ý ở kết quả. Không coi roster lịch sử là bảng kê lần hiện tại.
- Xác nhận hàng loạt chỉ dùng đề xuất đủ mạnh. Xác nhận sửa hoặc nguồn đã duyệt có thể bỏ negative cũ và cập nhật kiến thức ngay; receipt chống học lặp.

## Cập nhật kho đã học

Database mới dùng một file [create_current.sql](../sql/create_current.sql). Kho cũ chạy [005](../sql/migrations/005_confirmed_evidence.sql) sau 004 và [reindex_knowledge.py](../scripts/reindex_knowledge.py) khi backend dừng. Sau đó nhập lại nguồn đã duyệt để bổ sung roster tổng hợp và biến thể số không còn trong ví dụ đại diện. Chi tiết, lệnh Linux/Windows và các giới hạn ở [hướng dẫn nâng cấp](migration-and-storage.md).

## Cần kiểm chứng trên máy triển khai

Đợt cập nhật tương tác sau đó đã chạy regression và benchmark E5 cô lập, được ghi ở [interactive-benchmark.md](interactive-benchmark.md). SQL chỉ được sinh/đối chiếu, chưa chạy trên PostgreSQL của người dùng. Runtime Windows, chỉ mục PostgreSQL và NER vẫn cần đo riêng; không suy rộng số đo SQLite thành hiệu năng triển khai.

Khi người dùng đánh giá, tách rõ ba loại kết quả:

1. Precision/recall/F1 của `auto_accept`, ưu tiên số ghép sai; sau đó xem thêm F0.5.
2. Khả năng đưa đúng người vào danh sách gợi ý cho dòng cần duyệt, kể cả khách hàng ít lịch sử hoặc chỉ có trong roster.
3. Độ đúng của phân bổ nhiều khách hàng và kỳ hóa đơn; không gộp với chỉ số ghép một người.

Tách FN học và FN nhãn đánh giá. Không học kỳ thử vào kho trước khi chấm. `scripts/benchmark_all.py` dùng kho SQLite riêng và có thể chọn sheet; bỏ `--sheets` để đọc mọi sheet. Script dùng cùng luồng nhập và nhãn nhiều IDKH; chấm allocation riêng. Các đường HNSW/GIN/PostgreSQL và runtime Windows cần được kiểm tra trên triển khai thực tế, không suy ra từ fixture SQLite.

## Giới hạn còn lại

Nội dung ngân hàng không có định danh riêng, tên trùng, khoản trả hộ hoặc bảng kê không được cung cấp vẫn cần con người. Những giới hạn precision có thể làm tăng số dòng cần duyệt. Nhận diện kỳ không thể giải mọi tháng/năm mơ hồ. Kênh ngân hàng dùng alias mặc định và [bí danh do quản trị xác nhận](interactive-knowledge.md); đổi namespace đã có kiến thức cần bảo trì có kiểm soát. Học đáng tin vẫn phụ thuộc nhãn do người dùng cung cấp đúng.
