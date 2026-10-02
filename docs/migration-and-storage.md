# Cài đặt, chuyển phiên bản và lưu trữ

## Database mới

Cài pgvector trên PostgreSQL 15+ trước. Tạo login/database nếu chưa có bằng tài khoản quản trị; thay mật khẩu mẫu:

```sql
CREATE USER invoice_app WITH PASSWORD '123456';
CREATE DATABASE invoice_filter OWNER invoice_app;
```

Từ terminal tài khoản Linux của bạn, trong thư mục project:

```bash
sudo -u postgres psql -d invoice_filter -v ON_ERROR_STOP=1 < sql/create_current.sql
```

`sql/create_current.sql` tạo thẳng schema hiện tại gồm 11 bảng kiến thức, kiểu TEXT cho giá trị số/token, digest và đầy đủ chỉ mục. Với database mới, **chỉ chạy file này**, không chạy init → 001 → 002. File không xóa dữ liệu hoặc nâng cấp cấu trúc bảng cũ.

Dùng `<` để shell của bạn đọc file; tiến trình postgres không phải mở file trong thư mục home riêng. Không chạy từ shell postgres nếu tài khoản đó không đọc được project. `-p` cần số port, ví dụ `-p 5432`.

Trên **Windows (PowerShell)**, dùng kết nối TCP và `-f`, không dùng `sudo` hoặc `<`:

```powershell
psql -U postgres -h 127.0.0.1 -p 5432 -d invoice_filter -v ON_ERROR_STOP=1 -f "sql/create_current.sql"
```

Nếu `psql` chưa có trong PATH, dùng `& "C:\Program Files\PostgreSQL\16\bin\psql.exe"` thay cho `psql` (đổi `16` theo bản cài). File SQL vẫn do bạn tự chạy; PostgreSQL trên Windows cũng cần pgvector cài sẵn. Xem [hướng dẫn hai hệ điều hành](platform-setup.md).

File tự cấp quyền cho login `invoice_app` nếu login đã tồn tại. Nếu dùng login khác, administrator cần cấp quyền tương ứng trong database invoice_filter (thay tên login ở các lệnh):

```sql
GRANT USAGE ON SCHEMA public TO invoice_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO invoice_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO invoice_app;
```

Tài khoản tạo bảng sở hữu bảng/sequence; sở hữu database không tự cấp quyền sửa bảng do tài khoản khác tạo. Kết nối ghi rõ database để tránh mặc định tìm tên database giống login:

```bash
psql -U invoice_app -h 127.0.0.1 -p 5432 -d invoice_filter
```

## Database phiên bản cũ

**Migration 002 xóa các bảng và dữ liệu transactions, feedback, jobs cũ; giữ kho kiến thức.** Chỉ chạy sau khi đã xuất dữ liệu cũ cần giữ. Backend không tự chạy migration.

1. Dừng backend để dữ liệu không đổi khi xuất/migrate.
2. Từ thư mục project, xuất kết quả cũ; script tự đọc `.env`:

   ```bash
   .venv/bin/python scripts/archive_legacy_results.py --output runtime/legacy-export-before-002
   ```

   Windows PowerShell:

   ```powershell
   .\.venv\Scripts\python.exe scripts/archive_legacy_results.py --output runtime/legacy-export-before-002
   ```

   Script chỉ đọc PostgreSQL, không học/xóa. Nó xuất transactions.csv, feedback.csv, jobs.csv nếu bảng tồn tại và **confirmed_learning.csv** cho các dòng đã xác nhận nhưng chưa học. Thư mục xuất phải mới để tránh ghi đè. Giữ CSV trước bước tiếp theo; có thể dùng thêm pg_dump nếu cần sao lưu toàn database.

3. Tự chạy SQL:

   ```bash
   sudo -u postgres psql -d invoice_filter -v ON_ERROR_STOP=1 < sql/migrations/002_knowledge_only.sql
   ```

   Windows PowerShell (chỉ sau khi đã xuất/sao lưu dữ liệu cũ):

   ```powershell
   psql -U postgres -h 127.0.0.1 -p 5432 -d invoice_filter -v ON_ERROR_STOP=1 -f "sql/migrations/002_knowledge_only.sql"
   ```

   Đã bao gồm đổi numeric_value/token thành TEXT; không cần chạy 001 riêng. Script backfill digest, đổi chỉ mục rồi bỏ ba bảng tạm. SHA-256 dùng [hàm có sẵn PostgreSQL](https://www.postgresql.org/docs/16/functions-binarystring.html). Toàn bộ trong transaction, lỗi sẽ không commit; không CASCADE để tránh xóa phụ thuộc ngoài phạm vi.

4. Khởi động backend mới, một worker:

   ```bash
   .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
   ```

   Windows: `.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1`.

5. Nếu confirmed_learning.csv có dữ liệu, vào **Học dữ liệu mới**, tải CSV đó và xác nhận nguồn đã duyệt để học các dòng còn chờ của phiên bản cũ.

CSV xuất cũ là bản lưu ngoài ứng dụng; UI mới không tự nhập lại mọi kết quả cũ. Migration giữ các mẫu đã học. Mẫu mới phân biệt thêm hợp đồng/mã quan trọng; muốn bổ sung biến thể đã bị gộp trước đây, thêm ví dụ đã xác nhận qua quản lý mẫu. Import cùng file hoàn tất vẫn chống trùng, không tự khôi phục knowledge đã cố ý xóa.

`CREATE TABLE IF NOT EXISTS` không cập nhật cấu trúc bảng đã tồn tại: kho cũ cần migration tương ứng, gồm 002 cho lưu trữ knowledge-only và 003 cho mẫu chung. Nếu encoder/extractor khác cấu hình hiện tại, migration không tự chuyển embedding; dùng cấu hình tương thích hoặc kho mới để nhập lại lịch sử đã xác nhận.

## Nâng cấp kho knowledge-only lên mẫu chung

Nếu kho đã ở phiên bản 002, dừng ứng dụng, sao lưu rồi tự chạy **`sql/migrations/003_shared_templates.sql`**. Trên Windows:

```powershell
psql -U postgres -h 127.0.0.1 -p 5432 -d invoice_filter -v ON_ERROR_STOP=1 -f "sql/migrations/003_shared_templates.sql"
```

Linux dùng cùng lệnh TCP, hoặc `sudo -u postgres psql -d invoice_filter -v ON_ERROR_STOP=1 < sql/migrations/003_shared_templates.sql`. Script bổ sung `payment_templates`, gắn `template_id`, giữ số/vector/receipts và bổ sung loại thu hộ/tự trả. Dữ liệu cũ giữ unknown để admin xác nhận trên UI. Không chạy migration trên database mới đã tạo bằng `create_current.sql`. Chi tiết [schema và quản lý thu hộ](shared-payment-templates.md).

## Vị trí lưu trữ

Mặc định RUNTIME_DIR=runtime:

```text
runtime/working/
  state.json                   # namespace và số ID tiếp theo
  <job-uuid>/
    job.json                   # tệp nguồn, trạng thái, tiến trình
    results.jsonl              # kết quả/bằng chứng tại lúc lọc
    row-errors.jsonl           # toàn bộ dòng lỗi: sheet, số dòng, bước và nguyên nhân
    index.json                 # offset, quyết định, trạng thái từng dòng
    reviews/<row-id>.json      # trạng thái duyệt và khách hàng xác nhận
    intents/<row-id>.json      # xác nhận đang chờ hoàn tất
```

Payload JSONL đọc từng dòng, chỉ giữ offset/trạng thái nhỏ trong bộ nhớ. Chỉ mục vẫn tăng theo số dòng, nên bộ nhớ không hoàn toàn độc lập với kích thước dữ liệu. Ghi JSON bằng flush/fsync file rồi thay thế nguyên tử; append xong mới cập nhật index. Linux đồng bộ thêm metadata thư mục khi filesystem hỗ trợ; Windows bỏ bước fsync thư mục vì Python không hỗ trợ `O_DIRECTORY` ở đây. Mức bảo đảm metadata khi mất điện phụ thuộc nền tảng/filesystem; xem [khác biệt nền tảng](platform-setup.md).

| Tệp/thư mục | Mục đích |
| --- | --- |
| runtime/uploads/UUID/ | Upload tạm, dọn khi tác vụ kết thúc/dừng/lỗi; sự cố process có thể để lại file cần dọn. |
| runtime/models/ | Cache encoder và NER theo cấu hình. |
| runtime/exports/ | Tệp xuất tạm theo sheet; dọn sau khi tải hoàn tất. |
| runtime/embeddings.sqlite | Cache embedding có giới hạn, không phải lịch sử nghiệp vụ. |
| runtime/legacy-export-before-002/ | CSV cũ do quản trị viên tự xuất. |
| reports/ | Báo cáo phát triển, không đưa vào UI người dùng. |

Trình đọc XLSX có SQLite tạm cho shared strings để không nạp cả workbook; không thay PostgreSQL hoặc lưu lịch sử lọc.

Danh sách lỗi từng dòng nằm trong working files và có CSV tải từ Tác vụ xử lý. Job JSON chỉ giữ tổng số và tối đa 50 lỗi đầu, giao diện hiển thị tối đa 20; CSV chứa mọi lỗi đã ghi, nên không giữ toàn bộ báo cáo trong RAM. PostgreSQL chỉ giữ số lỗi tổng hợp trong metadata lần nhập kiến thức, không lưu danh sách số dòng/nguyên nhân. Xóa tệp làm việc cũng xóa báo cáo lỗi; tải CSV trước nếu cần giữ.

## Gián đoạn và phục hồi

Ứng dụng lưu intent trước khi học, commit PostgreSQL rồi ghi trạng thái review vào file. Nếu file lỗi sau commit, intent còn; restart hoặc bấm tiếp tục sẽ chạy cùng receipt để hoàn tất trạng thái, không nhân đôi knowledge.

Không xóa working bằng tay khi còn intent. UI chặn xóa tệp có xác nhận chờ. Sao lưu working cùng PostgreSQL nếu cần giữ luồng đang dở. Backend một process dùng khóa trong tiến trình và advisory lock cho ghi knowledge.

Sau restart, tác vụ nhập/lọc đang chạy chuyển thành gián đoạn. Đợt học đã commit còn, đợt chưa commit rollback. Tải lại file xác nhận chống trùng theo receipt; tải lại file chưa lọc tạo tác vụ/bộ kết quả mới.

## Dọn dữ liệu

- **Xóa kết quả hiển thị** chỉ làm trống bảng trên trang.
- **Xóa tệp làm việc** ở Tác vụ xử lý xóa job/result/review của tác vụ đã dừng, giữ knowledge.
- **Xóa mẫu/khách hàng** mới xóa knowledge PostgreSQL, có xác nhận.
- CSV kết quả dùng đối soát; không xuất riêng dữ liệu đã học. CSV đã xác nhận của người dùng hoặc CSV lưu từ phiên bản cũ vẫn nhập được.

Không tự đặt thời hạn xóa kết quả. Quản trị viên tải CSV rồi xóa tệp không còn cần. Không chạy nhiều Uvicorn worker cùng ghi một working directory.
