# Cơ chế tìm kiếm, so khớp và học kiến thức

Tài liệu mô tả implementation trong `app/normalize.py`, `app/numeric_match.py`, `app/core.py`, `app/knowledge.py`, `app/main.py` và `app/workspace.py`. Các tệp thử nghiệm theo tháng không quyết định cách vận hành của giao diện.

## 1. Hai luồng tách nhau

```mermaid
flowchart LR
    A[Excel ngân hàng chưa lọc] --> B[Đọc theo đợt]
    B --> C[Chuẩn hóa và giữ thứ tự số]
    C --> D[Tìm mẫu trong PostgreSQL]
    D --> E[Xếp hạng và kiểm tra định danh]
    E --> F[Tệp làm việc JSONL]
    F --> G[Người dùng kiểm tra / sửa khách hàng]
    G --> H[Xác nhận và học]
    H --> I[PostgreSQL: kiến thức đã xác nhận]
    F --> J[CSV kết quả]
    H --> K[CSV các dòng đã học]
    I --> D
```

Phân loại gọi `Core.classify()` và không thêm kết quả dự đoán vào PostgreSQL. Mỗi đợt đối soát đọc knowledge trong transaction `REPEATABLE READ READ ONLY` trên PostgreSQL. Phần đã phân loại được ghi vào tệp làm việc ngoài database.

Luồng triển khai quét mọi sheet trong XLSX, nhận diện cột theo tiêu đề riêng từng sheet và giữ `sheet/payer/reference` trên kết quả/CSV. Import đọc các sheet đã có nhãn; CSV học đọc mọi dòng/kênh. Các trường ngày/số tiền/chiều giao dịch chưa đáng tin trả manual 0% trước khi gọi bộ so khớp. Sheet không phải bố cục giao dịch được liệt kê trong tác vụ, không bị giả lập thành giao dịch. Giới hạn BIDV của benchmark theo tháng chỉ thuộc công cụ phát triển.

Xác nhận gọi `Core.review()` rồi `Core.learn()` trong transaction ghi có advisory lock. Thay đổi chỉ xuất hiện trong kho kiến thức sau khi transaction commit. Xác nhận trong lúc đối soát có thể ảnh hưởng đến các đợt đọc kiến thức **sau đó**; các kết quả đã tạo không được thay đổi ngầm.

## 2. Chuẩn hóa vẫn bảo toàn số

Đầu vào được lưu nguyên văn ở `raw_example` đối với mẫu đã học hoặc `raw` trong tệp làm việc đối với dòng chưa duyệt.

Để tìm kiếm, hệ thống tạo thêm:

- `normalized`: chữ thường, bỏ dấu để đối chiếu từ ngữ; giá trị số vẫn là chuỗi.
- `segments`: các đoạn text/number/date theo thứ tự, với vị trí và offset gốc.
- `template`: nội dung chữ và placeholder `<NUM_1>`, `<NUM_2>`…; mã chữ/số giữ hình dạng chữ và vị trí số.
- `structure`: chuỗi vai trò/định dạng theo thứ tự.
- `semantic_text`: tiếng Việt giữ dấu; ngày và giá trị định danh được thay bằng vai trò trước khi đưa vào encoder.

Ví dụ:

```text
TT KH:001234 HD:700012 TIEN NUOC
```

```json
{
  "template": "tt kh <NUM_1> hd <NUM_2> tien nuoc",
  "numbers": [
    {"slot": 1, "numeric_type": "CUSTOMER_ID", "value": "001234"},
    {"slot": 2, "numeric_type": "CONTRACT_ID", "value": "700012"}
  ]
}
```

Giá trị số không được đổi sang float/int, không cắt chuỗi, không tự chia một chuỗi số dài thành các mã khách hàng. `raw_value` trong segments giữ cách ghi gốc. Các token chữ/số liền nhau được giữ nguyên; hợp đồng có dấu nối hoặc dấu `/` được bảo vệ khỏi việc tách sai.

### Vai trò của số

| Vai trò | Cách dùng |
| --- | --- |
| CUSTOMER_ID | Định danh khách hàng có ngữ cảnh rõ ràng. |
| CONTRACT_ID | HD/hợp đồng; phải đối chiếu hợp đồng chính xác. |
| CARD_ID / ACCOUNT_ID | Thẻ hoặc tài khoản người trả tiền; kiểm tra có dùng chung cho nhiều khách hàng không. |
| MIXED_CODE / LOCATION_NUMBER | Mã chữ/số, số cơ sở; khác giá trị ở đúng vai trò có thể chặn ghép. |
| INVOICE_ID / INVOICE_CODE | Thông tin hóa đơn; có thể thay đổi theo khoản thu, không tự xác lập khách hàng. |
| BANK_REFERENCE / BANK_PROTOCOL / REFERENCE_ID | Tham chiếu/giao thức thay đổi; không tự dùng làm định danh. |
| RECEIVER_ACCOUNT | Tài khoản nhận tiền của đơn vị cấp nước; không dùng làm tài khoản khách hàng trả tiền. |
| DATE / AMOUNT / QUANTITY | Không tự xác định khách hàng. |
| UNKNOWN_NUMBER | Chưa đủ ngữ cảnh; chỉ dùng làm chứng cứ khách hàng khi đã có xác nhận gắn đúng token/vị trí. |

`HD` luôn là **hợp đồng**, không phải hóa đơn. Chỉ ngữ cảnh `hóa đơn/invoice` mới là số hóa đơn. `TKThe` là thẻ của người trả tiền, không phải mã khách hàng.

Có một ngoại lệ rõ ràng ở **tra cứu hồ sơ mã khách hàng**: `id_key()` bỏ số 0 đầu để tìm các biến thể mã được xác nhận là cùng hồ sơ, ví dụ `001234` và `1234`. Cơ chế này không áp dụng cho hợp đồng, thẻ, tài khoản hoặc giá trị lưu trong numeric features. Giá trị đầy đủ và mẫu theo từng cách ghi vẫn được giữ và ưu tiên so khớp nguyên văn. Nếu khóa này ánh xạ tới nhiều hồ sơ, đối soát trả manual 0%; học một mã biến thể mới cũng không tự chọn hồ sơ đầu tiên để gộp.

## 3. Một khách hàng, nhiều mẫu

Khóa mẫu mới là SHA-256 của:

```text
[template, structure, ordered_identity]
```

`ordered_identity` chứa `(slot, numeric_type, full_value, format)` của mã khách hàng, hợp đồng, mã chữ/số, số cơ sở và token số đã được xác nhận là thuộc khách hàng.

Do đó:

- Đổi nội dung chữ hoặc cấu trúc → có thể tạo mẫu mới.
- Cùng chữ nhưng khác hợp đồng/mã định danh quan trọng → mẫu riêng.
- Đổi ngày, tham chiếu ngân hàng hoặc số hóa đơn biến đổi → có thể cập nhật cùng mẫu.
- Một mẫu có nhiều giá trị theo từng slot, được lưu ở `numeric_features`; không phải mỗi lần xuất hiện đều tạo một mẫu mới.

Ràng buộc unique là `(customer_id, fingerprint)`, **không** phải unique trên `customer_id`.

Mẫu từ phiên bản cũ được tái sử dụng nếu template, structure và định danh của ví dụ đã lưu phù hợp. Không đổi fingerprint của mọi mẫu cũ hay rebuild embedding tự động. Các ví dụ khác đã bị gộp ở phiên bản cũ không thể được dựng lại chỉ từ một `raw_example`; cần thêm nội dung đã xác nhận tương ứng qua giao diện. Nhập lại cùng tệp đã được đánh dấu hoàn tất vẫn tuân theo chống trùng.

## 4. Retrieval: tìm ứng viên trước, không rerank toàn bộ kho

Mặc định tối đa 40 mẫu được rerank, cấu hình bằng `CANDIDATE_LIMIT`.

### Nhiều đường tìm kiếm và hợp nhất theo thứ hạng

1. **Có mã khách hàng rõ ràng:** tra posting `id:` trên toàn kho. Không có hồ sơ → manual 0%; nhiều hồ sơ → manual 0%. Khi chỉ có một hồ sơ, mọi đường tìm kiếm bên dưới đều giới hạn vào khách hàng này. Đường mã khách hàng ưu tiên nội dung nguyên văn, template/structure, mã nguyên văn và thời điểm cập nhật.
2. **Nội dung chính xác:** `exact:` chứa SHA-256 của nội dung chuẩn hóa.
3. **Định danh:** `contract:` cho hợp đồng và `bound-id:` cho số trần đã được người dùng xác nhận thuộc khách hàng.
4. **Chi tiết có cấu trúc:** `code:` cho mã chữ/số và `detail:` cho chữ ký nội dung thanh toán.
5. **Người trả tiền:** `payer:` cho tài khoản/thẻ, tách khỏi định danh khách hàng.
6. **Tên trích từ nội dung:** NER hoặc trường tên ngân hàng tạo đường `name_entity`, tra `name:` trong các bí danh đã học.
7. **Tìm gần đúng:** postings theo từ/tên/số/vector buckets, vector cosine, full-text, trigram template và trigram tên khách hàng.

Tìm thấy một tài khoản hoặc mã không làm dừng các đường khác. Mỗi đường postings gom theo **pattern_id trước khi LIMIT**, nên mẫu khớp nhiều token không chiếm nhiều vị trí. Ngân sách mỗi đường là `min(500, max(32, 3 × CANDIDATE_LIMIT))`, mặc định 120 mẫu. Khi chưa giới hạn vào một khách hàng, mỗi đường giữ tối đa 4 mẫu/khách hàng. Đường vector đọc trước tối đa 4 lần ngân sách rồi áp dụng giới hạn này; HNSW vẫn là tìm kiếm gần đúng, không bảo đảm tìm đủ mọi đối thủ.

`app/retrieval.py` hợp nhất bằng weighted reciprocal rank fusion (RRF):

```text
retrieval_score(pattern) = Σ channel_weight / (60 + rank_in_channel)
```

| Đường tìm | Trọng số RRF |
| --- | ---: |
| Mã khách hàng | 6 |
| Nội dung chính xác; hợp đồng/token đã xác nhận | 5 |
| Mã chữ/số/chi tiết thanh toán | 3 |
| Tài khoản/thẻ trả tiền | 2 |
| Tên trích từ nội dung (`name_entity`) | 2 |
| Từ khóa, vector, full-text, trigram, tên | 1 mỗi đường |

RRF cộng thứ hạng, tránh cộng trực tiếp điểm posting, cosine và trigram có thang điểm khác nhau. Công thức dựa trên [bài báo RRF gốc](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf); trọng số và các giới hạn là lựa chọn của ứng dụng, chưa được hiệu chỉnh bằng benchmark mới.

Trước khi lấy top cuối, hệ thống dành chỗ cho một mẫu định danh/nội dung chính xác của mỗi khách hàng trong từng đường này. Các chỗ còn lại lấy theo RRF, tối đa 3 mẫu/khách hàng ở lượt đầu để giữ các khách hàng cạnh tranh; nếu còn chỗ thì bổ sung biến thể. Khi ID rõ ràng đã giới hạn khách hàng, không áp dụng hạn mức 3 biến thể. Cuối cùng chỉ tải đầy đủ các mẫu được chọn, tối đa `CANDIDATE_LIMIT`.

### Các chỉ mục PostgreSQL

- Khóa tra cứu có digest SHA-256, đồng thời so lại toàn bộ token để bảo đảm giá trị chính xác.
- Full-text: `to_tsvector('simple', template_text)` và `to_tsquery`, nối các từ đã làm sạch bằng OR (`|`), xếp hạng `ts_rank_cd`. Từ bổ sung trong lời chuyển tiền không còn bắt buộc phải xuất hiện hết trong mẫu cũ. Chuỗi query chỉ được tạo từ token chữ `a-z` và truyền bằng tham số SQL. Xem [toán tử full-text PostgreSQL](https://www.postgresql.org/docs/16/textsearch-controls.html).
- Trigram: `%`/`similarity()` trên template và tên khách hàng.
- Vector: pgvector HNSW, khoảng cách cosine `embedding <=> query_vector`.

Với ID rõ ràng, vector được xếp hạng chính xác trong tập mẫu của khách hàng đã giới hạn bằng CTE materialized, tránh việc lọc khách hàng sau truy vấn ANN làm mất biến thể cũ. Không tải toàn bộ vector của kho vào Python.

Đây là hybrid retrieval gồm exact/structured + full-text + trigram + vector; không có LLM sinh nội dung để quyết định mã khách hàng.

## 5. Embedding tiếng Việt

Encoder hiện tại là `intfloat/multilingual-e5-small`, 384 chiều, chạy CPU theo cấu hình mặc định. Không dùng Ollama hoặc mô hình sinh văn bản cục bộ.

Nội dung đưa vào encoder giữ dấu tiếng Việt, thay số định danh bằng vai trò và bỏ ngày. Hai phía dùng prefix `query:` cho so sánh đối xứng. Vector được chuẩn hóa và cache theo mô hình/nội dung. Tham khảo [model card chính thức](https://huggingface.co/intfloat/multilingual-e5-small).

Cosine cao chỉ hỗ trợ tìm/xếp hạng nội dung. Nó không chứng minh một hợp đồng hoặc khách hàng là đúng và không vượt qua mâu thuẫn định danh.

### Nhận diện tên trong nội dung chuyển tiền

`app/name_extraction.py` dùng [NlpHUST/ner-vietnamese-electra-base](https://huggingface.co/NlpHUST/ner-vietnamese-electra-base), một mô hình token classification tiếng Việt dựa trên ELECTRA, huấn luyện NER trên VLSP 2018. Đây là mô hình nhận diện thực thể, không phải LLM sinh văn bản. [Cấu hình chính thức](https://huggingface.co/NlpHUST/ner-vietnamese-electra-base/blob/main/config.json) có nhãn PERSON và ORGANIZATION; bộ trích chỉ nhận hai loại này khi điểm đạt ngưỡng. Điểm NER là điểm của thực thể, không phải độ tin cậy ghép khách hàng. Chất lượng trên nội dung ngân hàng, đặc biệt tên không dấu, cần đánh giá trên dữ liệu thực tế; chưa có benchmark mới cho thay đổi này.

NER chỉ được gọi trong **đối soát**, tách khỏi `Core.normalize()`, `prepare_batch()` và `learn()`. Bộ chuẩn hóa số, semantic text, fingerprint, metadata `feature_extractor` và schema giữ nguyên. Không cần SQL hoặc học lại kho chỉ để bật NER. Luồng học vẫn bỏ ngày/kỳ theo quy tắc hiện có.

Các bước trích và sử dụng tên:

1. Tạo một bản nội dung dành riêng cho NER: che token có chữ số và đổi dấu phân cách sang khoảng trắng, giữ nguyên độ dài/vị trí. Nội dung gốc và các số dùng để so khớp không bị sửa.
2. Với bố cục MB đã xác định, `app/bank_content.py` kiểm tra toàn bộ cấu trúc, ngày giờ hợp lệ, ngày lặp lại và tên đơn vị cấp nước. Trường tên sau kỳ được đọc riêng; đơn vị nhận tiền bị che trong đầu vào NER. Thêm một đầu vào ngữ cảnh người chuyển tiền để hỗ trợ nhận diện tên không dấu. Bộ đọc này không suy vai trò của các số đứng trước kỳ.
3. Chạy token-classification theo nhóm, tokenizer nhanh và cửa sổ chồng lấn cho nội dung dài. Lấy tên bằng offset từ **văn bản gốc**, không lấy chuỗi token đã sửa, không sinh thêm dấu hay ký tự. Lọc tên có chữ số, tên đơn vị nhận tiền, tên quá dài/ngắn và kết quả dưới ngưỡng.
4. Giữ tên đầy đủ của trường ngân hàng nếu NER chỉ nhận một phần. Ghi nguồn `bank_field`, `ner` hoặc `bank_field+ner`; nguồn cuối chỉ dùng khi NER nhận đúng cả tên đó. Ưu tiên trường ngân hàng, rồi PERSON, rồi ORGANIZATION nếu không có PERSON. Có nhiều tên thì `extracted_name` để trống để người dùng chọn.
5. Tên giúp tạo tập ứng viên qua posting `name:` và RRF. `matched_extracted_names` trong evidence ghi những tên trùng toàn bộ bí danh đã học sau khi chuẩn hóa chữ. NER không trực tiếp cộng điểm customer/auto-accept, không tạo ID mới, không bỏ qua mâu thuẫn số hay khách hàng cạnh tranh. Các điều kiện chấp nhận hiện có vẫn kiểm tra nội dung và định danh riêng.
6. Lưu thông tin trích tên trong JSONL kết quả ngoài PostgreSQL, hiển thị trên giao diện và xuất CSV. Chỉ khi con người xác nhận mã/tên mới gọi luồng học thông thường.

#### Cấu hình và vận hành

Cài các phụ thuộc cập nhật bằng `python -m pip install -r requirements.txt` trong môi trường Python đang chạy ứng dụng, rồi khởi động lại dịch vụ. Trên Linux có thể dùng `python3` nếu đó là lệnh Python của môi trường; Windows dùng Python trong venv như hướng dẫn cài đặt.

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `NER_ENABLED` | `true` | Bật nhận diện tên trong đối soát. `false` vẫn đọc trường tên MB theo cấu trúc. |
| `NER_MODEL` | `NlpHUST/ner-vietnamese-electra-base` | Mô hình token classification có nhãn PERSON/PER và tokenizer nhanh. |
| `NER_REVISION` | `main` | Revision trên Hugging Face; có thể ghim commit để vận hành ổn định. |
| `NER_MODEL_CACHE` | `runtime/models/ner` | Thư mục cache, dùng được trên Windows/Linux. |
| `NER_DEVICE` | `cpu` | Thiết bị chạy NER. |
| `NER_MIN_SCORE` | `0.85` | Ngưỡng nhận thực thể; từ 0 đến 1. |
| `NER_BATCH_SIZE` | `8` | Số nội dung mỗi nhóm; từ 1 đến 32. |

Mô hình tải lười ở lần đối soát đầu tiên; chỉ tải trọng số/tokenizer, nội dung chuyển tiền được xử lý tại máy chủ ứng dụng. Theo [danh sách tệp chính thức](https://huggingface.co/NlpHUST/ner-vietnamese-electra-base/tree/main), tệp `model.safetensors` khoảng **532 MB**. Đây là kích thước trọng số tải xuống, không phải RAM khi chạy: RAM còn có encoder embedding, tensor trung gian và các phần khác của ứng dụng. Chưa đo RAM trong repository cho cấu hình này. Mô hình giữ trong bộ nhớ sau khi tải; cache thực thể tối đa 2.048 nội dung và xử lý theo nhóm.

Lần tải đầu cần kết nối để lấy model; những lần sau dùng cache. Code yêu cầu trọng số safetensors, không chạy remote code hoặc Java. NER tải/chạy lỗi sẽ ghi cảnh báo kỹ thuật và dùng trường ngân hàng nếu có; các bước đối soát còn lại tiếp tục. Nếu tải model thất bại, xử lý nguyên nhân kết nối/phụ thuộc/cấu hình rồi khởi động lại để thử tải lại. Nếu một nhóm suy luận lỗi, thử từng nội dung; lỗi một nội dung không làm dừng cả tệp. Nút dừng tác vụ được kiểm tra giữa các nhóm; không ngắt giữa chừng một lần tải hoặc suy luận đang chạy.

Tệp kết quả cũ chỉ bổ sung tên từ bố cục ngân hàng khi đọc, không tự tải NER hoặc phân loại lại. Kỳ thanh toán được đọc riêng trong `app/payment_period.py`, hỗ trợ trường `MM/YYYY` đứng giữa dấu chấm khi có ngữ cảnh tiền nước và trường kỳ MB hợp lệ; không lấy ngày giờ trong mã giao dịch làm kỳ.

## 6. Reranking theo từng mẫu

| Feature | Trọng số |
| --- | ---: |
| Khách hàng: ID/tên/hợp đồng/token đã xác nhận | 0.30 |
| Người/kênh trả tiền | 0.10 |
| Nội dung | 0.20 |
| Cấu trúc và thứ tự | 0.15 |
| Số khớp chính xác | 0.15 |
| Bằng chứng lịch sử | 0.10 |

```text
text = 0.65 × lexical_template_similarity + 0.35 × vector_cosine
weighted_score = Σ weight(feature) × feature_value
history = 0.5 × pattern_confidence
        + 0.5 × min(1, log(1 + seen_count) / log(10))
```

Điểm số là giá trị lớn nhất của đóng góp exact-value hợp lệ và mức đồng thuận số ổn định theo thứ tự. Exact-value chỉ góp khi trùng vai trò, giá trị đầy đủ, định dạng, vị trí đã đối chiếu và template tương thích. Tham chiếu/giao thức/tài khoản nhận/số chưa xác định không tự được dùng làm bằng chứng định danh dương.

Bên cạnh điểm trọng số, có các điều kiện bằng chứng mạnh để nâng điểm: ID đã biết + nội dung phù hợp/mục đích tiền nước; hợp đồng duy nhất + đúng mẫu; token khách hàng đã được xác nhận; mã chữ/số duy nhất với toàn bộ định danh đúng thứ tự; tài khoản trả tiền duy nhất kèm nội dung/địa điểm phù hợp; hoặc tên đã xác nhận và nội dung đặc trưng thuộc duy nhất một khách hàng.

Các điều kiện nâng điểm vẫn chịu kiểm tra mâu thuẫn và loại trừ ở bước tiếp theo.

## 7. Ưu tiên precision và kiểm tra số theo thứ tự

Mẫu và giao dịch được so bằng sequence vai trò/định dạng, không dùng một tập số bỏ thứ tự.

- Nếu sequence ổn định trùng và template phù hợp, các tham chiếu biến đổi có thể được thêm/bớt mà vẫn đối chiếu định danh theo đúng vai trò. Giao diện hiển thị cả slot hiện tại và slot lịch sử khi chúng khác nhau.
- Mã hợp đồng khác nhau chặn ghép tự động.
- Mã chữ/số hoặc số cơ sở thiếu, thêm, đổi giá trị, đổi vai trò/định dạng hoặc chưa đối chiếu được đúng thứ tự chặn ghép tự động, kể cả khi tài khoản hoặc vector rất giống. Các trường biến đổi như tham chiếu ngân hàng, số hóa đơn và lượng tiêu thụ vẫn được xử lý theo vai trò riêng; không áp dụng quy tắc này cho mọi con số.
- Token số trần đã được gắn với khách hàng còn phải trùng vị trí tuyệt đối, vai trò, định dạng và giá trị đầy đủ, và có **một chủ sở hữu duy nhất trên toàn kho** mới được nâng điểm như định danh chắc chắn.
- Nếu số trần đã xác nhận trong mẫu cũ bị mất, đổi giá trị/vị trí/vai trò, tài khoản hoặc nội dung giống không được dùng để vượt qua mâu thuẫn. Ngoại lệ: giao dịch đã có IDKH rõ ràng khớp duy nhất với hồ sơ, nên không cần dùng số trần cũ để chứng minh khách hàng.
- Kiểm tra các chủ sở hữu đã xác nhận trên toàn kho trước khi LIMIT ứng viên. Ví dụ IDKH thuộc A nhưng hợp đồng hoặc mã chữ/số đã xác nhận thuộc B → manual 0%, không để một đường tìm kiếm che khuất mâu thuẫn. Token dùng chung có thể hỗ trợ tìm kiếm nhưng không tự trở thành bằng chứng duy nhất.
- Tài khoản/thẻ dùng chung không đủ để xác lập khách hàng.
- Tên tổ chức chung hoặc template chuyển tiền phổ biến không đủ để xác định đồng hồ.
- Một giao dịch có nhiều mã khách hàng cần phân bổ thủ công; không tự chọn một mã trong danh sách.
- ID rõ ràng nhưng không có trong knowledge → khách hàng rỗng, điểm 0%, kiểm tra thủ công.
- Không có bằng chứng khách hàng đáng tin → điểm 0%, kiểm tra thủ công; có thể hiển thị mẫu gần nhất để người dùng tham khảo.
- Hard negative áp dụng cho cặp nội dung chuẩn hóa/khách hàng đã bị từ chối; ứng viên đó bị hạ về 0.

Sau khi chấm các mẫu, hệ thống giữ **mẫu tốt nhất của mỗi khách hàng**. Các mẫu của cùng khách hàng không tạo một cuộc cạnh tranh giả. Nếu khách hàng kế tiếp đạt ngưỡng duyệt và chênh điểm dưới `MATCH_MARGIN` (mặc định 0.08), kết quả được hạ xuống vùng cần duyệt.

Mặc định `AUTO_THRESHOLD=0.90`, `REVIEW_THRESHOLD=0.60`, `MATCH_MARGIN=0.08`. Các guard có thể hạ điểm dưới vùng duyệt hoặc chuyển về manual 0%, nên không chỉ kiểm tra một ngưỡng tổng. Định danh mâu thuẫn vẫn phải manual nếu quản trị viên giảm ngưỡng; không dùng một mức trần cố định cho phép vượt guard khi thay cấu hình. Điểm là mức bằng chứng theo quy tắc, không phải xác suất đã hiệu chuẩn.

Kết quả chứa `retrieval` (phương pháp, số mẫu/khách hàng, số mẫu theo đường tìm), `retrieval_sources` (đường nào tìm thấy mẫu được chọn), `confirmed_identifier_owners`, `unique_confirmed_customer_tokens`, `identifier_guards`, `numeric_comparison`, `runner_up`, `score_margin` và `required_score_margin`. Giao diện giải thích bằng tiếng Việt, kèm mẫu gốc, nguồn dữ liệu và bảng đối chiếu số. Đây là bằng chứng đã kiểm tra, không phải lời suy luận do LLM sinh.

Thay đổi retrieval và guard này không đổi encoder, extractor hoặc schema; không cần học lại hay chạy migration. Theo yêu cầu người dùng, chưa chạy kiểm thử/benchmark phiên bản này. Chưa có số liệu mới để kết luận precision/F1 đã tăng; guard chặt hơn có thể làm nhiều dòng cần duyệt và tìm nhiều đường có thể tăng thời gian xử lý. Cần đánh giá bằng dữ liệu có nhãn tách khỏi kho học trước khi điều chỉnh trọng số/ngưỡng cho triển khai thực tế.

## 8. Xác nhận, sửa manual và học ngay

```mermaid
sequenceDiagram
    participant U as Người dùng
    participant API as FastAPI
    participant F as Tệp làm việc
    participant DB as PostgreSQL knowledge
    U->>API: Xác nhận hoặc sửa khách hàng rồi xác nhận
    API->>F: Lưu ý định xác nhận bền vững
    API->>DB: Begin + advisory lock
    API->>DB: Bằng chứng loại trừ khách hàng sai (nếu có)
    API->>DB: Hồ sơ, bí danh, payer, mẫu, số, postings, receipt
    DB-->>API: Commit
    API->>F: Ghi trạng thái confirmed/learned
    API-->>U: Đã xác nhận & học
```

`Core.review()` dùng `Core.learn()` ngay. Nó không đợi nút cập nhật hoặc một đợt import khác. Người dùng phải bấm xác nhận; thay đổi nội dung ô hoặc chọn checkbox để đưa vào danh sách không tự học.

Nếu sửa A thành B, negative cho A và positive cho B nằm trong cùng transaction. Bí danh do người dùng xác nhận cũng được cập nhật vào khóa tìm kiếm. Khi chỉ từ chối, không tạo mẫu dương.

Nếu PostgreSQL commit xong nhưng ghi trạng thái file bị gián đoạn, ý định còn trong `intents/`. Khi khởi động lại hoặc bấm tiếp tục, hệ thống lặp lại cùng nội dung với cùng receipt. Positive receipt chống học hai lần; negative receipt theo hành động cũng chống ghi loại trừ hai lần.

### Chống trùng

- Excel xác nhận: receipt từ nội dung, khách hàng, ngày, số tiền và dòng nguồn; file import hoàn tất còn được nhận diện bằng SHA-256 toàn bộ file.
- Xác nhận trên web / CSV đã học / thêm mẫu trực tiếp: receipt từ nội dung gốc, mã khách hàng chính thức, ngày, số tiền và kênh trả tiền.
- Hai dòng hoàn toàn trùng các trường trên được xử lý bảo thủ như cùng bằng chứng học. Số lần gặp không tăng giả vì import/export lại.
- Receipt chỉ lưu hash, khách hàng và kỳ; không lưu toàn bộ lịch sử kết quả lọc.

### Confidence theo kỳ

Mẫu, payer, slot và giá trị số mới bắt đầu ở 0.20. Xuất hiện ở kỳ mới hợp lệ: `c_new = c + 0.30 × (1 - c)`. Giá trị vắng mặt ở kỳ mới của cùng mẫu: `c_new = c × 0.7`. Giá trị dưới 0.05 không tham gia phần exact-value đang dùng. Nhiều bản sao trong cùng kỳ không tạo thêm độ tin cậy theo kỳ. Ngày không xác định không được dùng để giả lập tái xuất hiện qua tháng.

## 9. Giới hạn thực tế

- Chưa có bộ reranker học máy hoặc xác suất được hiệu chuẩn; hiện tại là weighted/rule reranking.
- Retrieval bị giới hạn số mẫu; có thể bỏ sót khi một hồ sơ có rất nhiều biến thể. Giới hạn có thể cấu hình, nhưng không nên nới tự động mà bỏ kiểm tra số.
- Kết quả trong tệp làm việc là snapshot của lần đối soát, không tự đổi theo knowledge mới. Muốn tính lại thì tạo một tác vụ đối soát mới.
- Không có số F1 mới cho phiên bản quản trị này: không chạy lại benchmark theo yêu cầu người dùng. Báo cáo cũ trong `reports/` thuộc phiên bản đã đo trước đó.
- Tác vụ và tệp làm việc cần chạy trong một process Uvicorn. Không sử dụng nhiều worker cùng ghi một thư mục working.
