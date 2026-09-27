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

Mở http://127.0.0.1:8000. Chỉ chạy một worker cho một thư mục working. Nếu chạy qua service, đặt working directory về thư mục project để các đường dẫn runtime/cache tương đối vẫn trỏ đúng.

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

Các thay đổi tương thích được rà soát và kiểm tra cú pháp tĩnh; chưa chạy backend/test/benchmark hoặc xác minh runtime trên Windows theo yêu cầu không chạy test của bạn.
