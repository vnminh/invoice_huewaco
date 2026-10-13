# Hướng dẫn quản trị đối soát HueWACO

Trang quản trị dùng để đối soát tệp ngân hàng, xác nhận và quản lý kiến thức lịch sử. Không phụ thuộc một tháng hoặc một ngân hàng cụ thể.

## Đối soát giao dịch

1. Chọn Excel ngân hàng và bấm **Bắt đầu đối soát**. Hệ thống đọc tất cả sheet có bố cục giao dịch nhận diện được.
2. Theo dõi trong **Tác vụ xử lý**; kết quả xuất hiện theo từng đợt. Có thể dừng, giữ kết quả đã hoàn tất.
3. Chọn đúng **Tệp đang xem**, tìm theo nội dung/mã/tên/sheet/kênh/kỳ, lọc theo đề xuất và trạng thái duyệt.
4. Mở **Kiểm tra** để đối chiếu giao dịch hiện tại với mẫu lịch sử, số theo thứ tự, nguồn ví dụ và lý do đề xuất.
5. Kiểm tra khách hàng rồi **Xác nhận & học**; sửa mã/tên trước khi xác nhận nếu cần.

| Đề xuất | Cách xử lý |
| --- | --- |
| Có thể xác nhận | Có bằng chứng đủ mạnh; kiểm tra và xác nhận riêng hoặc chọn hàng loạt. |
| Cần duyệt | Có ứng viên nhưng chưa đủ để tự ghép. |
| Kiểm tra thủ công | Có mâu thuẫn hoặc bằng chứng yếu; vẫn có gợi ý liên quan để chọn. |
| Không ghép | Ghi nợ/không phù hợp hoặc cặp khách hàng đã bị từ chối. |

Điểm là mức bằng chứng, không phải xác suất. Mã chưa có trong kho luôn cần kiểm tra thủ công 0%. Không có thông tin liên quan mới để trống; một mẫu ngân hàng chung không đủ để điền khách hàng.

### Gợi ý và nhiều khách hàng

Trong màn hình kiểm tra, mở danh sách gợi ý và chọn **Chọn** để điền vào form. Chọn gợi ý chưa làm phát sinh kiến thức. Có thể nhập mã mới hợp lệ và bổ sung tên sau.

Mở **Nhóm khách hàng cần kiểm tra** để xem những người từng dùng cùng ngân hàng/mẫu hoặc có tên liên quan. Có thể tìm theo tên/mã và chuyển trang. Nhóm một người vẫn cần duyệt; nếu phạm vi rộng, tiếp tục tìm hoặc nhập khách hàng ngoài nhóm. Thiếu IDKH có thể giúp chọn nhóm lịch sử tương tự, không tự chứng minh người trả. Xem [chi tiết nhóm gợi ý](contextual-recommendations.md).

UI dùng **Chưa có lịch sử**, **Chỉ có nhóm gợi ý** hoặc **Đã xác nhận** khi phù hợp, tránh hiểu điểm 0 là gợi ý chắc chắn sai. Điểm gốc vẫn được giữ trong tệp xuất để đối chiếu.

Một lần chuyển có thể thanh toán nhiều khách hàng. Nhập các IDKH sáu số cách nhau bằng dấu phẩy; kiểm tra người thực sự được thanh toán. Hệ thống học các liên kết riêng sau xác nhận, không tự chia tiền. Nếu nhập nhiều tên, dùng dấu chấm phẩy theo đúng thứ tự mã; để trống nếu không rõ. Các mã được nhắc trong nội dung có thể là người khác, không phải mọi mã đều là người được trả.

Khoản thu hộ tổng hợp cần đối chiếu bảng kê. Danh sách khách hàng liên quan là lịch sử của mẫu, không tự coi đó là danh sách được trả ở lần hiện tại. Có thể quản lý danh sách đầy đủ trong **Khách hàng thu hộ**.

### Kỳ thanh toán và tên trong nội dung

**Kỳ thanh toán** lấy từ nội dung hóa đơn nước, tách khỏi ngày giờ chuyển khoản. Ví dụ `kỳ 8/2026`, `ky 082026`, `KY72026`, `T6N2026`, `Nuoc6/2026` được đọc thành tháng/năm. Chỉ ghi `tháng 8` thì hiển thị **Tháng 08 (chưa rõ năm)**; không đoán năm theo ngày chuyển tiền. Một giao dịch có thể có nhiều kỳ. Nội dung mơ hồ cần kiểm tra đoạn bằng chứng.

Ví dụ MB có trường riêng:

```text
MB.5051-66994-20260913-101133-06800.20260913.HUE WATER SUPPLY JSC.131173.1030324868.08/2026.Tran Thi Ngoc..1
```

Ngày chuyển là 13/09/2026 10:11:33, kỳ là 08/2026, tên là Tran Thi Ngoc. Bố cục hợp lệ còn cho trường mã khách hàng 131173; số 1030324868 là tham chiếu. Tên đơn vị nhận không phải tên khách hàng. Trường MB/Agribank được đọc riêng; nhận diện tên tự động hỗ trợ các nội dung khác. Tên có thể là người trả thay nên không tự xác định khách hàng.

Kỳ chỉ lưu ở kết quả đối soát; học bỏ ngày/kỳ khỏi đặc trưng. Tên tự trích chỉ thành bí danh khi liên kết đủ rõ và đã được con người xác nhận. Tên chuẩn không tự bị thay bằng tên nhận diện.

### Xác nhận hàng loạt

Chỉ các dòng **Có thể xác nhận**, chưa duyệt, có khách hàng và không cần phân bổ nhiều người được chọn hàng loạt. Tối đa 100 dòng mỗi lần. Dòng gợi ý yếu, thủ công hoặc nhiều mã phải duyệt riêng. Lỗi một dòng không hủy các xác nhận đã hoàn tất.

Chọn checkbox chưa học. Bấm xác nhận mới ghi kiến thức. Sửa IDKH rồi xác nhận cũng học ngay; từ chối không tạo mẫu đúng mới. Nếu gián đoạn sau commit, thao tác còn chờ được tiếp tục bằng receipt để không học lặp.

## Học dữ liệu mới

Nhập Excel/CSV **đã được kiểm tra**, đánh dấu xác nhận nguồn rồi bấm **Nhập & học dữ liệu**. Mã sáu số và số tiền hợp lệ là điều kiện cơ bản; nội dung có nhiều mã, mã khác nhãn hoặc không có mã vẫn học theo nhãn đã duyệt. `ko` bỏ qua, nhãn chưa phân giải không tạo khách hàng.

Mỗi sheet được nhận diện theo tiêu đề riêng: IDKH/MaKH/Mã KH/Mã số KH/Mã khách hàng… Cột ID đơn lẻ cần đi kèm tên KH rõ và nội dung giao dịch. Sheet hồ sơ/tổng hợp không có giao dịch được bỏ qua. Nếu không nhận diện bố cục, bấm **mẫu bố cục** để xem ngay trên UI và sửa tệp. Không cần tải mẫu.

Một khách hàng có nhiều cách ghi: thêm ví dụ mới để giữ mẫu cũ. Một HD được phép thuộc nhiều IDKH. Dữ liệu được học theo từng liên kết, không ghi đè số của người này sang người khác.

Có thể dùng một dòng một IDKH hoặc danh sách mã phân cách trong cùng ô. Mỗi tên cần tương ứng đúng người; không dùng một tên chung cho mọi mã. Xem [bố cục và ví dụ CSV](excel-layout-and-export.md).

Các trường kiểu thanh toán là tùy chọn: Ngân hàng thu hộ, trả qua ví điện tử, người dùng tự chuyển khoản hoặc các mã proxy/self/unknown. Để trống thì dùng nhãn mẫu/kênh/bố cục đủ rõ; chưa rõ vẫn unknown.

Lỗi từng dòng được ghi theo sheet/dòng và các dòng khác tiếp tục. Vào tác vụ để xem nguyên nhân hoặc tải CSV lỗi; lỗi toàn tệp/kết nối/cấu hình vẫn có thể dừng. Dừng giữ đợt đã commit. Nhập lại tệp đã duyệt đọc lại để bổ sung/khôi phục liên kết, receipt ngăn tăng số lần gặp của giao dịch đã học.

## Quản lý kiến thức

Mọi bảng ứng dụng đều xem/tìm/phân trang được. Bảng hỗ trợ chỉ xem; hồ sơ và mẫu có thao tác nghiệp vụ.

| Nhóm | Thao tác |
| --- | --- |
| Khách hàng | Thêm, sửa tên, xóa hồ sơ/kiến thức của người đó; mở mẫu đã học hoặc mẫu thu hộ. |
| Mẫu giao dịch | Tìm/lọc, đổi tên/ghi chú, gán kiểu thanh toán; xem liên kết và khách hàng thu hộ. |
| Liên kết mẫu với khách hàng | Xem ví dụ/số/nguồn, sửa nội dung hoặc khách hàng, thêm mẫu khác, sửa kiểu riêng, xóa. |
| Khách hàng thu hộ | Xem/tìm/phân trang danh sách của mẫu; xóa một thành viên. |
| Bảng hỗ trợ | Xem alias, người/tài khoản trả tiền, slots, giá trị số, khóa tìm, receipt và thông tin kho. |

**Thêm mẫu khác** giữ mẫu cũ. **Lưu mẫu đã sửa** dùng khi ví dụ cũ sai; ứng dụng xây lại số/chỉ mục. Xóa liên kết giữ mẫu chung và người khác; xóa khách hàng dọn kiến thức thuộc người đó. Mẫu chung chỉ xóa khi không còn liên kết/membership. Nhập lại nguồn đã duyệt có thể khôi phục quan hệ đã xóa.

### Gán kiểu thanh toán cho mẫu

Mở Chi tiết mẫu giao dịch, chọn Thu hộ/Tự trả/Chưa xác định, loại đơn vị và tên, kiểm tra phạm vi rồi lưu. Nhãn cập nhật các liên kết hiện có và làm mặc định cho liên kết học mới. Có thể sửa ngoại lệ ở liên kết; gán lại toàn mẫu sẽ thay ngoại lệ.

Sau tìm kiếm/lọc, **Cập nhật tất cả mẫu đang lọc** áp dụng cả các trang chưa hiển thị. Kiểm tra số mẫu/liên kết trước xác nhận. Nếu phạm vi thay đổi, hệ thống yêu cầu làm mới, không cập nhật một phần. Thành viên thu hộ nhận kiểu từ mẫu.

## Tác vụ, xuất tệp và dọn hiển thị

**Tác vụ xử lý** hiển thị tiến trình theo sheet, số dòng lỗi, lý do sheet chưa nhận diện, nút dừng/mở kết quả/tiếp tục xác nhận/xóa tệp. Cập nhật tiến trình giữ trạng thái phần đang mở và lựa chọn dropdown.

**Tải Excel** chia sheet theo tệp gốc. **CSV theo sheet (ZIP)** chứa mỗi sheet một CSV, giữ nội dung, nguồn dòng, kỳ, gợi ý và khách hàng đã xác nhận riêng. Xuất toàn bộ kết quả đã lưu của tệp đang xem, không chỉ trang/bộ lọc hiện tại. Không xuất riêng dữ liệu đã học. Bố cục chuẩn chỉ hiển thị trên UI.

**Xóa kết quả hiển thị** làm trống màn hình; có thể mở lại tệp từ tác vụ. **Làm mới** đọc lại dữ liệu/trạng thái. **Xóa tệp làm việc** xóa kết quả và báo cáo của tác vụ đã dừng, giữ kiến thức; bị chặn khi còn xác nhận chưa hoàn tất. Tải báo cáo lỗi trước khi xóa nếu cần giữ.

PostgreSQL chỉ lưu kiến thức. Working files chứa tác vụ/kết quả/duyệt; sao lưu cùng database khi cần giữ quy trình đang làm dở. Nâng cấp schema do quản trị tự chạy theo [hướng dẫn nâng cấp](migration-and-storage.md).

## Bổ sung và sửa kiến thức

Trong **Quản lý kiến thức**, chọn **Thêm bí danh khách hàng** hoặc **Thêm bí danh ngân hàng / ví**; kiểm tra thông tin rồi lưu. Hồ sơ khách hàng có nút xem/thêm bí danh riêng. Bí danh ngân hàng áp dụng cho tên sheet/cột trùng chính xác; không dùng để đổi ngân hàng của kiến thức cũ.

Mở **Tên / bí danh khách hàng** để duyệt tên tự nhận diện hoặc xóa tên sai. Mở **Ghép sai đã bị từ chối** để gỡ một lần chặn sai. Thay đổi này cho xét lại, không tự xác nhận giao dịch.

**Nhật ký thay đổi** cho xem trước/sau và hoàn tác bí danh đủ điều kiện. Các thay đổi lớn sửa lại từ khách hàng/mẫu tương ứng. Nếu báo kiến thức đã đổi, làm mới rồi kiểm tra lại trước khi lưu. [Chi tiết cơ chế](interactive-knowledge.md).
