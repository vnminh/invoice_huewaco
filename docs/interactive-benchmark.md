# Kiểm chứng kiến thức tương tác — 13/10/2026

## Phạm vi và cách tách dữ liệu

Đã chạy bộ kiểm tra chức năng/bảo vệ, kiểm tra trình duyệt và benchmark thực với encoder **`intfloat/multilingual-e5-small`**, CPU. Kho benchmark là SQLite riêng; **không kết nối, khởi tạo hay thay đổi PostgreSQL của người dùng**. NER được tắt trong phép đo này; bộ đọc tên theo bố cục ngân hàng vẫn hoạt động.

Nguồn dữ liệu:

- Học: `Data/Ngan hang thang 7-2026 FN.xlsx`, mọi sheet nhận diện được. SHA-256: `119d6133419335d280a371f62aeaaa51109084c77b405951101bdf19e0179948`.
- Đối soát: `Data/Ngan hang thang 08.2026.xlsx`, sheet BIDV.
- Nhãn chấm điểm: `Data/Ngan hang thang 8-2026 FN.xlsx`, sheet BIDV. **Không đưa các nhãn này vào học**.
- Mẫu validation: 1.000 dòng ghi có hợp lệ đầu tiên. Đã dùng mẫu này để tìm và sửa một đường ghép thiếu căn cứ; kết quả sau sửa trên chính mẫu này không được gọi là test độc lập.
- Mẫu holdout: 500 dòng hợp lệ tiếp theo, `--test-offset 1000`. Chạy sau khi cố định quy tắc; không sửa quy tắc dựa trên kết quả holdout trong đợt này. Đây là lát cắt liên tiếp theo tệp, chưa phải mẫu ngẫu nhiên đại diện mọi ngân hàng.

Ghép raw với nhãn FN bằng **kênh ngân hàng + nội dung đã chuẩn hóa chữ/dấu/khoảng trắng**. Nội dung lặp được gộp tập IDKH đã xác nhận; chưa có khóa tham chiếu giao dịch đáng tin cậy xuyên hai tệp. Vì vậy các giao dịch nhiều khách hàng và nội dung lặp cần xem riêng. Dòng không tìm thấy nhãn là *chưa được chấm*, không coi là kết quả sai hay giao dịch âm tính.

“Đã biết” được xác định từ khách hàng thực sự có trong kho sau học, không chỉ từ ID đọc được trong tệp nguồn. Khách hàng chưa biết vẫn phải duyệt thủ công, độ tin cậy 0%; nhận ra một chuỗi ID chưa biết không được tính là tự động đối soát đúng.

## Kết quả học

| Chỉ tiêu | Số lượng |
| --- | ---: |
| Dòng đã xử lý | 159.169 |
| Dòng có nhãn xác nhận | 158.982 |
| Bộ đếm learned | 158.952 |
| Dòng thu hộ tổng hợp được học gọn | 103.304 |
| Khách hàng thực sự có trong kho | 156.925 |
| Dòng lỗi riêng lẻ | 107 |
| Thời gian học | 1.666,377 giây, khoảng 27 phút 46 giây |
| Dung lượng SQLite | 1.212,9 MB |

Các bộ đếm học có phần giao nhau, không cộng thành một phân hoạch dòng. Có 18 trường hợp nhãn được duyệt không trùng mã trong nội dung vẫn được học. 107 dòng lỗi gồm **61 lỗi mã khách hàng không hợp lệ theo quy ước sáu số**, **46 lỗi số tiền**; không làm dừng toàn bộ nhập. Rà soát lại bằng bộ đọc ghi nhận đúng 107 dòng trên các sheet SCB, MB (2), VTB (2), VCB (2), AGR. Danh sách sheet/số dòng được giữ riêng để sửa nguồn.

Kho chỉ có kỳ học `2026-07`. Dung lượng/thời gian trên gồm toàn bộ sheet lịch sử; không chỉ BIDV.

## Phát hiện từ validation và thay đổi precision

Đường ghép cũ có thể tự chấp nhận một IDKH có trong registry/roster khi nội dung nói đến tiền nước, dù chưa có mẫu riêng phù hợp để kiểm tra. Khi học thêm các ngân hàng, 6 kết quả đi qua đường này: 5 đúng, **1 sai**. Đây là bằng chứng rằng thêm nhiều nguồn không tự làm kết quả đáng tin hơn.

Đã đổi thành **gợi ý cần duyệt** khi khách hàng chỉ có trong registry/roster và chưa có mẫu riêng đối chiếu. Không bỏ gợi ý; một lần người dùng xác nhận sẽ học mẫu riêng để lần sau có căn cứ. Kiểm tra hồi quy bao phủ cả chu trình này.

Ghi chú phiên bản: số đo dưới đây mô tả phiên bản tại thời điểm benchmark. Bản hiện tại bổ sung đối chiếu mẫu thu hộ dùng chung cùng ngân hàng/kênh cho khách hàng đã học qua bảng kê; không còn bắt buộc mẫu riêng. Vẫn không tự ghép chỉ từ IDKH tồn tại và câu tiền nước nếu thiếu mẫu phù hợp. Xem điều kiện trong [tài liệu so khớp](matching-and-learning.md); các số đo cũ không phải benchmark của thay đổi này.

| Lần đo trên 1.000 dòng validation | Tự chấp nhận đúng | Tự chấp nhận sai | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Học BIDV lịch sử, trước sửa guard | 45 | 0 | 100% | 5,073% | 0,09657 |
| Học mọi ngân hàng, trước sửa guard | 51 | 1 | 98,077% | 5,750% | 0,10863 |
| Học mọi ngân hàng, sau sửa guard | 46 | 0 | 100% | 5,186% | 0,09861 |

Recall/F1 dùng 887 dòng có đúng một khách hàng trong nhãn; 81 dòng nhiều khách hàng được theo dõi riêng, 32 dòng chưa có nhãn không được chấm. Các kết quả tự chấp nhận sai ở nhóm nhiều khách hàng, nếu có, vẫn được tính vào lỗi precision. Phiên bản sau sửa đánh đổi một phần recall để loại đường tự ghép thiếu bằng chứng. **Không tuyên bố F1 tăng mạnh** hoặc dùng validation đã điều chỉnh để chứng minh khả năng tổng quát.

## Holdout độc lập: 500 dòng tiếp theo

| Nhóm | Dòng có nhãn | Tự chấp nhận đúng | Sai | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Một khách hàng, toàn bộ | 429 | 37 | 0 | 100% quan sát | 8,625% | 0,15880 |
| Một khách hàng, đã biết | 144 | 37 | 0 | 100% quan sát | 25,694% | 0,40884 |

Trong 429 dòng một khách hàng, **285 dòng thuộc khách hàng chưa có trong kho**, nên không thể tự xác nhận theo chính sách hiện tại. F0.5 toàn nhóm một khách hàng là **0,32062**, phù hợp theo dõi ưu tiên precision hơn recall. Không so trực tiếp F1 của lát cắt này với validation để kết luận cải thiện: thành phần khách hàng đã biết/chưa biết khác nhau.

Các kết quả hỗ trợ người duyệt:

- Có danh sách ứng viên đã biết cho **219/429 dòng (51,049%)**; đúng ở vị trí đầu **113/429 (26,340%)**, xuất hiện trong 5 ứng viên đầu **130/429 (30,303%)**.
- Với 285 dòng khách hàng chưa biết, đọc được ID gợi ý đúng **207**, sai **1**: precision của *mã gợi ý* 99,519%, recall 72,632%. Đây **không phải precision đối soát tự động**; các dòng vẫn phải kiểm tra thủ công.
- 46 dòng nhiều khách hàng và 2 dòng thu hộ tổng hợp được ghi riêng. Có 6 tập phân bổ trùng hoàn toàn nhãn, 1 tập chỉ khớp một phần. Chưa đủ mẫu để kết luận chất lượng phân bổ hàng loạt; không tự chia số tiền.
- 23 dòng chưa có nhãn không được chấm. Benchmark chưa đo accuracy kỳ thanh toán hay NER.

Hiệu năng holdout trên kho SQLite 1,21 GB: tổng 114,542 giây; latency mỗi lần `classify` p50 **5,42 ms**, p95 **742,13 ms**, p99 **832,04 ms**. Tổng thời gian còn gồm đọc tệp và chuẩn bị batch; không suy ra throughput chỉ từ p50.

**37 kết quả tự chấp nhận là mẫu nhỏ.** Chưa thấy sai trong mẫu này không đảm bảo precision 100% khi triển khai. Recall còn thấp; hướng cải thiện tiếp theo là bổ sung ví dụ xác nhận cho khách hàng đã có trong roster, học danh bộ có nguồn, và đánh giá theo từng ngân hàng/thu hộ/tự trả/nhiều người. Không tăng tự ghép chỉ để làm F1 đẹp hơn.

## Kiểm tra chức năng và bảo vệ

- **70 kiểm tra tự động qua**, bao gồm parser/học/so khớp và tương tác: đăng nhập, origin, giới hạn đăng nhập sai, trường thừa, truy vấn có ký tự đặc biệt, bản sửa hết hạn, phạm vi cập nhật hàng loạt thay đổi dù cùng số lượng, audit/undo và bí danh đã dùng.
- Kiểm tra bằng Chromium thực: thêm khách hàng/mẫu trước khi mở quản lý dữ liệu, thêm bí danh khách hàng/ngân hàng, hoàn tác bí danh chưa dùng, cập nhật loại thanh toán sau lọc. Không ghi nhận lỗi JavaScript hoặc tràn chiều ngang ở cửa sổ 1.280 và 480 px.
- Dựng SQL bằng ORM/mock dialect PostgreSQL, kiểm tra cú pháp Python/JavaScript và whitespace. **Chưa thực thi SQL, đo HNSW/GIN hay integration trên PostgreSQL; chưa chạy trên Windows thực tế.** Những phép đo này cần làm tại môi trường triển khai.

## Tái lập

Từ thư mục project, môi trường Python đã cài requirements và encoder. Kho output phải mới; script từ chối ghi đè kho đã có nếu không chỉ rõ `--reuse`.

Linux:

```bash
.venv/bin/python scripts/benchmark_all.py \
  --train 'Data/Ngan hang thang 7-2026 FN.xlsx' \
  --raw 'Data/Ngan hang thang 08.2026.xlsx' \
  --truth 'Data/Ngan hang thang 8-2026 FN.xlsx' \
  --out runtime/bench_interactive --sheets BIDV --all-train-sheets \
  --max-test-rows 1000 --no-ner
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_all.py `
  --train 'Data/Ngan hang thang 7-2026 FN.xlsx' `
  --raw 'Data/Ngan hang thang 08.2026.xlsx' `
  --truth 'Data/Ngan hang thang 8-2026 FN.xlsx' `
  --out runtime/bench_interactive --sheets BIDV --all-train-sheets `
  --max-test-rows 1000 --no-ner
```

Giữ bản `report.json` và `predictions.jsonl` validation trước khi đánh giá tiếp: các tệp báo cáo được ghi lại mỗi lần chạy. Kho `knowledge.sqlite` giữ nguyên với `--reuse`.

```bash
.venv/bin/python scripts/benchmark_all.py --reuse \
  --raw 'Data/Ngan hang thang 08.2026.xlsx' \
  --truth 'Data/Ngan hang thang 8-2026 FN.xlsx' \
  --out runtime/bench_interactive --sheets BIDV --all-train-sheets \
  --test-offset 1000 --max-test-rows 500 --no-ner
```

Trên PowerShell thay executable và dấu nối dòng như ví dụ trước. `--hash` chỉ là phép loại bỏ encoder ngữ nghĩa, không dùng để khẳng định chất lượng E5. Bỏ `--max-test-rows` để đo hết dòng hợp lệ; bỏ `--sheets` để đánh giá mọi sheet. Khi so sánh phiên bản, phải dùng cùng tập dòng, cùng encoder và kho học tương đương.

## Artifacts của lần chạy

[Metrics JSON không chứa nội dung giao dịch](benchmarks/interactive-2026-10-13.json) lưu cả bốn lần đo cùng mô tả phân tách. Những trường suggestion của baseline cũ được giữ nguyên nguồn, không dùng để so sánh nhóm khách hàng chưa biết vì baseline chưa tách bộ đếm suggestion theo nhóm.

Kho thử nghiệm và predictions có nội dung giao dịch nằm ngoài source tại `/tmp/huewaco-bench-20261013/` trên máy chạy; có thể bị hệ điều hành dọn:

- `bidv/report_initial.json`, `bidv/predictions_initial.jsonl`.
- `all_banks/report_initial.json`, `all_banks/predictions_initial.jsonl`.
- `all_banks/report_validation.json`, `all_banks/predictions_validation.jsonl`.
- `all_banks/report_holdout.json`, `all_banks/predictions_holdout.jsonl`.
- `all_banks/learning_errors_read.jsonl`: kết quả rà lại sheet/số dòng lỗi nhập.

Không chép toàn bộ SQLite/predictions vào Git. Benchmark là công cụ phát triển CLI; các route benchmark trên web vẫn tắt mặc định.

## Cập nhật: nhóm khách hàng cần kiểm tra

Đã bổ sung [nhóm theo bối cảnh](contextual-recommendations.md), rồi chạy lại đúng 500 dòng offset 1.000 trên kho tháng 7 đã cố định. Lát cắt này đã được phân tích để phát triển nhóm gợi ý, nên kết quả cập nhật là **kiểm tra hồi quy/validation, không phải holdout độc lập mới**. Encoder vẫn E5, CPU, NER tắt; nhãn tháng 8 chỉ chấm điểm.

Nhóm ưu tiên bối cảnh ngân hàng/mẫu/tên và giữ thêm gợi ý cá nhân liên quan để tránh loại mất khách hàng khi đổi cách ghi. Kết quả:

| Chỉ tiêu ở khách hàng đã biết, còn cần duyệt | Kết quả |
| --- | ---: |
| Số dòng | 107 |
| Nhóm có chứa khách hàng đúng | 99/107, 92,523% |
| Khách hàng đúng trong 6 người xem trước | 94/107, 87,850% |
| Trung vị kích thước nhóm | 7 người |
| Nhóm có tối đa 10 người | 81/107 |
| Nhóm trên 100 người | 22/107 |

Scope recall được chấm bằng tìm ID thật **trong tập nhóm đã tạo**, không dùng ID thật để tạo/xếp nhóm hoặc học. Sáu người xem trước khác tập đầy đủ có phân trang. Không diễn giải scope recall thành precision ghép người.

Tất cả **500 quyết định, ID dự đoán và điểm ban đầu giữ nguyên** so với lần trước: 37 tự chấp nhận đúng, chưa thấy sai, F1 một khách hàng 0,15880. Cải thiện này hỗ trợ người duyệt, chưa làm tăng tỷ lệ tự chấp nhận.

463 dòng manual đều có nhóm theo lịch sử/bối cảnh. Tuy nhiên toàn bộ các nhóm có trung vị 1.035 người, p95 1.830; riêng 285 khách hàng chưa biết có trung vị 1.432. Đây là giới hạn thực tế của mẫu ngân hàng chung khi chưa có định danh liên quan: không giả vờ một nhóm lớn đã xác định được người, UI báo phạm vi rộng, cho tìm kiếm/phân trang và vẫn giữ mã mới/0 điểm nếu khách hàng chưa biết. Không tự học hay xác nhận toàn nhóm. Với roster thu hộ tổng hợp, vẫn cần bảng kê.

Sau tối ưu truy vấn gộp các nguồn bằng CTE/tập ID có chỉ mục, latency `classify` p50 **27,41 ms**, p95 **750,47 ms**, p99 **834,05 ms**. Tổng thời gian benchmark **125,785 giây**, gồm **3,470 giây** truy vấn bổ sung chỉ để chấm membership của nhóm. Các số này vẫn là SQLite; chưa đo hiệu năng PostgreSQL hoặc Windows.

**78 kiểm tra qua**; Chromium kiểm tra tìm kiếm, phân trang, chọn rồi xác nhận học, UI 1.280/480 px không lỗi JavaScript/tràn trang. Các truy vấn nhóm còn được compile với dialect PostgreSQL trên fixture SQLite; không kết nối PostgreSQL, không chạy migration. Nhóm dùng các bảng hiện có nên không thêm schema.

[Metrics nhóm đầy đủ, không có nội dung giao dịch](benchmarks/contextual-groups-2026-10-13.json). Artifacts tạm của bản cuối: `all_banks/report_groups_optimized.json`, `all_banks/predictions_groups_optimized.jsonl` trong thư mục benchmark `/tmp/huewaco-bench-20261013/`. CLI tái lập vẫn là lệnh `--reuse --test-offset 1000 --max-test-rows 500 --no-ner` ở trên; báo cáo thêm `candidate_groups`.

## Kiểm tra giữ kiến thức sau cập nhật bộ nhớ

Các kết quả E5/78 kiểm tra ở trên thuộc bản trước `recurring_links`. Bản mới có 89 kiểm tra qua và replay 200 dòng FN trước/sau học thêm 1.000 dòng: giữ đủ 200 liên kết; 175 tự nhận đúng, 25 nhóm cần duyệt, không tự ghép sai. [Protocol và giới hạn](recurring-recognition.md), [báo cáo](benchmarks/retention-2026-10-13.json). Replay dùng hash/không NER, không phải F1 holdout. Không dùng số 37 auto hay F1 E5 cũ để khẳng định hiệu năng bản mới; cần chạy lại benchmark tháng độc lập khi có encoder. Schema hiện tại bổ sung migration 007; nhận xét “không thêm schema” ở trên chỉ áp dụng riêng tính năng nhóm gợi ý.
