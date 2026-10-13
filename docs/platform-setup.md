# Chạy trên Linux và Windows

Backend, xử lý Excel/CSV, học PostgreSQL và working files dùng cùng code trên hai hệ điều hành. Lệnh cài đầy đủ ở [README](../README.md). Tạo virtualenv riêng trên từng máy; luôn chạy từ thư mục project. Trên Windows nên đặt project ở đường dẫn ngắn như `C:\HueWACO` để tránh giới hạn đường dẫn của hệ thống.

## Cài hoặc cập nhật dependency

Linux:

```bash
.venv/bin/python -m pip install -r requirements.txt
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`python-dotenv` là dependency mới. Web và các script CLI đọc `.env` ở gốc project, UTF-8 có hoặc không có BOM. Biến môi trường đã có trong process được giữ nguyên. Không cần activate virtualenv, đổi execution policy hay dùng `source .env`.

## Khởi động backend

Linux:

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Mở http://127.0.0.1:8000. Chỉ chạy một worker cho một thư mục working. Mỗi process có 2 luồng xử lý nền: tối đa 2 jobs chạy đồng thời, các jobs sau chờ trong hàng đợi. Không tăng `--workers` để tăng số jobs. Nếu chạy qua service, đặt working directory về thư mục project để các đường dẫn runtime/cache tương đối vẫn trỏ đúng.

## PostgreSQL và SQL

PostgreSQL có thể nằm cùng máy hoặc máy khác. `DATABASE_URL` dùng cùng định dạng trên hai hệ điều hành. Server PostgreSQL phải có pgvector; cài package Python không cài extension vào server.

Bạn tự tạo database/login và chạy SQL; ứng dụng không thực hiện bước này. Với database mới, chỉ chạy `sql/create_current.sql`. Với database cũ, làm theo [quy trình sao lưu và migration](migration-and-storage.md).

PowerShell dùng `-f` để đọc SQL:

```powershell
psql -U postgres -h 127.0.0.1 -p 5432 -d invoice_filter -v ON_ERROR_STOP=1 -f "sql/create_current.sql"
```

Nếu không tìm thấy `psql`, gọi đường dẫn đầy đủ, ví dụ (đổi phiên bản theo máy):

```powershell
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -h 127.0.0.1 -p 5432 -d invoice_filter -v ON_ERROR_STOP=1 -f "sql/create_current.sql"
```

Lệnh `sudo -u postgres ... < file.sql` trong tài liệu chỉ dành cho Linux; PowerShell dùng lệnh trên.

## Đường dẫn, Unicode và file tạm

`RUNTIME_DIR`, `EMBEDDING_CACHE`, `EMBEDDING_MODEL_CACHE` có thể dùng đường dẫn phù hợp máy. Trong `.env` trên Windows, dùng dấu `/` để tránh nhầm chuỗi escape, ví dụ:

```dotenv
RUNTIME_DIR=C:/HueWACO/runtime
EMBEDDING_CACHE=C:/HueWACO/runtime/embeddings.sqlite
EMBEDDING_MODEL_CACHE=C:/HueWACO/runtime/models
```

Tên tệp upload giữ tiếng Việt; ký tự Windows không cho phép hoặc tên thiết bị như `CON.xlsx` được thay an toàn. Tên quá dài được rút gọn, giữ phần mở rộng; nội dung giao dịch và các chuỗi số không bị thay đổi. Trên Windows, độ dài tên còn được giới hạn theo thư mục lưu để không cần bật long paths. Nếu bản thân thư mục quá dài, đổi project hoặc `RUNTIME_DIR` sang đường dẫn ngắn hơn.

Cache SQLite đóng connection sau mỗi lần truy cập. Trình đọc Excel đăng ký và đóng các stream ZIP, connection SQLite trước khi dọn thư mục tạm, kể cả khi lỗi hoặc dừng giữa chừng. Harness benchmark cũng đóng connection/engine tạm trước khi xóa thư mục. PostgreSQL vẫn là kho kiến thức; SQLite chỉ là cache/file tạm hoặc fixture phát triển.

Thư mục tạm được Python tự tạo theo cấu hình hệ điều hành, không cố định `/tmp`. Trình đọc Excel giữ riêng đối tượng `TemporaryDirectory` để quản lý dọn dẹp và đường dẫn `Path` để mở SQLite: giá trị trả về khi vào context là chuỗi đường dẫn, không phải đối tượng có thuộc tính `.name`. Bạn không cần tạo thư mục temp thủ công cho lỗi này.

## Ghi working files

Trên cả hai hệ điều hành, file JSON được ghi vào file tạm cùng thư mục, flush/fsync file, đóng handle rồi `os.replace` sang tên chính. File JSONL cũng flush/fsync trước khi cập nhật chỉ mục.

Linux/POSIX đồng bộ thêm thư mục khi có `O_DIRECTORY` và filesystem hỗ trợ. Windows không gọi `os.open`/`fsync` trên thư mục vì Python không cung cấp thao tác tương đương ở đây; không phát sinh lỗi thiếu `O_DIRECTORY`. Lỗi I/O thực sự trên nền tảng có hỗ trợ vẫn được báo.

Thay thế nguyên tử tránh đọc JSON ghi dở, nhưng độ bền metadata khi mất điện không giống nhau trên mọi filesystem. Intent và receipt giúp tiếp tục xác nhận sau lỗi/khởi động lại mà không học trùng; vẫn nên sao lưu working cùng PostgreSQL nếu cần giữ thao tác đang dở.

## Đo RAM trong báo cáo phát triển

Linux giữ `resource.getrusage(...).ru_maxrss`; Windows dùng `ctypes` gọi [GetProcessMemoryInfo](https://learn.microsoft.com/en-us/windows/win32/api/psapi/nf-psapi-getprocessmemoryinfo), lấy [PeakWorkingSetSize](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-process_memory_counters). Không cần cài thư viện đo RAM thêm. Đơn vị hiển thị là MiB, đo peak của cả vòng đời process; đây không phải riêng dung lượng model.

Nếu hệ điều hành không cung cấp thống kê hoặc API không đọc được, báo cáo dùng `null`/`N/A`; lỗi thống kê không làm tác vụ thất bại. Module `resource` chỉ được import ở nhánh hỗ trợ, nên Windows vẫn khởi động bình thường.

Các thay đổi tương thích được rà soát và kiểm tra cú pháp tĩnh. Đợt cập nhật tương tác đã chạy backend, kiểm tra hồi quy, trình duyệt và benchmark E5 trên Linux với SQLite riêng; xem [báo cáo](interactive-benchmark.md). Chưa xác minh runtime trên Windows thực tế hoặc tích hợp PostgreSQL.

## Quản lý hai jobs song song

- Đối soát và nhập dữ liệu dùng chung pool hai luồng; tiến trình, lỗi, kết quả và nút dừng thuộc từng job. Job thứ ba chờ đến khi có chỗ trống.
- Dừng job đang chờ: hủy ngay và dọn upload. Dừng job đang chạy: chuyển “Đang dừng”, kết thúc ở điểm kiểm tra tiếp theo; những đợt đã lưu được giữ. Đợt suy luận mô hình đang chạy có thể cần hoàn tất trước khi dừng.
- Ghi kiến thức được khóa theo từng transaction, gồm metadata nhập, receipt và audit. Đọc tệp, trích xuất và đối soát vẫn chạy song song. Hai lần nhập cùng tệp không nhân đôi kiến thức.
- Encoder/NER dùng chung một instance có khóa, không tải thêm bản sao mô hình cho job thứ hai. Tốc độ phụ thuộc CPU/GPU, I/O và phần suy luận dùng chung; hai jobs không đồng nghĩa tốc độ gấp đôi.
- Shutdown thông thường ngừng nhận job, hủy hàng đợi, yêu cầu dừng hai job đang chạy, chờ đóng tệp và dọn upload. Nếu process bị tắt đột ngột, jobs chưa xong chuyển sang gián đoạn khi khởi động lại; kết quả đã lưu còn nguyên.
- PostgreSQL đối soát theo snapshot từng nhóm dòng; khi học và đối soát đồng thời, các nhóm sau có thể sử dụng kiến thức vừa commit. Không chia sẻ SQLAlchemy session giữa các jobs.

Kiểm tra tại `tests/test_job_concurrency.py`: hai jobs thực sự chồng thời gian, giới hạn hàng đợi, kết quả/ID riêng, dừng độc lập, lỗi không kẹt hàng đợi, shutdown, khởi động lại và nhập trùng đồng thời. Các kiểm tra tự động dùng SQLite riêng; không chạy vào PostgreSQL triển khai.
