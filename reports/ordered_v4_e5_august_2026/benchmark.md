# BIDV benchmark

Read previously saved July knowledge; test August BIDV only; no training or knowledge writes during evaluation

Training: **Ngan hang thang 7-2026 FN.xlsx** (all labeled sheets/channels). Test: **Ngan hang thang 08.2026.xlsx**, sheet **BIDV**.

| Evaluation | Precision | Recall | F1 | TP | FP | FN |
|---|---:|---:|---:|---:|---:|---:|
| classification | 100.00% | 11.96% | 21.36% | 891 | 0 | 6561 |
| auto_accept | 100.00% | 11.96% | 21.36% | 891 | 0 | 6561 |
| seen_customers | 100.00% | 61.96% | 76.51% | 891 | 0 | 547 |
| seen_customers_auto_accept | 100.00% | 61.96% | 76.51% | 891 | 0 | 547 |
| filtering | 100.00% | 11.96% | 21.36% | 891 | 0 | 6561 |

Duration: 89.434 seconds. Process peak RSS: 1347.83 MB.

## Data counts

```json
{
  "raw_rows": 8342,
  "truth_rows": 11481,
  "truth_unmatched_descriptions": 330,
  "truth_unique_customers": 11193,
  "truth_ko_skipped_rows": 127,
  "truth_unresolved_rows": 8,
  "training_rows": 12036,
  "training_customers": 11753,
  "heldout_rows": 8342,
  "extractor_explicit_id": 6822,
  "heldout_positive": 7452,
  "unseen_customer_positives": 6014,
  "manual_check": 6575,
  "seen_customer_positives": 1438,
  "auto_accept": 891,
  "extractor_rules": 644,
  "extractor_not_used": 486,
  "heldout_negative": 500,
  "reject": 486,
  "ko_rows_skipped": 127,
  "ambiguous_rows_excluded": 255,
  "unresolved_rows_excluded": 8
}
```

## Limits and error analysis

- Training includes July confirmed labels only. August labels are never used for retrieval, reranking or knowledge updates.
- Unseen customer IDs and matches without reliable customer-specific evidence return 0% confidence and manual_check. Ground truth is used only for evaluation.
- FN includes edited descriptions and split payments. Multiple-customer matches are excluded from single-customer classification.
- ko rows are skipped entirely. ht/pd/th are unresolved CMA labels and excluded from single-customer evaluation.
- Unmatched raw descriptions are treated as filtered negatives; some may be edited FN positives. Negative labels need manual audit.
- Thresholds are fixed. Engineering changes were informed by errors in the August benchmark; reported improvements are development-set results, not a new untouched holdout.
- Real multilingual semantic embeddings are used. Semantic similarity cannot override conflicting contract IDs, identifier roles or numeric order.
- Peak RSS covers this entire process; XLSX shared strings, reconciliation and knowledge are disk-backed.

## Missing-customer reporting rule

Every positive FN customer absent from the July registry is reported at 0% with manual_check. This reporting rule uses evaluation labels after prediction; classifier metrics use untouched model outputs.

Unknown-customer rows reported at 0%: 6014. Nonzero model suggestions overridden for reporting: 0.

model_result in evidence.jsonl; model_score/model_decision/model_predicted_customer_id in predictions.csv

Low overall recall is expected when the holdout contains customers absent from the training knowledge. The seen-customers row isolates repeated customers, while overall classification counts abstention as a false negative. Wrong accepted customers count as both FP and FN. Auto-accept precision is undefined when no rows auto-accept.

See benchmark.json for FP/FN examples, predictions.csv for every tested row, and evidence.jsonl for the historical template and feature scores of each prediction.
