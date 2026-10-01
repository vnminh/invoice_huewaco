# Hướng dẫn quản trị đối soát HueWACO

Trang quản trị phục vụ hai việc: đối soát tệp ngân hàng và quản lý kiến thức đã được con người xác nhận. Dữ liệu mẫu theo tháng trong repository chỉ dành cho phát triển; giao diện không phụ thuộc một tháng cụ thể.

## 1. Các khu vực trên giao diện

| Khu vực | Dùng để làm gì |
| --- | --- |
| Đối soát giao dịch | Tải Excel ngân hàng chưa lọc, xem kết quả theo từng tệp, tìm giao dịch, duyệt và tải CSV. |
| Học dữ liệu mới | Nhập Excel đã xác nhận hoặc CSV đã học; thêm khách hàng hoặc một mẫu mới. |
| Quản lý kiến thức | Tìm khách hàng, xem tất cả mẫu của khách hàng, sửa/xóa hồ sơ hoặc mẫu; xem các bảng kiến thức hỗ trợ. |
| Tác vụ xử lý | Theo dõi nhập/đối soát, dừng tác vụ, mở kết quả, tải CSV và xóa tệp làm việc. |
| Hướng dẫn sử dụng | Hướng dẫn ngắn ngay trên trang. |

Các con số đầu trang là số khách hàng và mẫu trong kho kiến thức. Trạng thái kết nối chỉ báo dịch vụ sẵn sàng hay gián đoạn; thông tin kỹ thuật về mã hóa ngữ nghĩa không xuất hiện trong luồng đối soát.

## 2. Đối soát một tệp

1. Chọn Excel `.xlsx` ngân hàng chưa lọc, rồi bấm **Bắt đầu đối soát**. Hệ thống quét tất cả sheet và đọc các sheet giao dịch nhận diện được, không giới hạn BIDV.
2. Kết quả xuất hiện sau mỗi đợt đã xử lý. Có thể vào **Tác vụ xử lý** để theo dõi hoặc dừng.
3. Tại **Tệp đang xem**, chọn đúng tệp cần làm việc. Kết quả của các tệp khác không bị trộn vào tệp đang chọn.
4. Lọc theo đề xuất và trạng thái duyệt. Ô tìm kiếm nhận nội dung, mã/tên khách hàng, sheet, kênh và tham chiếu, không phân biệt hoa/thường và dấu tiếng Việt.
5. Chọn **Kiểm tra** trên một dòng. Màn hình hiển thị nội dung hiện tại, mẫu trong lịch sử, nguồn mẫu và lý do so khớp. Mở phần đối chiếu số để xem giá trị, vai trò và thứ tự.

| Đề xuất | Ý nghĩa |
| --- | --- |
| Có thể xác nhận | Có đủ bằng chứng để đề xuất khách hàng; vẫn cần người dùng xác nhận trước khi học. |
| Cần duyệt | Có ứng viên nhưng chưa đủ điều kiện ghép tự động. |
| Kiểm tra thủ công | Chưa biết khách hàng hoặc thiếu bằng chứng; điểm 0%. |
| Không ghép | Không phải khoản ghi có phù hợp, hoặc có bằng chứng loại trừ. |

**Điểm so khớp là điểm xếp hạng bằng chứng, không phải xác suất đã được hiệu chuẩn.**

### Kỳ thanh toán và thời gian chuyển khoản

Cột **Kỳ thanh toán** trả lời hóa đơn nước thuộc tháng nào, lấy từ **nội dung chuyển tiền**, không lấy tháng của cột ngày giao dịch. Khi mở **Kiểm tra**, hệ thống hiển thị kỳ, đoạn nội dung dùng để nhận diện, và thời gian chuyển khoản ở hai mục riêng.

| Nội dung | Kỳ thanh toán |
| --- | --- |
| `TT tiền nước tháng 07/2026, chuyển khoản ngày 11/09/2026 09:30` | `07/2026` |
| `Tiền nước kỳ 7 năm 2026` hoặc `T7/26` | `07/2026` |
| `TT tiền nước 07/2026` | `07/2026` |
| `Tiền nước tháng 6,7/2026` hoặc `tháng 6-7/2026` | `06/2026, 07/2026` |
| `Tiền nước từ tháng 11/2025 đến tháng 2/2026` | `11/2025, 12/2025, 01/2026, 02/2026` |
| `TT tiền nước ngày 11/09/2026 09:30` hoặc chỉ ghi `tháng 7` | `Chưa xác định` |

Hỗ trợ dấu `/`, `-`, `.`, nhãn tháng/kỳ/T và cách ghi `tháng 072026`. Năm hai chữ số chỉ nhận với nhãn tháng/kỳ/T và được hiểu là `20xx`. Không lấy ngày đầy đủ, thời điểm giao dịch, hạn nộp hoặc giá trị của mã khách hàng/hợp đồng/tài khoản/tham chiếu làm kỳ. Tháng/năm không có nhãn cần ở gần cụm thanh toán tiền nước; hậu tố kỳ `@@MM/YYYY` của nội dung BIDV O@L cũng được nhận diện. Không suy năm bị thiếu từ ngày chuyển khoản hoặc ngày hiện tại.

Nếu có nhiều kỳ rõ ràng, hệ thống liệt kê các kỳ và bỏ trùng. Khoảng có cả hai đầu tháng/năm được mở rộng tối đa 24 tháng; khoảng ngược hoặc dài hơn được để chưa xác định. Nội dung không đủ căn cứ để xác định tháng/năm hiển thị **Chưa xác định**; điều này không tự thay đổi đề xuất khách hàng hoặc điểm so khớp.

CSV kết quả thêm cột `payment_period`; cột `date` vẫn là ngày giờ chuyển khoản. Có thể tìm trong bảng bằng kỳ chuẩn hóa, ví dụ `07/2026`. Kết quả làm việc cũ được bổ sung kỳ khi đọc lại từ nội dung, không cần đối soát lại hoặc chạy SQL.

**Chỉ luồng đối soát có thông tin này.** Luồng học tiếp tục bỏ ngày/tháng theo bộ chuẩn hóa hiện tại; không đưa kỳ vào embedding, fingerprint, numeric slots hoặc điểm so khớp. CSV đã học giữ nguyên các cột và logic. Kỳ được lưu trong tệp kết quả làm việc, không thêm cột/bảng PostgreSQL.

### Nhiều sheet và các bố cục ngân hàng

Mỗi sheet được nhận diện theo tiêu đề cột: ngày, nội dung/mô tả/diễn giải, số tiền ghi có/ghi nợ hoặc số tiền có dấu, tham chiếu. Không áp dụng cột cố định của BIDV cho ngân hàng khác. Hàng tiêu đề được tìm trong 100 dòng đầu.

Các bố cục có tiêu đề trong tệp mẫu gồm BIDV/chi nhánh, Vietcombank, Công thương, Quân đội, Nông nghiệp, Eximbank, SHB, Hàng Hải, ACB, VP Bank và Vikki. Tên sheet được giữ làm kênh mặc định; tệp học có cột ngân hàng/hình thức thì ưu tiên giá trị cột đó.

Sheet Sacombank không có hàng tiêu đề trong tệp mẫu được đọc theo bố cục C/E/M/Q/T. Do chưa có tiêu đề xác nhận chiều ghi nợ/ghi có, các dòng được đưa về **0% / kiểm tra thủ công**; kiểm tra cột Q/T trong tệp gốc trước khi duyệt. Có thể bổ sung hàng tiêu đề chuẩn vào bản nhập để xác định chiều giao dịch.

Sheet tổng hợp/hồ sơ khách hàng không có nội dung giao dịch và ngày/số tiền phù hợp không được tự chuyển thành giao dịch. Danh sách sheet chưa xử lý và lý do xuất hiện tại **Tác vụ xử lý**, cùng số dòng từng sheet. Không tìm thấy sheet phù hợp sẽ báo lỗi. Dữ liệu có số tiền/ngày không đọc được hoặc chưa rõ chiều ghi có/ghi nợ cần kiểm tra thủ công, không tự ghép.

Đối soát xử lý lần lượt từng sheet và cập nhật kết quả sau mỗi nhóm tối đa 32 dòng (hoặc nhỏ hơn nếu batch_size được đặt nhỏ hơn). Tác vụ hiển thị sheet/số dòng đang đối soát ngay trước khi xử lý nhóm. Số đã xử lý chỉ tăng khi kết quả đã lưu, nên có thể vẫn là 0 trong lúc xử lý nhóm đầu; các sheet chưa đến lượt hiển thị **Chưa có kết quả**. Sheet bị bỏ qua được báo riêng, không có nghĩa cả tác vụ bị lỗi.

Hai sheet có cùng số dòng Excel vẫn là hai giao dịch riêng. Kết quả hiển thị tên sheet; CSV kết quả giữ `sheet`, `payer`, `reference`, `debit`, `validation_errors`, `input_file` bên cạnh các cột có sẵn. Cột nguồn mẫu lịch sử và tệp đầu vào là hai thông tin riêng.

Chân trang dạng `Telex:`, `Swift:`, `Website:`, `Contact center:`, `Trang 1 / 1` hoặc `Page 1 / 1` được bỏ qua khi không có tham chiếu, không có ô số tiền và không có ngày hợp lệ. Những dòng này là thông tin in sao kê, không phải giao dịch. Dòng có bằng chứng giao dịch vẫn được giữ để xử lý/kiểm tra thủ công.

Nếu một dòng phát sinh lỗi khi đọc/chuyển đổi hoặc đối soát, hệ thống bỏ qua dòng đó và tiếp tục các dòng khác. Trong **Tác vụ xử lý**, mở **dòng lỗi đã bỏ qua** để xem sheet, số dòng và nguyên nhân; chọn **Tải danh sách dòng lỗi CSV** để lấy toàn bộ danh sách. Số dòng là số dòng trong tệp gốc, không phải ID giao dịch hoặc vị trí trong bảng kết quả. Nếu XML của Excel mất chỉ số dòng, báo cáo giữ vị trí bản ghi XML riêng, không tự đoán số dòng Excel.

Những dòng vẫn đọc được nhưng ngày/số tiền/chiều giao dịch chưa rõ tiếp tục xuất hiện ở **kiểm tra thủ công 0%** như trước. Chỉ dòng thực sự phát sinh ngoại lệ mới bị bỏ qua. Lỗi toàn tệp (ZIP/XML hỏng, không nhận diện được bố cục), cấu hình kiến thức không phù hợp hoặc mất kết nối database vẫn dừng tác vụ.

### Xóa hiển thị và xóa tệp khác nhau

**Xóa hiển thị** chỉ làm trống bảng trên màn hình. Tệp, các xác nhận và kiến thức vẫn được giữ. Tự làm mới tiến trình không làm bảng xuất hiện lại; bấm **Làm mới**, đổi bộ lọc hoặc bắt đầu tệp đối soát mới để xem kết quả.

**Xóa tệp làm việc** trong khu vực tác vụ xóa các tệp kết quả, tiến trình và xác nhận của tệp đó. Kiến thức đã học vẫn ở PostgreSQL. Tải CSV trước khi xóa nếu cần giữ kết quả để bàn giao.

## 3. Xác nhận có tự học không?

**Có: bấm Xác nhận & học sẽ học ngay trong cùng yêu cầu xử lý.** Không cần nút cập nhật kiến thức riêng sau đó.

- Chỉ mở một dòng, chọn checkbox để đưa vào danh sách hoặc sửa ô mã/tên **chưa** làm hệ thống học.
- Khi đã kiểm tra, nhập/chọn mã khách hàng và tên hoặc bí danh đúng, rồi bấm **Xác nhận & học**.
- Với khách hàng mới, hệ thống thêm hồ sơ rồi học nội dung giao dịch cho khách hàng đó.
- Với khách hàng đã có, hệ thống bổ sung mẫu mới hoặc cập nhật mẫu phù hợp. Các mẫu khác vẫn được giữ.
- Khi sửa đề xuất từ khách hàng A sang B, hệ thống học cho B và ghi nhận bằng chứng không ghép giao dịch này với A.
- Bấm **Không ghép khách hàng này** không tạo mẫu dương; nếu có khách hàng đã được đề xuất, hệ thống ghi nhận bằng chứng loại trừ.
- Dòng đã xác nhận không được sửa ngầm qua thao tác xác nhận lại. Sửa kiến thức đã học bằng chức năng quản lý mẫu/hồ sơ.

Nếu kết nối bị gián đoạn sau khi đã xác nhận, ý định xác nhận nằm trong tệp làm việc. Khi khởi động lại, hệ thống tiếp tục các xác nhận này. Có thể bấm **Tiếp tục học dữ liệu đã xác nhận** nếu cần. Dấu chống trùng bảo đảm thử lại không học hai lần.

### Xác nhận nhiều dòng

Chọn các dòng có khách hàng được đề xuất, rồi bấm **Xác nhận & học đã chọn** và xác nhận thao tác. Có thể chọn tối đa 100 dòng mỗi lần. Những dòng không có khách hàng phải được duyệt riêng để nhập đúng khách hàng.

Mỗi dòng được xử lý độc lập: lỗi ở một dòng không hủy những dòng đã xác nhận thành công. Màn hình thông báo số dòng đã học và số dòng còn cần kiểm tra.

## 4. Một khách hàng có nhiều mẫu

Ví dụ khách hàng `001234` có các nội dung:

```text
TT KH:001234 HD:700012 TIEN NUOC
CONG TY XYZ thanh toan tien nuoc KH:001234 HD:900045
TT KH:001234 HD:900045 TIEN NUOC
```

Đây có thể là ba mẫu của cùng khách hàng. Mẫu thứ nhất và thứ ba cùng dạng chữ nhưng khác hợp đồng; chúng được giữ riêng. Không thay hợp đồng `700012` bằng `900045` rồi coi là cùng một số.

Ngày/kỳ thanh toán và tham chiếu ngân hàng thay đổi không nhất thiết tạo mẫu mới. Hệ thống giữ nhiều giá trị theo đúng vị trí đối với những trường có thể biến đổi.

Trong **Quản lý kiến thức**:

1. Tìm khách hàng bằng mã hoặc tên.
2. Mở **Chi tiết**, xem số mẫu đã học và bấm **Xem mẫu đã học**.
3. Bấm **Thêm mẫu cho khách hàng này** nếu có một cách ghi mới. Mẫu cũ được giữ.
4. Bấm **Lưu mẫu đã sửa** chỉ khi nội dung cũ có sai sót. Hệ thống tính lại cấu trúc và thông tin tìm kiếm của mẫu đó. Nếu trùng mẫu đã có thì gộp vào mẫu tương ứng.

Sửa tên khách hàng cập nhật cả tên chuẩn hóa và thông tin tìm kiếm liên quan. Mã khách hàng không được thay đổi bằng thao tác sửa tên.

## 5. Học từ tệp đã xác nhận

### Excel

Chọn tệp đã lọc/đã xác nhận ở **Học dữ liệu mới**, đánh dấu đã kiểm tra nhãn rồi bấm **Nhập & học dữ liệu**. Hệ thống đọc mọi sheet có cột IDKH, gồm các kênh trả tiền có trong tệp. Bỏ qua `ko`; các nhãn chưa phân giải như `ht/pd/th` không trở thành hồ sơ khách hàng.

Nhập được lưu theo từng đợt. Dừng nhập sẽ giữ những đợt đã hoàn tất và hủy đợt đang xử lý. Tải lại cùng tệp sẽ đọc lại tệp, bỏ qua những dòng đã học; không cần xóa kiến thức để tiếp tục.

Mỗi dòng học có savepoint riêng. Nếu một dòng có dữ liệu không hợp lệ hoặc lỗi ràng buộc khi ghi PostgreSQL, các thay đổi của riêng dòng đó được rollback và báo vào danh sách lỗi; những dòng hợp lệ vẫn được commit theo đợt. Trạng thái **Hoàn tất · có dòng lỗi** có nghĩa tác vụ đã đọc xong, nhưng cần kiểm tra báo cáo trước khi xem tệp đã được học đầy đủ. Tệp từng có lỗi được phép nhập lại để thử các dòng lỗi; receipt ngăn học lại các dòng thành công. Sửa dữ liệu trong tệp nguồn rồi tải lại khi cần.

CSV học cũng bỏ qua từng dòng có lỗi dữ liệu/mã hóa nội dung và báo số dòng vật lý trong CSV (có tính hàng tiêu đề). Lỗi cấu trúc CSV hoặc mã hóa của cả tệp có thể khiến việc đọc tiếp không an toàn và vẫn dừng tác vụ.

### CSV đã học

**CSV đã học** chỉ chứa dòng đã xác nhận thành công; không chứa các đề xuất chưa duyệt. Có thể nhập lại CSV này ở mục học dữ liệu, hoặc chuyển sang một kho kiến thức khác.

| Cột | Nội dung |
| --- | --- |
| IDKH | Mã khách hàng dạng chuỗi, giữ số 0 đầu. Bắt buộc. |
| TENKH | Tên/bí danh đã xác nhận. |
| NOIDUNG | Nội dung chuyển tiền đầy đủ. Bắt buộc. |
| NGAY | Ngày giao dịch, nên dùng `YYYY-MM-DD` hoặc ngày ISO có giờ. |
| SOTIEN | Số không âm, không có phân cách hàng nghìn; dấu chấm là phần thập phân. |
| NGANHANG | Ngân hàng/kênh trả tiền; nếu trống dùng SHEET, hoặc BIDV để tương thích CSV cũ không có cả hai cột. |
| NOIDUNG_GOC_B64 | Bản mã hóa nội dung gốc để bảo toàn nội dung khi xuất CSV. Không cần tự tạo. |
| SHEET | Tên sheet nguồn; tùy chọn, được giữ khi xuất CSV đã học. |
| REFERENCE | Tham chiếu giao dịch nguồn; tùy chọn. |

Tệp dùng UTF-8; CSV tải từ hệ thống có BOM để Excel đọc tiếng Việt. Khi chỉnh CSV trong Excel, định dạng cột IDKH là **Text** để không mất số 0 đầu. Nếu sửa NOIDUNG, hệ thống dùng nội dung hiển thị mới; bản mã hóa chỉ dùng để khôi phục chính xác nội dung xuất chưa bị sửa.

CSV không có worksheet; hệ thống đọc mọi dòng của mọi ngân hàng/kênh trong CSV, không lọc riêng BIDV. CSV kết quả và CSV đã học của một tác vụ bao gồm các sheet đã xử lý trong tệp đó.

Nhập lại CSV có cùng nội dung/khách hàng/ngày/số tiền/kênh đã xác nhận không tăng thêm lần học. Khi nhập sang kho khác, các dòng được học vào kho đó.

## 6. Quản lý tác vụ và dữ liệu

Dừng được cả nhập kiến thức, đối soát và tác vụ tiếp tục xác nhận. Phần đang chạy dừng tại điểm kiểm tra an toàn, không ngắt thread giữa lúc ghi kiến thức. Tệp đã xử lý một phần vẫn có thể tải phần kết quả đã hoàn tất.

Sửa/xóa kiến thức bị chặn khi còn tác vụ đang chạy hoặc xác nhận đang chờ hoàn tất. Điều này tránh sửa/xóa một mẫu trong khi mẫu đó đang được dùng hoặc được học.

Xóa khách hàng xóa hồ sơ cùng các mẫu, số và liên kết trả tiền của khách hàng. Xóa mẫu chỉ xóa mẫu đó và dữ liệu tìm kiếm liên quan. Những kết quả/CSV đã tạo vẫn là bản ghi của lần xử lý trước; chúng không được tính lại ngầm khi sửa kiến thức.

Các bảng phụ trợ chỉ xem. Dùng thao tác thêm/sửa/xóa hồ sơ và mẫu để hệ thống cập nhật các phần liên quan đồng bộ; không cần mở công cụ database.
