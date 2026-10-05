# Bố cục Excel và xuất kết quả theo sheet

Hệ thống vẫn tự nhận diện từng sheet theo tiêu đề, không phụ thuộc ngân hàng hoặc vị trí cột. Bố cục chuẩn là cách sửa tệp khi nhận diện chưa thành công, không bắt buộc chuyển mọi tệp ngân hàng sang một mẫu duy nhất.

## Bố cục chuẩn cho đối soát

Bấm **mẫu bố cục** ở khu vực đối soát để xem ngay trên UI. Mẫu chỉ hiển thị tiêu đề và một dòng minh họa, không có nút hoặc API tải mẫu. Đối soát vẫn nhận tệp Excel `.xlsx`.

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

Đặt tiêu đề trên một dòng và mỗi giao dịch trên một dòng bên dưới. Không gộp ô tiêu đề; tránh hai cột có cùng tiêu đề tài chính. Có thể đổi thứ tự cột và tạo nhiều sheet theo cùng mẫu. Dòng minh họa trong màn hình mẫu chỉ để xem, không được nhập hay học tự động.

Nội dung cần giữ nguyên, kể cả mã dài và số 0 đầu. Mã khách hàng, hợp đồng, tài khoản và tham chiếu nên được định dạng **Text trước khi dán** vào Excel. Nếu Excel đã làm mất số 0 hoặc làm tròn mã dài, ứng dụng không thể dựng lại giá trị ban đầu.

Tệp có cột `SOTIEN`/số tiền có dấu vẫn được nhận diện: số dương có dấu `+` là ghi có, số âm là ghi nợ. Số dương không có dấu và không có tiêu đề ghi có rõ ràng cần duyệt thủ công; không tự coi là tiền nhận vào. Mẫu dùng `GHICO/GHINO` để tránh sự không rõ ràng này.

Ngày/số tiền sai hoặc chưa rõ chiều giao dịch vẫn đi vào kiểm tra thủ công 0%. Kỳ thanh toán đọc riêng từ nội dung; không lấy tháng của `NGAY` để thay thế kỳ thiếu.

## Bố cục chuẩn cho học

Bấm **mẫu bố cục** ở khu vực Học dữ liệu mới để xem tiêu đề CSV và một dòng minh họa ngay trên UI. Nhập được Excel `.xlsx` hoặc CSV đã xác nhận. Không cần tải tệp mẫu.

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

Trong luồng học, NER có thể thêm một tên làm bí danh cho **IDKH đã xác nhận**, không tự suy IDKH hoặc thay tên chuẩn. Có nhiều tên đáng tin thì không tự gắn tất cả vào một khách hàng. Với nội dung có nhiều IDKH, không tự gắn tên trích được vào các hồ sơ; dùng `TENKH` đã kiểm tra cho từng IDKH. Ngày/kỳ tiếp tục được bỏ khỏi đặc trưng so khớp như trước.

Một giao dịch có thể thanh toán cho nhiều khách hàng. Nội dung nhiều IDKH được học bình thường khi mã xác nhận nằm trong danh sách đó. Mỗi dòng có **một IDKH đã xác nhận**; để học cho hai khách hàng, lặp lại nguyên văn `NOIDUNG` ở hai dòng với `IDKH`/`TENKH` tương ứng. Không ghép hai mã vào một giá trị IDKH, không tự chia số tiền, không tự học các mã chưa được xác nhận.

Ví dụ hai dòng sau tạo hai liên kết khách hàng với cùng mẫu; toàn bộ số và thứ tự trong nội dung được giữ:

```csv
IDKH,TENKH,NOIDUNG
001234,Nguyễn Văn A,"TT tiền nước Ma KH-001234,005678 kỳ 8/2026"
005678,Trần Thị B,"TT tiền nước Ma KH-001234,005678 kỳ 8/2026"
```

Mã xác nhận hoàn toàn khác các IDKH ghi rõ vẫn được báo lỗi riêng dòng để kiểm tra; các dòng khác tiếp tục xử lý. Một HD được phép liên kết nhiều IDKH; không từ chối chỉ vì HD đã xuất hiện ở khách hàng khác. Tệp đã nhập có lỗi có thể được tải lại để học các dòng đã sửa; các receipt giữ những dòng đã học khỏi bị học trùng.

## Nhận diện tổng quát và thông báo lỗi bố cục

Hệ thống tìm tiêu đề trong 100 dòng đầu, hỗ trợ tên cột Việt/Anh, có hoặc không dấu, dấu phân cách và một số viết tắt. Hai dòng tiêu đề liền nhau được kết hợp khi đều là nội dung tiêu đề và dòng sau bổ sung vai trò nhận diện được. Không dùng dữ liệu số/ngày giao dịch làm tiêu đề. Bố cục cũ đã hỗ trợ tiếp tục được đọc.

Cột mã khách hàng nhận các biến thể `IDKH`, `ID KH`, `MaKH`, `Mã KH`, `Mã khách hàng`, `Mã số KH`, `Customer ID`, `Customer Code`; dấu cách/gạch dưới/gạch ngang, hoa thường và dấu tiếng Việt được chuẩn hóa. Cột `ID`/`Mã ID` chỉ được nhận là mã khách hàng khi cùng hàng có tên KH rõ ràng và nội dung giao dịch. Với hai cột mã KH khác nhau, không tự chọn một cột dù một cột tên IDKH và cột còn lại MaKH. CSV nhập dùng cùng quy tắc nhận diện; các trường gốc/mã hóa nội dung từ CSV cũ vẫn được hỗ trợ.

Hai cột tài chính hoặc định danh có cùng mức ưu tiên tiêu đề khiến bố cục không rõ ràng; hệ thống không tự chọn cột đầu tiên. Trong **Tác vụ xử lý**, sheet bị bỏ qua hiển thị lý do ngắn và link xem mẫu bố cục của đúng luồng. Các chi tiết cột thiếu/trùng vẫn có trong dữ liệu chẩn đoán của tác vụ.

- Sheet tổng hợp hoặc hồ sơ không có giao dịch có thể bỏ qua, không cần chuyển sang mẫu.
- Sheet giao dịch chưa nhận diện được: chép dữ liệu sang mẫu chuẩn, giữ tên sheet khi cần, kiểm tra số tiền và nội dung rồi tải lại.
- Sheet đã gán IDKH dùng **Học dữ liệu mới**; sheet chưa gán nhãn dùng **Đối soát**.
- Các sheet phù hợp vẫn xử lý; chỉ khi không có sheet phù hợp tác vụ mới dừng với hướng dẫn sửa bố cục.

Danh sách chuẩn có tại API `/layouts`, dùng chung cho màn hình xem bố cục và tài liệu. UI chỉ ghi một câu hướng dẫn kèm link; bố cục mở trong hộp xem trên trang.

## Xuất Excel và CSV

| Nút | Tệp nhận được |
| --- | --- |
| **Tải Excel** | `.xlsx`, chia kết quả theo tên/thứ tự sheet của tệp gốc; tiêu đề tiếng Việt. |
| **CSV theo sheet (ZIP)** | `.zip`, mỗi sheet một `.csv` UTF-8 có BOM, tên tệp lấy từ tên sheet. |

Excel kết quả dùng các cột phục vụ đối soát, không sao chép định dạng/bố cục ngân hàng gốc. `Dòng gốc` và `Sheet gốc` giữ nguồn để tra lại, không phải chỉ số dòng của tệp xuất. Khách hàng đề xuất và khách hàng đã xác nhận là hai cột riêng. Kỳ hóa đơn cũng tách khỏi thời gian chuyển khoản.

Sheet chưa xử lý hoặc trống vẫn có tiêu đề và không có giao dịch; xem lý do trong tác vụ. Với tác vụ đang chạy hoặc đã dừng, chỉ xuất phần kết quả đã lưu. Bộ lọc/phân trang trên màn hình không giới hạn tệp xuất: xuất toàn bộ kết quả của **tệp đang xem**.

Tên sheet Excel giữ nguyên nếu hợp lệ; tên vượt giới hạn, chứa ký tự không hợp lệ hoặc trùng sẽ được sửa an toàn. Tên sheet gốc vẫn có trong cột dữ liệu. Tên CSV cũng xử lý ký tự không hợp lệ Windows và trùng tên, không thay nội dung/số bên trong.

Mã khách hàng, tham chiếu và nội dung được ghi dưới dạng chuỗi trong Excel; không chuyển mã sang số hay công thức. CSV thêm dấu bảo vệ cho chuỗi bắt đầu như công thức. CSV/Excel kết quả có cột mẫu chung, số khách hàng dùng mẫu và loại/đơn vị thu hộ. Loại chưa xác nhận giữ unknown. Không xuất riêng dữ liệu đã học; chức năng nhập CSV đã xác nhận vẫn hoạt động, bao gồm tệp CSV cũ có trường nội dung gốc. Xem [mẫu chung và thu hộ/tự trả](shared-payment-templates.md).

Xuất dùng tệp tạm và ghi theo dòng để giới hạn RAM. File được đóng trước khi tải/dọn trên Windows; file tạm được dọn sau khi tải xong. Excel có giới hạn độ dài ô/số dòng, nên nếu vượt giới hạn hệ thống báo tải CSV theo sheet để giữ đầy đủ dữ liệu, không tự cắt nội dung.

API kết quả: `/export/{job_id}/results.xlsx`, `/export/{job_id}/csv.zip`. `/export/{job_id}` cũng trả ZIP CSV theo sheet. Các endpoint tải mẫu bố cục và xuất dữ liệu đã học đã được xóa.
