# HueWACO · Đối soát và quản lý kiến thức

Một trang HTML tiếng Việt và backend FastAPI để lọc giao dịch BIDV, đối chiếu với lịch sử đã xác nhận và quản lý kho kiến thức. Giao diện quản trị theo màu xanh/chuyển sắc và logo của [website HueWACO](https://huewaco.com.vn/); tài nguyên giao diện được phục vụ tại chỗ.

Mỗi khách hàng có nhiều mẫu. Thêm mẫu mới giữ mẫu cũ; hợp đồng/mã quan trọng khác nhau được phân biệt ngay cả khi cùng nội dung chữ. Khi người dùng sửa khách hàng rồi **Xác nhận & học**, ứng dụng học ngay trong transaction PostgreSQL. Dự đoán hoặc chọn checkbox không tự học. Khách hàng chưa biết/thiếu chứng cứ đáng tin được đưa về 0% và kiểm tra thủ công.

PostgreSQL chỉ lưu **kiến thức đã học**. Kết quả đối soát, trạng thái duyệt và tác vụ lưu trong tệp working; có thể tải CSV và dọn qua giao diện. Không cần mở database để quản lý khách hàng/mẫu hoặc dừng luồng xử lý.

## Cài đặt

Python 3.12+, PostgreSQL 15+ với pgvector cài trên máy chủ:

```bash
python3 -m venv .venv
.venv/bin/pip install 'torch>=2.4,<3' --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -r requirements.txt
test -f .env || cp .env.example .env
```

Sửa DATABASE_URL trong .env. Quản trị viên tự tạo database/login và chạy SQL; backend không tự khởi tạo hoặc migrate PostgreSQL.

**Database mới**: chỉ chạy [sql/create_current.sql](sql/create_current.sql) để tạo thẳng schema hiện tại, không cần init → 001 → 002. **Database đang dùng phiên bản cũ**: xuất dữ liệu cũ rồi chạy sql/migrations/002_knowledge_only.sql. Migration giữ kiến thức và xóa các bảng kết quả/tác vụ cũ sau khi bạn đã sao lưu. Xem lệnh, xử lý quyền đọc file và chuyển các xác nhận còn chờ tại [hướng dẫn chuyển phiên bản](docs/migration-and-storage.md).

Khởi động từ thư mục project:

```bash
set -a
source .env
set +a
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Mở **http://127.0.0.1:8000**; API tại /docs. Chạy một worker vì tác vụ và working files được quản lý trong một process.

## Giao diện và tài liệu

| Khu vực | Công việc |
| --- | --- |
| Đối soát | Tải Excel ngân hàng, lọc kết quả theo tệp, xem lịch sử, sửa/xác nhận từng dòng hoặc hàng loạt, xuất CSV. |
| Học dữ liệu mới | Nhập Excel đã gán nhãn từ mọi sheet có IDKH hoặc CSV đã duyệt; dừng tác vụ; thêm khách hàng/mẫu. |
| Quản lý kiến thức | Xem mọi bảng ứng dụng, tìm kiếm/phân trang/chi tiết; thêm/sửa/xóa khách hàng và mẫu; thêm biến thể. |
| Tác vụ xử lý | Tiến trình, dừng nhập/lọc, xem kết quả, xuất CSV, xóa tệp làm việc, tiếp tục xác nhận còn chờ. |
| Hướng dẫn | Quy trình thao tác cho người dùng. |

Tài liệu chi tiết:

- [Hướng dẫn quản trị](docs/admin-guide.md): đối soát, manual, học sau xác nhận, nhiều mẫu, CSV.
- [Tìm kiếm, so khớp và học](docs/matching-and-learning.md): số theo thứ tự, retrieval, feature/trọng số, guard precision, chống trùng và phục hồi.
- [Schema database](docs/database-schema.md): 10 bảng, quan hệ, trường, chỉ mục, cách lưu mẫu/hóa đơn/số và quyền quản lý.
- [Cài đặt, chuyển phiên bản và lưu trữ](docs/migration-and-storage.md): SQL tự chạy, CSV sao lưu cũ, working, restart và dọn kết quả.

## Xử lý cốt lõi

Encoder **intfloat/multilingual-e5-small**, 384 chiều, hỗ trợ tiếng Việt và mặc định chạy CPU. Không dùng Ollama/LLM sinh nội dung cục bộ. Văn bản giữ dấu cho encoder; số/định danh kiểm tra riêng bằng quy tắc. Cache mô hình mặc định runtime/models.

Retrieval kết hợp posting theo mã/tên/người trả tiền/hợp đồng, full-text, trigram và pgvector HNSW; rerank tối đa 40 mẫu. Sau đó lấy mẫu tốt nhất mỗi khách hàng để tránh các mẫu cùng khách hàng cạnh tranh giả. Điểm hiển thị là điểm bằng chứng, không phải xác suất đã hiệu chuẩn.

HD là **hợp đồng**; TKThe là thẻ người trả tiền. Số giữ chuỗi đầy đủ, thứ tự, vai trò, định dạng và số 0 đầu; không cắt chuỗi dài. Hợp đồng/mã xung đột, tài khoản dùng chung và nhiều khách hàng có điểm gần nhau chặn ghép tự động. Nhãn ko được bỏ qua khi học.

Giới hạn upload 100 MB, XLSX giải nén tối đa 2 GB; đọc streaming, chia đợt và cache embedding có giới hạn. Dừng tại checkpoint, giữ các đợt đã commit. Knowledge ghi tên encoder/extractor để chặn trộn cấu hình không tương thích.

## Thử nghiệm dành cho phát triển

File theo tháng chỉ là fixture; không có nút học/thử file mẫu trên UI. Để tái lập ca **học mọi sheet tháng 7, đánh giá BIDV tháng 8**, dùng kho thử nghiệm riêng đã được khởi tạo:

```bash
.venv/bin/python scripts/learn.py --train 'Data/Ngan hang thang 7-2026 FN.xlsx'
.venv/bin/python scripts/benchmark.py
```

Benchmark dùng BIDV trong Data/Ngan hang thang 08.2026.xlsx, nhãn từ Data/Ngan hang thang 8-2026 FN.xlsx; không học nhãn đánh giá. Route API phát triển được giữ nhưng không đưa lên UI.

Báo cáo cũ nằm trong reports/; **chưa chạy lại kiểm thử hoặc benchmark cho thay đổi quản trị này theo yêu cầu của bạn**, không có F1 mới. Báo cáo trước dùng fixture SQLite tách biệt; PostgreSQL là database ứng dụng. Không học tháng đánh giá vào kho benchmark.

scripts/generate_schema.py sinh sql/create_current.sql từ ORM không kết nối database. File này dành cho schema mới; schema cũ dùng migration. Có thể chọn tên file khác bằng --output.
