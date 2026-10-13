# Gợi ý nhóm khách hàng theo lịch sử

## Mục đích

Không đủ căn cứ tự xác nhận một khách hàng vẫn có thể đủ căn cứ giới hạn một nhóm để người dùng kiểm tra. Ví dụ nhiều tháng một người gửi cùng nội dung qua BIDV nhưng không ghi IDKH; hoặc một tên đơn vị từng trả cho nhiều đồng hồ.

Hệ thống có hai đầu ra riêng:

- **Gợi ý cá nhân:** có mã, tên, tài khoản hoặc chi tiết lịch sử liên quan đến một khách hàng.
- **Nhóm cần kiểm tra:** tập khách hàng từng liên quan đến cùng ngân hàng, mẫu hoặc tên. Không tự chọn phần tử đầu tiên, không nâng điểm tự ghép và không học toàn nhóm.

## Cách giới hạn nhóm

1. Chuẩn hóa ngân hàng/kênh qua bí danh đã được quản trị xác nhận.
2. Tìm mẫu cùng ngân hàng và bố cục nội dung; ngày/kỳ và các số biến đổi đã có placeholder. Không sửa, cắt hoặc chia giá trị số để suy người.
3. Tìm tên/bí danh có trong nội dung, gồm tên người/đơn vị đã học và tên do bộ trích xuất cung cấp. Việc tìm bí danh chính xác vẫn chạy khi NER không sẵn sàng.
4. Nếu tên có lịch sử trong tập ngân hàng/mẫu, lấy giao của hai nguồn để thu hẹp. Nếu không có lịch sử giao nhau, giữ nhóm theo tên và ghi đúng căn cứ; khách hàng có thể chuyển từ ngân hàng khác.
5. Nếu chưa tìm thấy mẫu/tên, dùng nhóm lịch sử cùng ngân hàng và đánh dấu **phạm vi rộng**. Khi nội dung hiện tại không có IDKH và lịch sử có ví dụ đã ghi nhận không có IDKH, ưu tiên tập lịch sử đó. Metadata cũ thiếu trường không được coi là đã quan sát thấy “không có ID”.
6. Không có tên, mẫu hoặc kênh liên quan thì nhóm rỗng. Không lấy tùy ý khách hàng toàn kho.

Luôn giữ thêm các gợi ý cá nhân liên quan đã tìm được từ mã/tài khoản/tên/chi tiết lịch sử, kể cả thuộc ngân hàng hoặc mẫu khác. Các gợi ý này đứng trước và có căn cứ riêng ở từng dòng. Nhóm là hợp của các nguồn có liên quan, không dùng giao cứng để loại mất người đúng khi họ đổi cách ghi. Nếu không nhận diện ngân hàng nhưng có gợi ý cá nhân, chỉ giữ những người đó, không lấy một nhóm ngân hàng `unknown` tùy ý.

Thiếu IDKH là một đặc điểm của cách ghi, có ý nghĩa **trong bối cảnh ngân hàng/lịch sử tương ứng**. Nó không chứng minh danh tính và không có nghĩa các khách hàng ngoài nhóm chắc chắn sai. Người dùng vẫn tìm/nhập IDKH ngoài nhóm qua form xác nhận bình thường.

Các số, tài khoản và mã rõ ràng vẫn được xử lý bởi luồng gợi ý cá nhân/so khớp hiện có. Nhóm chỉ bổ sung thông tin ở dòng cần duyệt; không ghi đè mã mới được phát hiện hoặc vượt qua mâu thuẫn định danh.

## Xếp thứ tự và dữ liệu

Nhóm gộp khách hàng từ `transaction_patterns` và `collection_memberships`, liên kết với `payment_templates`; tên/bí danh từ `customers` và `customer_aliases`. Một khách hàng xuất hiện một lần dù có nhiều liên kết.

Xếp trước khách hàng có lịch sử cùng ngân hàng, sau đó số lượt đã xác nhận và thời điểm cập nhật; IDKH chỉ dùng để ổn định thứ tự khi bằng nhau. Số lượt không phải xác suất, cũng không chứng minh đã lặp qua nhiều tháng. Bộ đếm receipt chống học trùng hiện có vẫn áp dụng.

Loại khỏi nhóm các cặp nội dung/khách hàng đã bị từ chối trong `hard_negatives`. Một tên có nhiều chủ sở hữu vẫn giữ nhiều người; không coi trùng tên là khóa duy nhất.

Roster thu hộ là lịch sử từng được phân bổ, **không phải bảng kê của lần chuyển hiện tại**. Người dùng cần bảng kê khi xử lý quyết toán tổng hợp; nhóm không tự phân bổ hay chia tiền.

Không thêm bảng hoặc migration. Kết quả nhóm là dữ liệu làm việc ngoài PostgreSQL; PostgreSQL tiếp tục chỉ lưu kiến thức và nhật ký chỉnh kiến thức.

## API và UI

Kết quả `manual_check` thêm `candidate_group`:

- `kind`: `bank_template_entity`, `bank_template`, `bank_entity`, `entity`, `bank`, `individual` hoặc `none`.
- `total`, `offset`, `limit`, `members`: tổng số và trang đang xem; bản kết quả ban đầu giữ tối đa 6 người để không phình tệp working.
- `sources`, `channel`, `broad`: căn cứ và mức độ rộng của tập.
- `missing_customer_id`, `missing_id_profile_used`: có thiếu ID và có thực sự dùng đặc điểm thiếu ID trong lịch sử không.
- `includes_individual_suggestions`: có giữ thêm gợi ý cá nhân ngoài phạm vi bối cảnh không.
- Thành viên có `customer_id`, `customer_name`, `seen_count`, `pattern_count`, `collection_count`, `sources`, `group_only`. `group_only: true` nghĩa chỉ có căn cứ nhóm; `false` nghĩa có gợi ý cá nhân riêng. Bộ đếm là lịch sử trong phạm vi bối cảnh, có thể bằng 0 với người được bổ sung từ gợi ý khác. Không gán xác suất cho thành viên nhóm.

`GET /transactions/{transaction_id}/candidates?q=...&offset=...&limit=20` tìm/phân trang trực tiếp từ kiến thức hiện tại, kể cả kết quả làm việc cũ chưa có `candidate_group`. Tối đa 50 người/trang, tìm theo IDKH/tên/bí danh. Endpoint yêu cầu cùng đăng nhập quản trị, chỉ đọc, dùng ORM tham số hóa/escape wildcard và không nhận SQL hoặc danh sách template tùy ý.

Trong **Kiểm tra giao dịch**, mở **Nhóm khách hàng cần kiểm tra** để tìm kiếm, chuyển trang và bấm **Chọn**. Nút này chỉ điền form; **Xác nhận & học** mới lưu liên kết được duyệt. Nhóm một người vẫn không tự chuyển sang chấp nhận; nhóm yếu cũng không đủ điều kiện xác nhận hàng loạt.

UI thể hiện rõ trạng thái:

- Khách hàng chưa có trong kho: **Chưa có lịch sử**; API vẫn giữ điểm 0 và yêu cầu duyệt.
- Chỉ có nhóm, không có bằng chứng cá nhân: **Chỉ có nhóm gợi ý**.
- Đã duyệt: **Đã xác nhận**; không tiếp tục hiển thị điểm dự đoán ban đầu như trạng thái hiện tại.

CSV/XLSX thêm bốn cột: số khách hàng trong nhóm, phạm vi, căn cứ và danh sách xem trước. Danh sách xem trước không phải toàn bộ nhóm; tổng số và UI phân trang cho biết phạm vi đầy đủ. Xuất giữ snapshot lúc đối soát, còn nhóm mở trên UI tra kiến thức mới nhất.

## Kiểm chứng

78 kiểm tra qua, bao phủ nội dung lặp khác kỳ không có ID, cùng mẫu nhưng khác ngân hàng, tên thu hẹp nhóm không cần NER, đặc điểm thiếu ID, phân trang/tìm kiếm có ký tự SQL, hard negative, roster gọn và khách hàng mới vẫn 0%. Kiểm tra API xác nhận nhóm không được học/duyệt hàng loạt; Chromium kiểm tra chọn rồi xác nhận trên desktop/mobile.

Benchmark nhóm dùng lại lát cắt 500 dòng để so sánh hồi quy với kết quả trước. Đây là tập đã được phân tích, **không gọi là holdout độc lập mới**. Đo riêng độ bao phủ nhóm đối với khách hàng đã biết và kích thước nhóm; một nhóm rất rộng có recall cao nhưng có thể chưa giúp người duyệt nhiều. Quy tắc tự chấp nhận không đổi. Xem phần cập nhật nhóm tại [báo cáo benchmark](interactive-benchmark.md).

## Bổ sung bộ nhớ đã xác nhận

Bản hiện tại hợp nhất cả chủ sở hữu nội dung trong `recurring_links` vào phạm vi nhóm trước khi phân trang, nên giới hạn ứng viên không làm mất thành viên đã xác nhận. Cùng nội dung A–B rồi học thêm mẫu riêng A vẫn giữ nhóm cũ. Mẫu riêng đủ điều kiện được tra trực tiếp và có thể tự nhận; do đó các số liệu 78 kiểm tra/auto không đổi phía trên là kết quả trước cập nhật bộ nhớ. Xem [quy tắc và kiểm chứng hiện tại](recurring-recognition.md).
