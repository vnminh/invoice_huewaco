# Mẫu giao dịch và thu hộ

Một mẫu có thể được nhiều khách hàng sử dụng; một người có nhiều mẫu. Phần chung lưu trong `payment_templates`, số/ví dụ riêng lưu trong `transaction_patterns`. Không gộp số của nhiều người vào cùng một bản ghi số riêng.

## Phân biệt kiểu thanh toán

| Kiểu | Ý nghĩa |
| --- | --- |
| `proxy` | Ngân hàng, ví hoặc dịch vụ thu hộ trả thay khách hàng. |
| `self` | Khách hàng/người dùng tự chuyển khoản. |
| `unknown` | Chưa có đủ căn cứ để phân biệt. |

`provider_kind` phân biệt bank/wallet/other/unknown; `provider_name` là tên đơn vị. Đây là thông tin kiểu thanh toán, không phải điều kiện xác định duy nhất khách hàng.

Nguồn nhãn theo thứ tự khi học: nhãn trong dòng đã duyệt; mặc định mẫu được admin gán; kênh dịch vụ thu hộ rõ như MoMo/Payoo/VNPAY; bố cục thu hộ cố định đã nhận diện. Nếu không có, giữ unknown. Nhãn tự trả xóa đơn vị thu hộ. Các nhãn tiếng Việt như **Ngân hàng thu hộ(mẫu cố định)**, **trả qua ví điện tử**, **người dùng tự chuyển khoản** được chấp nhận. Nhãn tùy chọn chưa hiểu không làm mất dòng học IDKH đã duyệt.

Đối soát có thể gợi ý proxy từ bố cục cố định, kênh ví hoặc liên kết lịch sử đã khớp chắc. Bằng chứng ghi rõ nguồn; người dùng được sửa trước xác nhận. Tên ngân hàng chung, người gửi hoặc chỉ chữ giống nhau không đủ chứng minh thu hộ/tự trả. Admin có thể sửa các suy luận bố cục không phù hợp thực tế.

## Bố cục và liên kết riêng

Mẫu chung có fingerprint gồm **ngân hàng/kênh, shared shape, structure**. Tên đã xác nhận/đọc rõ được che trong shared shape; placeholders số giữ thứ tự. Các liên kết giữ khách hàng, ví dụ gốc, số theo slot, embedding, nguồn, độ lặp và loại thanh toán riêng. Một người có thể có nhiều liên kết dưới một mẫu khi hợp đồng/mã ổn định khác nhau.

Không ghi đè chỉ vì template giống trên 90%. Số định danh khác phải được giữ thành biến thể, số tham chiếu biến đổi có thể bổ sung vào slot cùng liên kết. IDKH sáu số rõ và đã biết ưu tiên hơn HD; một HD được phép có nhiều IDKH. Trong bố cục cụ thể, HD sáu số có thể được học là IDKH thông qua nhãn đã xác nhận và context ngân hàng.

Mẫu dùng chung không cho biết ai trả. So khớp kiểm tra số đầy đủ/đúng vai trò/đúng thứ tự và chủ sở hữu toàn kho. Tên và tài khoản trung gian chỉ hỗ trợ tìm. Thiếu bằng chứng thì vẫn hiển thị gợi ý liên quan cho người duyệt, không tự chấp nhận.

## Quyết toán tổng hợp và danh sách khách hàng

Một khoản của ví/dịch vụ có thể được phân bổ thành hàng trăm dòng trong tệp đã duyệt. Khi nhóm liền nhau có cùng nội dung, tham chiếu riêng và ít nhất 10 người, hệ thống lưu gọn mẫu/receipt/hồ sơ và `collection_memberships`. Mỗi membership duy nhất theo mẫu/khách hàng, có count/lần gặp gần nhất. Không tạo các vector giống nhau cho từng người từ một nội dung không nêu từng người.

Đối soát lần sau hiển thị khoản tổng hợp và danh sách khách hàng từng liên quan; yêu cầu bảng kê của lần hiện tại. Danh sách không có nghĩa mọi thành viên đều được trả ở lần này. Người có IDKH rõ trong một giao dịch riêng vẫn được tìm trong registry, kể cả chưa có pattern riêng.

Khoản tự trả nhiều đồng hồ vẫn học đầy đủ từng liên kết. Nhiều IDKH nhắc đến chỉ là thông tin gợi ý; người dùng xác nhận tập người thực nhận. Không tự chia tiền.

## Quản lý trên UI

Vào **Quản lý kiến thức → Mẫu giao dịch**:

1. Tìm kiếm và lọc kiểu thanh toán; mở Chi tiết để sửa tên/ghi chú hoặc gán Thu hộ/Tự trả/Chưa xác định.
2. Xác nhận phạm vi rồi lưu: nhãn cập nhật mẫu và các liên kết hiện có. Liên kết mới không có nhãn riêng dùng mặc định của mẫu.
3. Mở **Xem khách hàng dùng mẫu** để xem liên kết đầy đủ; **Khách hàng thu hộ** để xem roster tổng hợp.
4. Sửa kiểu riêng ở liên kết nếu có ngoại lệ. Gán lại toàn mẫu sẽ thay ngoại lệ hiện có.

**Cập nhật tất cả mẫu đang lọc** áp dụng cả trang chưa hiển thị. Backend kiểm tra revision, dấu phạm vi ID/nhãn và số mẫu/liên kết trước khi cập nhật trong một transaction; phạm vi đổi thì yêu cầu làm mới. Số liên kết bao gồm membership tổng hợp; các membership nhận loại từ mẫu. Thao tác gán loại không đổi IDKH/số hoặc tăng lần học.

Ở hồ sơ khách hàng có **Xem mẫu đã học**, **Xem mẫu thu hộ** và thêm mẫu mới. Thành viên thu hộ được xem/xóa riêng; xóa membership không xóa hồ sơ hay mẫu. Chỉ xóa mẫu chung khi không còn liên kết hoặc membership. Xóa dữ liệu có xác nhận; nhập lại nguồn đã duyệt có thể tạo lại quan hệ.

## SQL

Database mới chỉ chạy [create_current.sql](../sql/create_current.sql). Kho cũ đến 004 chạy [005](../sql/migrations/005_confirmed_evidence.sql), rồi reindex và nhập lại dữ liệu thu hộ tổng hợp để điền roster cũ. Sau v7/005 chạy thêm [006](../sql/migrations/006_interactive_knowledge.sql) để dùng quản trị bí danh/nhật ký. Xem [hướng dẫn nâng cấp](migration-and-storage.md) và [schema chi tiết](database-schema.md).

Khách hàng chỉ có trong registry/roster, chưa có mẫu thanh toán riêng phù hợp, được gợi ý với điểm thấp và yêu cầu duyệt; không tự ghép chỉ vì IDKH có tồn tại. Xác nhận của người dùng bổ sung mẫu riêng để lần sau có căn cứ đối chiếu.
