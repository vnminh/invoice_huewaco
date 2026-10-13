# HueWACO · Đối soát và quản lý kiến thức

Một trang HTML tiếng Việt và backend FastAPI để lọc giao dịch ngân hàng từ mọi sheet nhận diện được trong tệp, đối chiếu với lịch sử đã xác nhận và quản lý kho kiến thức. Giao diện quản trị theo màu xanh/chuyển sắc và logo của [website HueWACO](https://huewaco.com.vn/); tài nguyên giao diện được phục vụ tại chỗ.

Một mẫu nội dung chung có thể thuộc nhiều khách hàng; mỗi khách hàng có nhiều mẫu. Số riêng được lưu theo từng liên kết, không gộp số của các khách hàng vào mẫu chung. Thêm mẫu mới giữ mẫu cũ; hợp đồng/mã quan trọng khác nhau được phân biệt ngay cả khi cùng nội dung chữ. Khi người dùng sửa khách hàng rồi **Xác nhận & học**, ứng dụng học ngay trong transaction PostgreSQL. Dự đoán hoặc chọn checkbox không tự học. Mã khách hàng chưa biết được đưa về 0% và kiểm tra thủ công; dòng có thông tin liên quan vẫn giữ gợi ý dù điểm thấp.

PostgreSQL chỉ lưu **kiến thức đã học**. Kết quả đối soát, trạng thái duyệt và tác vụ lưu trong tệp working; có thể tải CSV và dọn qua giao diện. Không cần mở database để quản lý khách hàng/mẫu hoặc dừng luồng xử lý.

## Cài đặt

Chạy trên Linux hoặc Windows, với Python 3.12+ và PostgreSQL 15+ có pgvector cài trên máy chủ. Chạy lệnh từ thư mục project; không cần activate virtualenv.

**Linux:**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install 'torch>=2.4,<3' --index-url https://download.pytorch.org/whl/cpu
.venv/bin/python -m pip install -r requirements.txt
test -f .env || cp .env.example .env
```

**Windows (PowerShell):**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install "torch>=2.4,<3" --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Virtualenv phải được tạo lại trên từng hệ điều hành; không chép `.venv` từ Linux sang Windows. Với repo đã cài, chạy lại lệnh cài `requirements.txt` để có dependency cập nhật, gồm `XlsxWriter` cho xuất Excel.

Sửa DATABASE_URL trong .env. Quản trị viên tự tạo database/login và chạy SQL; backend không tự khởi tạo hoặc migrate PostgreSQL.

**Database mới**: chỉ chạy [sql/create_current.sql](sql/create_current.sql) để tạo thẳng **17 bảng**. **Kho đã qua 007**: chạy [008_name_positions.sql](sql/migrations/008_name_positions.sql) rồi `scripts/reindex_knowledge.py` (hoặc nhập lại tệp FN) để học vị trí tên. **Kho đã qua 006**: chạy [007_recurring_memory.sql](sql/migrations/007_recurring_memory.sql), rồi `scripts/rebuild_recurring_memory.py` khi backend dừng. **Kho cũ đến 004**: chạy lần lượt 005, 006, 007 rồi `scripts/reindex_knowledge.py`; công cụ này cũng bổ sung bộ nhớ nội dung đã xác nhận. Kho v7/005 chạy 006, 007 rồi rebuild. Nhập lại FN/CSV để phục hồi các biến thể chưa có ví dụ trong kho cũ. Xem [hướng dẫn Windows/Linux](docs/migration-and-storage.md). Backend không tự chạy SQL.

Ứng dụng và script CLI tự đọc `.env` ở gốc project bằng UTF-8 (có thể có BOM). Biến môi trường đã đặt trong terminal/service được ưu tiên hơn `.env`; không cần `source .env` hay tự gán từng biến trong PowerShell.

Khởi động trên **Linux**:

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Khởi động trên **Windows (PowerShell)**:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Dùng chung qua mạng/proxy: cấu hình `APP_ADMIN_USER` và `APP_ADMIN_PASSWORD` (ít nhất 16 ký tự), HTTPS, rồi khởi động lại. Chưa cấu hình tài khoản thì chỉ truy cập loopback trực tiếp. Xem [cơ chế tương tác và bảo vệ](docs/interactive-knowledge.md).

Mở **http://127.0.0.1:8000**; Schema API tại /openapi.json (cùng cơ chế đăng nhập). Chạy một worker; bên trong có **2 luồng nền, tối đa 2 jobs song song**. Các jobs còn lại chờ trong hàng đợi; mỗi job có tiến trình và nút dừng riêng. Không tăng `--workers` vì working files được quản lý trong một process.

## Giao diện và tài liệu

| Khu vực | Công việc |
| --- | --- |
| Đối soát | Tải Excel ngân hàng, lọc kết quả theo tệp, xem lịch sử, sửa/xác nhận từng dòng hoặc hàng loạt, xuất Excel và CSV theo sheet. |
| Học dữ liệu mới | Nhập Excel đã gán nhãn từ mọi sheet có IDKH hoặc CSV đã duyệt; dừng tác vụ; thêm khách hàng/mẫu. |
| Quản lý kiến thức | Xem mọi bảng ứng dụng, tìm kiếm/phân trang/chi tiết; thêm/sửa/xóa khách hàng và mẫu; gán thu hộ/tự trả cho mẫu hoặc từng liên kết. |
| Tác vụ xử lý | Tiến trình, dừng nhập/lọc, xem kết quả, xuất CSV, xóa tệp làm việc, tiếp tục xác nhận còn chờ. |
| Hướng dẫn | Quy trình thao tác cho người dùng. |

Tài liệu chi tiết:

- [Kiến thức tương tác và kết hợp nhiều nguồn](docs/interactive-knowledge.md): thao tác UI, nguồn bằng chứng, audit/undo, bảo vệ truy cập và lộ trình mở rộng.
- [Nhóm khách hàng gợi ý](docs/contextual-recommendations.md): thu hẹp theo ngân hàng, mẫu, tên và đặc điểm không ghi IDKH; tìm kiếm/phân trang để duyệt.
- [Benchmark tương tác](docs/interactive-benchmark.md): protocol, kết quả và giới hạn đo.

- [Nâng cấp độ tin cậy v7](docs/precision-upgrade.md): thay đổi, dữ liệu đã rà soát, giới hạn và cách đánh giá.
- [Hướng dẫn quản trị](docs/admin-guide.md): đối soát, manual, học sau xác nhận, nhiều mẫu, CSV.
- [Bố cục Excel và xuất theo sheet](docs/excel-layout-and-export.md): xem hai bố cục ngay trên UI, nhận diện tổng quát, Excel kết quả và ZIP CSV theo sheet.
- [Tìm kiếm, so khớp và học](docs/matching-and-learning.md): số theo thứ tự, retrieval, feature/trọng số, guard precision, chống trùng và phục hồi.
- [Mẫu chung và thu hộ/tự trả](docs/shared-payment-templates.md): quan hệ nhiều khách hàng, số riêng theo liên kết, gán loại thủ công trên web và SQL mới.
- [Nhận lại mẫu đã xác nhận](docs/recurring-recognition.md): giữ liên kết nhóm cũ và mẫu cá nhân mới, điều kiện tự ghép và nâng cấp.
- [Schema database](docs/database-schema.md): 17 bảng, quan hệ, trường, chỉ mục, cách lưu mẫu/hóa đơn/số và quyền quản lý.
- [Cài đặt, chuyển phiên bản và lưu trữ](docs/migration-and-storage.md): SQL tự chạy, CSV sao lưu cũ, working, restart và dọn kết quả.
- [Chạy trên Linux và Windows](docs/platform-setup.md): lệnh PowerShell, đường dẫn/cache, SQL và khác biệt thao tác file/đo RAM.

## Xử lý cốt lõi

Encoder **intfloat/multilingual-e5-small**, 384 chiều, hỗ trợ tiếng Việt và mặc định chạy CPU. Không dùng Ollama/LLM sinh nội dung cục bộ. Văn bản giữ dấu cho encoder; số/định danh kiểm tra riêng bằng quy tắc. Cache mô hình mặc định runtime/models.

NER **NlpHUST/ner-vietnamese-electra-base** trích tên trong đối soát và bổ sung bí danh khi học dữ liệu đã xác nhận. Một tên rõ ràng được gắn với IDKH đã xác nhận, ở mức tin cậy thấp hơn tên người dùng kiểm tra; nhiều tên không tự gộp vào một hồ sơ. Tên giúp tìm ứng viên, không tự xác lập mã khách hàng. Mô hình chỉ tải khi cần lần đầu; trường tên theo bố cục MB vẫn được đọc khi NER chưa sẵn sàng. Cài lại `requirements.txt` và khởi động lại dịch vụ sau khi cập nhật. Xem [cấu hình và giới hạn NER](docs/matching-and-learning.md).

Retrieval kết hợp posting theo mã/tên/người trả tiền/hợp đồng, full-text, trigram và pgvector HNSW bằng thứ hạng RRF; giữ nhiều khách hàng trong tập ứng viên và rerank tối đa 40 mẫu. Không dừng ở tài khoản/mã đầu tiên. Sau đó lấy mẫu tốt nhất mỗi khách hàng để tránh các mẫu cùng khách hàng cạnh tranh giả. Điểm hiển thị là điểm bằng chứng, không phải xác suất đã hiệu chuẩn.

IDKH luôn **6 chữ số**. HD thường là **hợp đồng**, nhưng có thể là IDKH ở vị trí của một mẫu thu hộ đã được xác nhận; TKThe là thẻ người trả tiền. Số giữ chuỗi đầy đủ, thứ tự, vai trò, định dạng và số 0 đầu; không cắt chuỗi dài. Chủ sở hữu định danh được kiểm tra trên toàn kho, độc lập với giới hạn ứng viên. Một HD có thể liên kết nhiều IDKH. IDKH rõ ràng và đã biết được ưu tiên trước HD; IDKH chưa biết vẫn trả thủ công 0%. HD dùng chung không đủ xác định khách hàng. Mã/số cơ sở chưa khớp đủ và token/tài khoản dùng chung không đủ để ghép tự động. Hai khách hàng có điểm đủ cao và chênh dưới `MATCH_MARGIN=0.08` phải duyệt. Nhãn ko được bỏ qua khi học.

Admin có thể gán **Thu hộ / Tự trả / Chưa xác định** ở mẫu chung, chỉnh ngoại lệ theo liên kết hoặc cập nhật toàn bộ mẫu sau tìm kiếm/lọc, kể cả các trang chưa hiển thị. Loại mẫu là mặc định khi học liên kết mới không có nhãn riêng. Kênh ví và một số bố cục thu hộ cố định cung cấp gợi ý proxy, có ghi rõ nguồn; nhãn người dùng/mẫu được ưu tiên và có thể chỉnh lại. Không tự coi unknown là self. Khi chưa có nhãn hoặc dùng mẫu chung/thu hộ, không ghép chỉ theo tên hay tài khoản trung gian.

Giới hạn upload 100 MB, XLSX giải nén tối đa 2 GB; đọc streaming, chia đợt và cache embedding có giới hạn. Dừng tại checkpoint, giữ các đợt đã commit. Knowledge ghi tên encoder/extractor để chặn trộn cấu hình không tương thích.

Học nhận diện các cột IDKH/MaKH/Mã KH/Mã khách hàng theo từng sheet. Cột ID đơn lẻ chỉ được coi là mã khách hàng khi có cột tên khách hàng rõ ràng và nội dung giao dịch. Đối soát quét mọi sheet, nhận diện cột theo tiêu đề của từng bố cục; không cố định BIDV. CSV kết quả giữ sheet, ngân hàng/kênh và tham chiếu. Tác vụ hiển thị số dòng từng sheet và sheet chưa nhận diện được. Các dòng chưa rõ chiều ghi có/ghi nợ cần duyệt, giữ gợi ý liên quan với điểm thấp; xem chi tiết bố cục trong hướng dẫn quản trị.

UI có link xem bố cục cho học và đối soát, chỉ mở ngay trên trang; không tải mẫu hay xuất dữ liệu đã học. Excel kết quả chia theo sheet đầu vào; CSV kết quả đóng ZIP, mỗi sheet một CSV. Xuất giữ mã dưới dạng chuỗi. Khi học, nhãn IDKH trong tệp đã đối chiếu được tin cậy, kể cả khi nội dung ghi mã khác (được đếm ở `label_not_in_text_ids`). Hệ thống học vị trí mã khách hàng theo từng bố cục ngân hàng/kênh, học gọn các khoản thu hộ tổng hợp và đề xuất phân bổ khi một lần chuyển trả cho nhiều khách hàng; xem [cơ chế học và so khớp](docs/matching-and-learning.md).

Kết quả đối soát có cột **Kỳ thanh toán**, lấy tháng/năm hóa đơn nước từ nội dung và tách khỏi ngày giờ chuyển khoản. Chỉ có tháng thì giữ **chưa rõ năm**; không có căn cứ thì hiển thị **Chưa xác định**. CSV kết quả thêm `payment_period`, `extracted_name`, `extracted_names`, `name_extraction_status`; kỳ vẫn không được đưa vào đặc trưng học hoặc PostgreSQL. CSV học thêm ba cột tùy chọn về thu hộ/tự trả, tương thích tệp cũ.

Lỗi riêng một dòng khi đọc Excel/CSV, học hoặc đối soát được ghi theo sheet/số dòng và bỏ qua để tiếp tục. Tác vụ báo số dòng lỗi, cho xem nguyên nhân và tải toàn bộ danh sách CSV. Mỗi dòng học dùng savepoint để rollback riêng; lỗi toàn tệp, cấu hình hoặc mất kết nối vẫn dừng. Báo cáo dòng lỗi lưu ngoài PostgreSQL cùng working files.

## Thử nghiệm dành cho phát triển

File theo tháng chỉ là fixture; không có nút học/thử file mẫu trên UI. Để tái lập ca **học mọi sheet tháng 7, đánh giá BIDV tháng 8**, dùng kho thử nghiệm riêng đã được khởi tạo:

```bash
.venv/bin/python scripts/learn.py --train 'Data/Ngan hang thang 7-2026 FN.xlsx'
.venv/bin/python scripts/benchmark.py
```

Trên Windows, thay `.venv/bin/python` bằng `.\.venv\Scripts\python.exe`.

Kiểm tra nhanh một ngân hàng (học FN tháng 7, đối soát tháng 8, chấm bằng FN tháng 8; kho SQLite riêng, không chạm PostgreSQL):

```bash
.venv/bin/python scripts/benchmark_all.py --train 'Data/Ngan hang thang 7-2026 FN.xlsx' \
  --raw 'Data/Ngan hang thang 08.2026.xlsx' --truth 'Data/Ngan hang thang 8-2026 FN.xlsx' \
  --out runtime/bench_bidv --sheets BIDV --all-train-sheets
```

Bỏ `--sheets` để đọc mọi sheet. FN học và FN nhãn đánh giá phải tách biệt; không học nhãn đánh giá vào kho. Script dùng SQLite riêng, không xác minh các đường chỉ mục PostgreSQL. `scripts/learn.py` ghi vào PostgreSQL theo cấu hình khi bạn chủ động chạy; benchmark không được dùng kho đã học kỳ thử.

Bộ nhớ nội dung đã xác nhận đã qua **89 kiểm tra hồi quy**; kiểm tra giữ kiến thức trên 200 dòng rồi thêm 1.000 dòng giữ đủ 200 liên kết, 175 tự nhận đúng, 25 cần duyệt, không ghi nhận tự ghép sai. Đây là replay đã học, không phải F1 tháng mới; xem [protocol và báo cáo](docs/recurring-recognition.md).

Các số liệu sau thuộc **bản trước bộ nhớ mới**. Báo cáo trong `reports/` thuộc phiên bản trước. Đợt cập nhật tương tác và nhóm gợi ý đã qua **78 kiểm tra**, kiểm tra Chromium và benchmark E5 trên kho SQLite riêng: holdout ban đầu 500 dòng có 37 kết quả tự chấp nhận đúng, chưa ghi nhận sai; F1 nhóm một khách hàng là 0,15880. Lần kiểm tra hồi quy nhóm gợi ý giữ nguyên kết quả tự chấp nhận. Mẫu tự chấp nhận nhỏ và recall còn thấp; xem [protocol, metrics và giới hạn](docs/interactive-benchmark.md). Runtime Windows và tích hợp PostgreSQL cần xác minh trên máy triển khai.

`scripts/generate_schema.py` sinh `sql/create_current.sql` từ ORM, không kết nối database hoặc đọc cấu hình bí mật. Có thể chọn tên file khác bằng `--output`.
