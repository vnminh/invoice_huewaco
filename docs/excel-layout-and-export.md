# Bố cục Excel và xuất kết quả theo sheet

Hệ thống vẫn tự nhận diện từng sheet theo tiêu đề, không phụ thuộc ngân hàng hoặc vị trí cột. Bố cục chuẩn là cách sửa tệp khi nhận diện chưa thành công, không bắt buộc chuyển mọi tệp ngân hàng sang một mẫu duy nhất.

## Bố cục chuẩn cho đối soát

Tải **tệp mẫu đối soát** ở khu vực tải giao dịch hoặc Hướng dẫn sử dụng. Endpoint: `/templates/raw.xlsx`.

| Tiêu đề | Bắt buộc | Nội dung |
| --- | --- | --- |
| `NGAY` | Có | Ngày giờ chuyển tiền: `dd/mm/yyyy HH:MM:SS`, `dd/mm/yyyy`, `yyyy-mm-dd` hoặc ngày Excel. Không phải kỳ hóa đơn. |
| `NOIDUNG` | Có | Nội dung chuyển tiền nguyên văn, giữ toàn bộ chữ/số và thứ tự. |
| `GHICO` | Có | Tiền nhận vào, không âm; để trống hoặc 0 nếu dòng chỉ ghi nợ. |
| `GHINO` | Không | Tiền chuyển ra, không âm; để trống hoặc 0 nếu chỉ ghi có. |
| `REFERENCE` | Không | Tham chiếu ngân hàng; giữ dạng Text. |
| `NGANHANG` | Không | Ngân hàng/kênh giao dịch; nếu trống dùng tên sheet. |
| `KIEUTHANHTOAN` | Không | `proxy` thu hộ, `self` tự trả, `unknown` chưa xác định; nhãn do nguồn/người dùng đã kiểm tra. |
| `LOAIDONVITHUHO` | Không | `bank`, `wallet`, `other`, `unknown`. |
| `DONVITHUHO` | Không | Tên đơn vị thu hộ, không dùng thay mã khách hàng. |

Đặt tiêu đề trên một dòng và mỗi giao dịch trên một dòng bên dưới. Không gộp ô tiêu đề; tránh hai cột có cùng tiêu đề tài chính. Có thể đổi thứ tự cột và tạo nhiều sheet theo cùng mẫu. Mẫu tải xuống chỉ có tiêu đề và chú thích trên ô tiêu đề, không có giao dịch giả.

Nội dung cần giữ nguyên, kể cả mã dài và số 0 đầu. Mã khách hàng, hợp đồng, tài khoản và tham chiếu nên được định dạng **Text trước khi dán** vào Excel. Nếu Excel đã làm mất số 0 hoặc làm tròn mã dài, ứng dụng không thể dựng lại giá trị ban đầu.

Tệp có cột `SOTIEN`/số tiền có dấu vẫn được nhận diện: số dương có dấu `+` là ghi có, số âm là ghi nợ. Số dương không có dấu và không có tiêu đề ghi có rõ ràng cần duyệt thủ công; không tự coi là tiền nhận vào. Mẫu dùng `GHICO/GHINO` để tránh sự không rõ ràng này.

Ngày/số tiền sai hoặc chưa rõ chiều giao dịch vẫn đi vào kiểm tra thủ công 0%. Kỳ thanh toán đọc riêng từ nội dung; không lấy tháng của `NGAY` để thay thế kỳ thiếu.

## Bố cục chuẩn cho học

Tải **tệp mẫu đã xác nhận** ở khu vực Học dữ liệu mới hoặc Hướng dẫn sử dụng. Endpoint: `/templates/confirmed.xlsx`.

| Tiêu đề | Bắt buộc | Nội dung |
| --- | --- | --- |
| `IDKH` | Có | Mã khách hàng đã được kiểm tra; định dạng Text. `ko` được bỏ qua. |
| `TENKH` | Không | Tên khách hàng do người dùng xác nhận; nếu trống, không suy tên chuẩn mới từ NER. Hồ sơ mới thiếu tên có thể dùng mã làm tên tạm. |
| `NOIDUNG` | Có | Giao dịch nguyên văn thuộc đúng mã khách hàng được xác nhận. |
| `NGAY` | Không | Ngày giờ chuyển tiền để theo dõi lần học; không phải kỳ hóa đơn. |
| `SOTIEN` | Không | Số tiền không âm; để trống thì 0. |
| `NGANHANG` | Không | Ngân hàng/kênh trả tiền; nếu trống dùng tên sheet. |
| `REFERENCE` | Không | Tham chiếu ngân hàng, dạng Text. |
| `KIEUTHANHTOAN` | Không | `proxy`, `self`, `unknown`; để trống dùng loại mặc định của mẫu đã được admin xác nhận nếu có. |
| `LOAIDONVITHUHO` | Không | `bank`, `wallet`, `other`, `unknown`. |
| `DONVITHUHO` | Không | Tên ngân hàng/ví/dịch vụ thu hộ. |

Trong luồng học, NER có thể thêm một tên làm bí danh cho **IDKH đã xác nhận**, không tự suy IDKH hoặc thay tên chuẩn. Có nhiều tên đáng tin thì không tự gắn tất cả vào một khách hàng. Ngày/kỳ tiếp tục được bỏ khỏi đặc trưng so khớp như trước.

Nếu nội dung ghi rõ mã khách hàng khác IDKH, nhiều mã khách hàng, hoặc hợp đồng đã thuộc hồ sơ khác, dòng bị từ chối học và xuất hiện trong báo cáo lỗi. Kiểm tra tệp hoặc sửa kiến thức liên quan; không bỏ/cắt mã số để ép học thành công. Các dòng khác tiếp tục được xử lý.

## Nhận diện tổng quát và thông báo lỗi bố cục

Hệ thống tìm tiêu đề trong 100 dòng đầu, hỗ trợ tên cột Việt/Anh, có hoặc không dấu, dấu phân cách và một số viết tắt. Hai dòng tiêu đề liền nhau được kết hợp khi đều là nội dung tiêu đề và dòng sau bổ sung vai trò nhận diện được. Không dùng dữ liệu số/ngày giao dịch làm tiêu đề. Bố cục cũ đã hỗ trợ tiếp tục được đọc.

Hai cột tài chính hoặc định danh có cùng mức ưu tiên tiêu đề khiến bố cục không rõ ràng; hệ thống không tự chọn cột đầu tiên. Trong **Tác vụ xử lý**, sheet bị bỏ qua hiển thị lý do, các cột chưa tìm được hoặc trùng vai trò, và nút tải mẫu đúng luồng.

- Sheet tổng hợp hoặc hồ sơ không có giao dịch có thể bỏ qua, không cần chuyển sang mẫu.
- Sheet giao dịch chưa nhận diện được: chép dữ liệu sang mẫu chuẩn, giữ tên sheet khi cần, kiểm tra số tiền và nội dung rồi tải lại.
- Sheet đã gán IDKH dùng **Học dữ liệu mới**; sheet chưa gán nhãn dùng **Đối soát**.
- Các sheet phù hợp vẫn xử lý; chỉ khi không có sheet phù hợp tác vụ mới dừng với hướng dẫn sửa bố cục.

Danh sách chuẩn cùng chú thích có tại API `/layouts`. Chuẩn dùng chung cho UI, file mẫu và tài liệu.

## Xuất Excel và CSV

| Nút | Tệp nhận được |
| --- | --- |
| **Tải Excel** | `.xlsx`, chia kết quả theo tên/thứ tự sheet của tệp gốc; tiêu đề tiếng Việt. |
| **CSV theo sheet (ZIP)** | `.zip`, mỗi sheet một `.csv` UTF-8 có BOM, tên tệp lấy từ tên sheet. |
| **CSV đã học (ZIP)** | `.zip`, mỗi sheet một CSV học; chỉ có dòng đã xác nhận và học xong. Giải nén rồi nhập từng CSV khi chuyển kiến thức. |

Excel kết quả dùng các cột phục vụ đối soát, không sao chép định dạng/bố cục ngân hàng gốc. `Dòng gốc` và `Sheet gốc` giữ nguồn để tra lại, không phải chỉ số dòng của tệp xuất. Khách hàng đề xuất và khách hàng đã xác nhận là hai cột riêng. Kỳ hóa đơn cũng tách khỏi thời gian chuyển khoản.

Sheet chưa xử lý, trống hoặc không có dòng đã học vẫn có tiêu đề và không có giao dịch; xem lý do trong tác vụ. Với tác vụ đang chạy hoặc đã dừng, chỉ xuất phần kết quả đã lưu. Bộ lọc/phân trang trên màn hình không giới hạn tệp xuất: xuất toàn bộ kết quả của **tệp đang xem**.

Tên sheet Excel giữ nguyên nếu hợp lệ; tên vượt giới hạn, chứa ký tự không hợp lệ hoặc trùng sẽ được sửa an toàn. Tên sheet gốc vẫn có trong cột dữ liệu. Tên CSV cũng xử lý ký tự không hợp lệ Windows và trùng tên, không thay nội dung/số bên trong.

Mã khách hàng, tham chiếu và nội dung được ghi dưới dạng chuỗi trong Excel; không chuyển mã sang số hay công thức. CSV thêm dấu bảo vệ cho chuỗi bắt đầu như công thức; CSV học giữ `NOIDUNG_GOC_B64` để phục hồi chính xác nội dung khi nhập lại. CSV học giữ 9 cột cũ và thêm 3 cột tùy chọn `KIEUTHANHTOAN`, `LOAIDONVITHUHO`, `DONVITHUHO`; tệp cũ thiếu các cột này vẫn nhập được. CSV/Excel kết quả có cột mẫu chung, số khách hàng dùng mẫu và loại/đơn vị thu hộ. Loại chưa xác nhận giữ unknown, không tự suy self từ tên sheet. Xem [mẫu chung và thu hộ/tự trả](shared-payment-templates.md).

Xuất dùng tệp tạm và ghi theo dòng để giới hạn RAM. File được đóng trước khi tải/dọn trên Windows; file tạm được dọn sau khi tải xong. Excel có giới hạn độ dài ô/số dòng, nên nếu vượt giới hạn hệ thống báo tải CSV theo sheet để giữ đầy đủ dữ liệu, không tự cắt nội dung.

API kết quả: `/export/{job_id}/results.xlsx`, `/export/{job_id}/csv.zip`, `/export/{job_id}/learning.zip`. `/export/{job_id}` cũng trả ZIP CSV theo sheet. Endpoint CSV học gộp cũ `/export/{job_id}/learning.csv` được giữ cho tích hợp cũ, không dùng trên UI.
