# Cài đặt, nâng cấp và lưu trữ

Ứng dụng dùng PostgreSQL cho kiến thức. Bạn tự chạy các lệnh SQL; backend không tự init, migrate hoặc reset database. Các lệnh dưới đây chạy từ thư mục project.

## Database mới: một file SQL

PostgreSQL cần pgvector cài trên máy chủ. Tạo login/database bằng tài khoản quản trị nếu chưa có, dùng mật khẩu riêng:

```sql
CREATE USER invoice_app WITH PASSWORD 'THAY_BANG_MAT_KHAU_CUA_BAN';
CREATE DATABASE invoice_filter OWNER invoice_app;
```

Sau đó chỉ chạy [sql/create_current.sql](../sql/create_current.sql), tạo trực tiếp **17 bảng** và chỉ mục hiện tại. Không cần init hoặc các migration trước/sau. File không tạo login/database, không xóa dữ liệu, không nâng cấp bảng cũ bằng `IF NOT EXISTS`.

Linux, từ shell tài khoản của bạn có quyền đọc project:

```bash
sudo -u postgres psql -d invoice_filter -v ON_ERROR_STOP=1 < sql/create_current.sql
```

Dấu `<` để shell của bạn đọc SQL, tránh tài khoản postgres không có quyền vào thư mục home của bạn. `-p` cần số port, ví dụ `-p 5432`.

Windows PowerShell, dùng TCP và `-f`:

```powershell
psql -U postgres -h 127.0.0.1 -p 5432 -d invoice_filter -v ON_ERROR_STOP=1 -f "sql/create_current.sql"
```

Nếu chưa có `psql` trong PATH, thay bằng `& "C:\Program Files\PostgreSQL\16\bin\psql.exe"` (đổi phiên bản theo máy). Cài pgvector vào PostgreSQL riêng, không phải chỉ pip install.

SQL cấp quyền cho `invoice_app` nếu login tồn tại. Chủ database không tự sở hữu các bảng do postgres tạo; quyền schema/table/sequence cho phép ứng dụng truy vấn, học và lấy ID tự tăng. Nếu dùng login khác, cấp quyền tương ứng:

```sql
GRANT USAGE ON SCHEMA public TO invoice_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO invoice_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO invoice_app;
```

Kết nối luôn ghi rõ database: `psql -U invoice_app -h 127.0.0.1 -p 5432 -d invoice_filter`. Cấu hình `DATABASE_URL` của ứng dụng trỏ vào database này.

## Kho đã qua 006: giữ nội dung đã xác nhận (007)

Dừng backend và sao lưu. Tự chạy [007_recurring_memory.sql](../sql/migrations/007_recurring_memory.sql), rồi bổ sung bộ nhớ từ các ví dụ đã duyệt, trước khi khởi động lại:

Windows PowerShell:

```powershell
psql -U postgres -h 127.0.0.1 -p 5432 -d invoice_filter -v ON_ERROR_STOP=1 -f "sql/migrations/007_recurring_memory.sql"
.\.venv\Scripts\python.exe scripts/rebuild_recurring_memory.py
```

Linux:

```bash
sudo -u postgres psql -d invoice_filter -v ON_ERROR_STOP=1 < sql/migrations/007_recurring_memory.sql
.venv/bin/python scripts/rebuild_recurring_memory.py
```

Script dùng kết nối ứng dụng đã cấu hình, không tải embedding/NER, không đọc kết quả đối soát. Chạy lại an toàn sau gián đoạn: giữ các ví dụ đã có, không tăng lần học. Kho cũ chưa bổ sung xong không tự chấp nhận bằng đường tra bộ nhớ mới. Xem [cách giữ liên kết nhóm/cá nhân](recurring-recognition.md). Database mới tạo bằng `create_current.sql` không cần migration hay backfill này.

## Kho v7/005: thêm giao diện tương tác (006)

Dừng backend, backup, tự chạy [006_interactive_knowledge.sql](../sql/migrations/006_interactive_knowledge.sql), rồi thực hiện 007 và rebuild ở mục trên trước khi khởi động lại. Không chạy SQL tạo mới trên kho đã có. Riêng 006 không cần dựng lại chỉ mục.

```powershell
psql -U postgres -h 127.0.0.1 -p 5432 -d invoice_filter -v ON_ERROR_STOP=1 -f "sql/migrations/006_interactive_knowledge.sql"
```

Linux có thể dùng cùng lệnh TCP hoặc `sudo -u postgres psql -d invoice_filter -v ON_ERROR_STOP=1 < sql/migrations/006_interactive_knowledge.sql`. SQL thêm `bank_aliases`, `knowledge_changes`, indexes và quyền audit cho role `invoice_app` nếu đã tồn tại. Backend không tự chạy. Xem [cơ chế tương tác và đăng nhập](interactive-knowledge.md).

## Kho cũ: migration 005 và xây lại chỉ mục

Nâng cấp giữ hồ sơ và các liên kết đã xác nhận, không cần xóa kho. Bộ trích xuất v7 thay đổi cách đọc số/kỳ; chỉ thêm cột SQL chưa đủ để dùng vector/slot cũ an toàn.

1. Dừng backend và sao lưu PostgreSQL cùng `runtime/working/` nếu cần giữ các lượt duyệt đang dở.
2. Xác định schema đã có các migration đến **004**. Kho 002 chạy 003 rồi 004; kho 003 chạy 004. Migration 004 có ghi chú lịch sử yêu cầu kho mới cho v6; khi nâng tiếp lên v7, công cụ reindex dưới đây thay bước nhập lại toàn bộ kho thường.
3. Tự chạy 005 bên dưới, rồi 006 và 007 ở các mục trước, **trước khi chạy reindex**: [005_confirmed_evidence.sql](../sql/migrations/005_confirmed_evidence.sql):

   ```powershell
   psql -U postgres -h 127.0.0.1 -p 5432 -d invoice_filter -v ON_ERROR_STOP=1 -f "sql/migrations/005_confirmed_evidence.sql"
   ```

   Linux có thể dùng cùng lệnh TCP hoặc:

   ```bash
   sudo -u postgres psql -d invoice_filter -v ON_ERROR_STOP=1 < sql/migrations/005_confirmed_evidence.sql
   ```

4. Giữ `EMBEDDING_MODEL` đúng mô hình đã dùng và `.env` trỏ đúng kho. Tự chạy công cụ bảo trì, khi backend vẫn dừng:

   ```powershell
   .\.venv\Scripts\python.exe scripts/reindex_knowledge.py
   ```

   Linux:

   ```bash
   .venv/bin/python scripts/reindex_knowledge.py
   ```

5. Khi công cụ hoàn tất, khởi động lại backend một worker và tải lại trình duyệt (Ctrl+F5). Có thể nhập lại các tệp FN/CSV đã duyệt để bổ sung ví dụ/alias và danh sách khách hàng thu hộ cũ.

### Công cụ reindex làm gì?

- Khôi phục mã nhãn cũ thiếu số 0 đầu và cập nhật FK; gộp hồ sơ trùng mã chuẩn khi cần. Mã không thể chuyển thành sáu số báo lỗi để quản trị sửa, không cắt chuỗi dài.
- Đọc `raw_example` đã xác nhận, trích lại số/ngày, tạo mẫu theo ngân hàng, vector, slots, features, postings và thông tin nhãn.
- Giữ ID liên kết, khách hàng, ví dụ/nguồn, số lần gặp, loại thanh toán, receipt; giữ các biến thể cũ nếu fingerprint mới trùng.
- Giữ tên/ghi chú/loại quản lý của mẫu cũ khi tạo mẫu tương ứng và mẫu mới chưa có nhãn.
- Cập nhật hard negative có liên kết tới ví dụ; hard negative rời không có nội dung gốc không thể xây lại fingerprint và có thể không còn khớp sau đổi chuẩn hóa.
- Đếm lại các vị trí IDKH theo ngân hàng và ghi phiên bản v7 khi hoàn tất.
- Bổ sung bộ nhớ `recurring_links` từ ví dụ đã duyệt, giữ cả các quan sát riêng đã học thêm; không cần chạy rebuild riêng sau reindex thành công.

Công cụ chỉ dựng lại số từ **ví dụ đại diện được lưu**, không phục hồi mọi biến thể số từ các giao dịch gốc không còn trong kho. Confidence của giá trị/vai trò còn trùng được giữ; dữ liệu số phái sinh không có trong ví dụ đại diện được dựng lại khi nhập tệp đã duyệt. Không tải NER trong reindex; embedding có thể cần cache/mô hình theo cấu hình người chạy.

Các khoản thu hộ tổng hợp cũ không có danh sách khách hàng trên mẫu: receipt chỉ có hash và mã, không đủ khôi phục quan hệ mẫu/khách hàng. **Nhập lại các tệp đã duyệt** để bổ sung `collection_memberships`; receipt ngăn tăng đếm các dòng đã học. Mẫu tổng hợp cũ không tự biến thành bảng kê mới chỉ nhờ SQL.

Reindex commit theo đợt, đặt `knowledge_maintenance=running`; ứng dụng chặn học/so khớp trong trạng thái này. Nếu bị gián đoạn, giữ backend dừng và chạy lại cùng lệnh từ đầu; không tăng lần học. Đây không phải transaction toàn kho có thể rollback bằng một câu SQL. Backup là cách quay lại trạng thái trước bảo trì. Công cụ không chuyển sang embedding model khác; nếu muốn đổi model, tạo kho mới và học lại lịch sử đã duyệt.

## Các phiên bản rất cũ còn lưu kết quả trong PostgreSQL

Migration 002 xóa các bảng `transactions`, `feedback`, `jobs` cũ. Nếu kho còn các bảng này, dừng backend và xuất trước bằng `scripts/archive_legacy_results.py --output runtime/legacy-export-before-002` (dùng Python trong virtualenv tương ứng hệ điều hành), rồi mới chạy 002, 003, 004, 005, 006, 007 và reindex. Script lưu CSV, gồm các xác nhận chưa học, chỉ đọc database. Nhập lại `confirmed_learning.csv` sau nâng cấp khi cần. Không chạy 002 chỉ vì muốn cập nhật một kho đã knowledge-only.

`sql/delete_all.sql` là công cụ xóa do quản trị tự chủ động dùng; không thuộc luồng nâng cấp giữ kiến thức. Không chạy migration cho database mới đã dùng `create_current.sql`.

## Vị trí lưu trữ

```text
runtime/working/
  state.json
  <job-uuid>/
    job.json
    results.jsonl
    row-errors.jsonl
    index.json
    reviews/<row-id>.json
    intents/<row-id>.json
```

PostgreSQL giữ kiến thức; working giữ tác vụ/kết quả/duyệt/lỗi từng dòng. Kỳ hóa đơn nằm trong kết quả. Cache encoder/NER ở `runtime/models/`, cache embedding ở `runtime/embeddings.sqlite`; upload/export có thư mục tạm riêng. Trình đọc XLSX dùng SQLite tạm cho shared strings và đóng trước khi dọn trên Windows. Không có lịch sử đối soát trong các bảng nghiệp vụ PostgreSQL.

Working ghi intent trước commit kiến thức, rồi ghi trạng thái duyệt sau commit. Nếu process dừng giữa hai bước, restart/tiếp tục chạy lại cùng receipt. UI chặn xóa tệp còn intent. Tác vụ đang chạy khi restart được đánh dấu gián đoạn; học đã commit còn, đợt chưa commit rollback.

Nhập lại tệp đã duyệt vẫn đọc lại để bổ sung/khôi phục liên kết; receipt ngăn tăng đếm. Do đó, sau khi cố ý xóa một liên kết, nộp lại tệp chứa liên kết đó có thể tạo lại nó. Muốn giữ liên kết bị xóa, loại dòng đó khỏi nguồn học.

**Xóa kết quả hiển thị** chỉ làm trống màn hình. **Xóa tệp làm việc** dọn kết quả/tác vụ đã dừng, giữ kiến thức. **Xóa khách hàng/mẫu/thành viên thu hộ** mới tác động PostgreSQL và cần xác nhận trên UI. Không có chức năng xuất dữ liệu đã học hoặc tải mẫu bố cục; mẫu chỉ hiển thị trên UI. Kết quả vẫn xuất Excel hoặc ZIP CSV theo sheet.

Ghi file sử dụng flush/fsync và thay thế file; Linux đồng bộ thêm thư mục khi hỗ trợ, Windows không cần `O_DIRECTORY`. Một process/worker quản lý working. Xem [hướng dẫn Linux/Windows](platform-setup.md).
