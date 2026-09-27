# PLAN XÂY DỰNG HỆ THỐNG LỌC HÓA ĐƠN BẰNG HYBRID RAG + RERANKING

## 1. Mục tiêu hệ thống

Xây dựng phần mềm hỗ trợ lọc/phân loại hóa đơn hoặc giao dịch của **tháng hiện tại** dựa trên dữ liệu lịch sử các tháng trước.

Hệ thống có 2 chức năng chính:

1. **Cập nhật Data Knowledge**
   - Đọc dữ liệu hóa đơn/giao dịch mới.
   - Chuẩn hóa dữ liệu.
   - Phân loại giao dịch theo khách hàng.
   - Sau khi được xác nhận, cập nhật lại knowledge base.

2. **Tìm kiếm và phân loại giao dịch**
   - Search theo tên khách hàng.
   - Search theo đơn vị trả tiền: ngân hàng, ví điện tử, đơn vị trung gian.
   - Search theo mẫu nội dung chuyển tiền trong lịch sử.
   - Search theo cấu trúc text/số và thứ tự xuất hiện.
   - Dùng Hybrid Retrieval + Reranking để tính độ phù hợp.

---

## 2. Kiến trúc tổng thể

```text
Excel tháng hiện tại
        |
        v
+---------------------+
|  Streaming Ingest   |
| đọc theo batch      |
+----------+----------+
           |
           v
+---------------------+
| Preprocessing       |
| normalize text      |
| detect number/date  |
| preserve order      |
+----------+----------+
           |
           +-------------------------+
           |                         |
           v                         v
+---------------------+      +----------------------+
| Structured Search   |      | Vector Search        |
| exact / trigram     |      | semantic similarity  |
| customer / payer    |      | transaction template |
+----------+----------+      +----------+-----------+
           |                            |
           +-------------+--------------+
                         |
                         v
                +------------------+
                | Candidate Top-K  |
                +--------+---------+
                         |
                         v
                +------------------+
                | Reranker         |
                | text             |
                | customer         |
                | payer            |
                | number           |
                | structure/order  |
                | history weight   |
                +--------+---------+
                         |
                         v
                +------------------+
                | Classification   |
                +--------+---------+
                         |
          +--------------+--------------+
          |                             |
          v                             v
     Auto Accept                    Human Review
          |                             |
          +--------------+--------------+
                         |
                         v
                +------------------+
                | Update Knowledge |
                +------------------+
```

---

## 3. Dữ liệu đầu vào (dữ liệu mẫu trong `Data/` folder)

Nguồn dữ liệu ban đầu:

- File chưa lọc: `Ngan hang ngay thang 7-2026.xlsx`
- File đã lọc: `Ngan hang thang 7-2026 FN.xlsx`

Trong đó:

- File chưa lọc dùng làm input.
- File FN dùng làm ground truth để kiểm tra độ chính xác.
- Giai đoạn đầu tập trung vào sheet **BIDV**.

---

## 4. Yêu cầu xử lý file lớn

Không được load toàn bộ file Excel vào RAM.

### Cách xử lý

Đọc file theo streaming/batch:

```text
Excel
  |
  v
1000-5000 rows/batch
  |
  v
Normalize
  |
  v
Retrieve + Rerank
  |
  v
Write result
  |
  v
Release memory
```

Mục tiêu RAM:

```text
O(batch_size)
```

thay vì:

```text
O(total_rows)
```

---

## 5. Chuẩn hóa nội dung giao dịch

### Ví dụ dữ liệu gốc

```text
CTY ABC TT HOA DON 123456 THANG 07/2026 HD 998812
```

### Sau normalize

```xml
<text>cty abc tt hoa don</text>
<num1>123456</num1>
<text>thang</text>
<date>07/2026</date>
<text>hd</text>
<num2>998812</num2>
```

Sau khi bỏ phần ngày tháng không hữu ích:

```xml
<text>cty abc tt hoa don</text>
<num1>123456</num1>
<text>hd</text>
<num2>998812</num2>
```

Template dùng cho retrieval:

```text
cty abc tt hoa don <NUM_1> hd <NUM_2>
```

---

## 6. Cấu trúc lưu transaction

Mỗi giao dịch nên lưu:

```json
{
  "raw": "CTY ABC TT HOA DON 123456 THANG 07/2026 HD 998812",
  "normalized": "cty abc tt hoa don 123456 hd 998812",
  "template": "cty abc tt hoa don <NUM_1> hd <NUM_2>",
  "segments": [
    {
      "type": "text",
      "value": "cty abc tt hoa don",
      "position": 0
    },
    {
      "type": "number",
      "slot": 1,
      "value": "123456",
      "position": 1
    },
    {
      "type": "text",
      "value": "hd",
      "position": 2
    },
    {
      "type": "number",
      "slot": 2,
      "value": "998812",
      "position": 3
    }
  ]
}
```

Mục tiêu quan trọng:

> Không làm mất thứ tự text và number.

---

## 7. Phân loại số

Không coi tất cả number là giống nhau.

Phân loại thành:

```text
NUMBER
|- DATE
|- AMOUNT
|- ACCOUNT_ID
|- CUSTOMER_ID
|- REFERENCE_ID
|- UNKNOWN_NUMBER
```

### Ví dụ

```text
123456       -> customer/reference id
072026       -> month/year -> noise
500000       -> amount
00123456789  -> account/customer id
```

### Weight gợi ý

```text
DATE            = 0
AMOUNT          = rất thấp
UNKNOWN_NUMBER  = thấp
stable ID       = cao
```

---

## 8. Xử lý ngày tháng

Không xóa số chỉ bằng regex đơn giản.

Phải detect date theo context:

```text
07/2026
07-2026
072026
T07
THANG 07
THANG 7 NAM 2026
T7/26
```

Sau đó đánh nhãn:

```text
DATE
```

và loại khỏi retrieval feature.

Tuy nhiên vẫn giữ trong `raw` để audit.

---

## 9. Data Knowledge Model

Không lưu knowledge chỉ dưới dạng câu transaction.

Mỗi customer nên có profile:

```text
Customer Profile
|
|- Canonical name
|- Aliases
|- Payer evidence
|- Text pattern evidence
|- Structural pattern evidence
|- Stable-number evidence
|- Variable-number pattern
|- Historical confidence
```

---

## 10. Database đề xuất

### Phương án ưu tiên

```text
PostgreSQL + pgvector
```

Lý do:

- relational search tốt
- JSONB
- pg_trgm
- full-text search
- vector search
- dễ JOIN customer / payer / pattern

### Schema gợi ý

#### customers

```text
id
canonical_name
normalized_name
```

#### customer_aliases

```text
id
customer_id
alias
normalized_alias
confidence
```

#### payer_entities

```text
id
customer_id
payer_name
payer_type
confidence
seen_count
last_seen
```

#### transaction_patterns

```text
id
customer_id
payer_id
normalized_text
template_text
segments JSONB
embedding VECTOR
seen_count
last_seen
confidence
```

#### numeric_features

```text
id
pattern_id
slot_index
numeric_value
numeric_type
confidence
seen_count
missing_count
last_seen
```

---

## 11. Hybrid Retrieval

Không search toàn bộ database bằng reranker.

### Stage 1 - Retrieve Candidate

Ví dụ database có:

```text
1,000,000 transactions
```

chỉ retrieve khoảng:

```text
Top 20 - Top 100 candidates
```

### Retrieval score ban đầu

```text
S_retrieve =
    0.35 * embedding_similarity
  + 0.35 * text/BM25 similarity
  + 0.20 * customer similarity
  + 0.10 * payer similarity
```

Embedding nên chạy trên transaction template:

```text
cty abc thanh toan hoa don <NUM> ma khach hang <NUM>
```

thay vì raw transaction.

---

## 12. Reranking

### Reranking score

```text
Score =
    wc * customer_score
  + wp * payer_score
  + wt * text_score
  + ws * structure_score
  + wn * number_score
  + wh * historical_score
```

### Weight khởi tạo

```text
customer        0.30
payer           0.15
text            0.25
structure       0.15
number          0.05
history         0.10
```

Number được đặt weight thấp ở giai đoạn đầu.

---

## 13. Structural / Order Similarity

Không chỉ so text.

Ví dụ:

```text
ABC <NUM1> THANH TOAN <NUM2>
```

encode thành:

```text
T N T N
```

Sau đó so với candidate.

Ví dụ:

```text
query      = T N T N
candidate1 = T N T N
candidate2 = T T N N
```

Candidate 1 có score structure cao hơn.

Có thể dùng:

- LCS
- Levenshtein distance
- SequenceMatcher

Không cần LLM ở bước này.

---

## 14. Weight cho number theo lịch sử

### Khi number mới xuất hiện

Ví dụ:

```text
confidence = 0.20
```

### Khi xuất hiện lại

```text
confidence_new = confidence + alpha * (1 - confidence)
```

Ví dụ:

```text
alpha = 0.30
```

Kết quả:

```text
month 1 = 0.20
month 2 = 0.44
month 3 = 0.608
month 4 = 0.726
month 5 = 0.808
```

### Khi không xuất hiện

```text
confidence_new = confidence * decay
```

Ví dụ:

```text
decay = 0.7
```

Nếu:

```text
confidence < 0.05
```

thì remove khỏi active knowledge.

---

## 15. Exact Number và Number Slot

Phải track 2 loại confidence:

```text
exact_value_confidence
slot_confidence
```

### Case 1 - Stable Number

```text
TT KH 938271
TT KH 938271
TT KH 938271
```

=> `938271` là identifier mạnh.

### Case 2 - Variable Number

```text
TT HD 938271
TT HD 194821
TT HD 382918
```

=> giá trị cụ thể không quan trọng.

Nhưng pattern:

```text
TT HD <NUM>
```

rất ổn định.

Ví dụ lưu:

```json
{
  "slot": 1,
  "slot_confidence": 0.96,
  "values": {
    "938271": 0.15,
    "194821": 0.11,
    "382918": 0.08
  }
}
```

---

## 16. Classification Threshold

Không chỉ dùng yes/no.

Chia thành 3 vùng:

```text
score >= 0.90
    -> AUTO ACCEPT

0.60 <= score < 0.90
    -> HUMAN REVIEW

score < 0.60
    -> REJECT
```

Threshold thực tế sẽ được tune bằng ground truth.

---

## 17. Knowledge Update Flow

Không update knowledge trực tiếp từ prediction.

Flow:

```text
transaction
    |
    v
prediction
    |
    v
confidence
    |
    v
human confirmation
    |
    v
CONFIRMED
    |
    v
update knowledge base
```

Lý do:

Prediction sai nếu được đưa ngay vào KB sẽ gây:

```text
wrong prediction
      |
      v
wrong knowledge
      |
      v
retrieval lại knowledge sai
      |
      v
model càng sai nhưng confidence cao
```

---

## 18. Negative Evidence

Human feedback không chỉ dùng cho positive case.

Nếu system match nhầm customer:

```text
transaction X -> customer A
```

nhưng người dùng sửa thành:

```text
customer B
```

thì:

- tăng evidence cho B
- giảm evidence pattern tương ứng của A
- lưu hard-negative pair

Hard negative này rất hữu ích khi train reranker sau này.

---

## 19. Live / Incremental RAG

Luồng theo tháng:

```text
Month 1 historical data
        |
        v
Knowledge V1
        |
        v
Process Month 2
        |
        v
Human confirm
        |
        v
Knowledge V2
        |
        v
Process Month 3
        |
        v
Knowledge V3
```

Knowledge được update incremental, không rebuild toàn bộ mỗi lần.

---

## 20. Benchmark bằng file FN

### Input

```text
Ngan hang ngay thang 7-2026.xlsx
```

### Ground Truth

```text
Ngan hang thang 7-2026 FN.xlsx
```

Giai đoạn đầu:

```text
sheet BIDV
```

### Quy trình benchmark

```text
BIDV raw
  |
  v
model filter
  |
  v
predicted result
  |
  v
compare
  |
  v
BIDV FN ground truth
```

---

## 21. Metrics

Không dùng Accuracy làm metric chính.

Theo dõi:

```text
Precision
Recall
F1-score
False Positive
False Negative
```

Đặc biệt quan trọng:

```text
Auto-Accept Precision
```

Ví dụ với các sample có:

```text
score >= 0.90
```

mục tiêu là precision rất cao.

---

## 22. Giai đoạn phát triển

# Phase 1 - Data Investigation

Mục tiêu:

- đọc sample BIDV của raw file
- đọc BIDV của FN
- xác định các column quan trọng
- xác định rule lọc hiện tại
- tìm customer pattern
- tìm payer pattern
- tìm number/date pattern

Output:

```text
data_profile.md
```

---

# Phase 2 - Streaming Reader

Implement:

```text
Excel -> batch reader
```

Yêu cầu:

- không load full workbook
- configurable batch size
- support BIDV trước
- giữ row index gốc để audit

---

# Phase 3 - Normalization Engine

Implement:

```text
normalize_text()
detect_date()
detect_number()
segment_transaction()
build_template()
```

Output:

```text
raw
normalized
segments
template
```

---

# Phase 4 - Database

Setup:

```text
PostgreSQL
pgvector
pg_trgm
```

Create tables:

```text
customers
customer_aliases
payer_entities
transaction_patterns
numeric_features
transactions
feedback
```

---

# Phase 5 - Historical Knowledge Builder

Từ dữ liệu đã xác nhận:

```text
transactions
   |
   v
cluster by customer
   |
   v
extract aliases
   |
   v
extract payer patterns
   |
   v
extract transaction templates
   |
   v
calculate numeric confidence
```

---

# Phase 6 - Hybrid Retrieval

Implement:

1. Exact match
2. Customer name match
3. Payer match
4. BM25/full-text
5. Trigram search
6. Vector similarity

Merge candidate list.

---

# Phase 7 - Rule-based Reranker V1

Feature vector:

```text
customer_score
payer_score
text_score
vector_score
structure_score
number_score
history_score
```

Dùng weighted score trước.

---

# Phase 8 - Benchmark

Run BIDV raw -> prediction.

Compare với BIDV FN.

Tạo report:

```text
Precision
Recall
F1
FP examples
FN examples
```

Phân tích lý do lỗi.

---

# Phase 9 - Learnable Reranker

Sau khi đã có đủ labeled data:

Không cần LLM ngay.

Train model nhẹ:

```text
Logistic Regression
LightGBM
XGBoost
```

Input chính là các feature reranking.

Ví dụ:

```text
X = [
 customer_score,
 payer_score,
 text_score,
 vector_score,
 structure_score,
 stable_number_score,
 slot_score,
 historical_score
]
```

Output:

```text
P(transaction belongs to customer)
```

---

# Phase 10 - Human Review UI

UI gồm:

```text
Transaction raw
Predicted customer
Score
Matched historical pattern
Matched text
Matched numbers
Reason for score
Accept / Reject / Change Customer
```

Quan trọng: system phải explain được vì sao match.

Ví dụ:

```text
Customer: ABC
Score: 0.94

Evidence:
+ Customer alias match: 0.98
+ BIDV payer history: 0.91
+ Text template similarity: 0.93
+ Stable customer ID 928172: 0.95
+ Structure T-N-T: 1.00
```

---

# Phase 11 - Incremental Knowledge Update

Sau khi review:

```text
confirmed transaction
       |
       v
update customer aliases
update payer evidence
update text patterns
update number confidence
update slot confidence
decay missing features
```

Không cần rebuild toàn bộ DB.

---

## 23. API đề xuất

### POST /classify

Input:

```json
{
  "transaction": "..."
}
```

Output:

```json
{
  "customer_id": 123,
  "customer_name": "ABC",
  "score": 0.93,
  "decision": "review",
  "evidence": {}
}
```

### POST /feedback

```json
{
  "transaction_id": 1234,
  "correct_customer_id": 123,
  "accepted": true
}
```

### POST /knowledge/update

Update knowledge từ confirmed batch.

### POST /batch/classify

Dùng cho Excel lớn.

---

## 24. Technology Stack đề xuất

### Backend

```text
Python
FastAPI
```

### Database

```text
PostgreSQL
pgvector
pg_trgm
```

### Data Processing

```text
openpyxl read-only hoặc streaming Excel reader
Polars nếu cần dataframe
```

### Embedding

Bắt đầu với multilingual/sentence embedding nhẹ.

Không cần model quá lớn.

### Reranking

V1:

```text
rule-based weighted reranker
```

V2:

```text
LightGBM/XGBoost
```

V3 chỉ khi cần:

```text
Cross Encoder / LLM reranker
```

---

## 25. Ưu tiên phát triển

Thứ tự nên làm:

```text
1. Inspect BIDV raw + FN
2. Streaming Excel Reader
3. Normalizer
4. Text/number segmentation
5. Knowledge DB schema
6. Historical knowledge builder
7. Structured retrieval
8. Vector retrieval
9. Rule reranker
10. Benchmark với FN
11. Error analysis
12. Tune threshold/weight
13. Human review
14. Online update
15. Train learned reranker
```

Không nên bắt đầu bằng LLM.

---

## 26. MVP

MVP chỉ cần xử lý BIDV.

### MVP input

```text
BIDV sheet
```

### MVP features

```text
customer alias
text pattern
payer
number pattern
order pattern
embedding
```

### MVP output

```text
transaction
predicted customer
score
accept/review/reject
matched evidence
```

### MVP evaluation

Compare trực tiếp với BIDV trong FN.

---

## 27. Mục tiêu cuối cùng

Hệ thống không đơn giản hỏi:

> "Câu giao dịch này giống câu lịch sử nào?"

Mà phải trả lời:

> "Có bao nhiêu bằng chứng độc lập cho thấy giao dịch này thuộc khách hàng X?"

Các evidence bao gồm:

```text
customer name
payer
text semantics
transaction structure
stable IDs
number positions
historical frequency
past confirmed transactions
```

Đây là nền tảng chính của hệ thống Hybrid RAG + Reranking này.
