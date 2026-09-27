# August BIDV development benchmark

All usable July FN labels were learned and persisted; only August raw BIDV was evaluated against August FN. No August labels entered prediction or knowledge updates.

| Measure | Previous best | Final rules + hash | Final rules + E5 |
|---|---:|---:|---:|
| Automatic precision | 100.000% | 100.000% | 100.000% |
| Automatic recall | 13.258% | 13.231% | 13.285% |
| Automatic F1 | 23.412% | 23.370% | 23.454% |
| Correct automatic matches | 988 | 986 | 990 |
| Wrong automatic matches | 0 | 0 | 0 |
| Known-customer automatic F1 | 81.451% | 81.353% | 81.549% |

Compared with the previous best, 14 rows gained automatic acceptance and 12 were moved to manual checking, for a net gain of 2 correct matches. The stricter contract/code rules have a recall cost; the net F1 increase is modest. E5 contributes four correct matches over the final hash ablation. Zero observed false positives applies only to this development data.

## Final behavior

- Model: `intfloat/multilingual-e5-small`, 384 dimensions, CPU. Vietnamese accents/names are retained; number values are masked in semantic input and compared separately. No Ollama or generative LLM.
- HD means hợp đồng in every form. Contract values, including leading zeros and mixed codes, must agree exactly.
- TKThe is payer-card evidence, never a customer ID; shared cards cannot identify a meter by themselves.
- Full alphanumeric codes and their numeric order are preserved. A uniquely owned exact mixed code can support acceptance only with matching text and the entire stable numeric sequence.
- Optional changing invoice/bank fields may be omitted only if the complete stable identity sequence keeps the same roles, formats, order, and contract values. Original/current historical slot indices remain in the explanation.
- Unknown explicit IDs/contracts and ambiguous identifying evidence return 0% / manual check. `ko` is skipped.
- The Vietnamese evidence dialog shows the stored July transaction/template and an ordered value/role comparison.

## Data and validation

12,036 July confirmations; 11,753 canonical customers. August: 8,342 raw BIDV rows; 7,452 single-customer positives; 1,438 known-customer positives. 6,014 positive rows belong to unseen customers, so required abstention caps overall recall at 19.297%.

45 regression tests passed; Chrome verified classification, the Vietnamese evidence table, confirmation/update, unknown 0%, Excel jobs, and desktop/mobile layouts.

Benchmarking used persisted development SQLite fixtures with the production relational models/scorer: `runtime/july_ordered_v5_e5.sqlite` and `runtime/july_ordered_v5_hash.sqlite`. July imports were closed and reopened; frozen snapshots matched before/after evaluation. PostgreSQL itself was not initialized or benchmarked here. Production remains PostgreSQL; run the supplied SQL yourself. Old hash/older-parser knowledge must be reimported into a fresh database. See [setup instructions](../README.md#retrieval-and-embeddings).

Final E5 benchmark duration: 79.551s; peak whole-process RSS: 928.52 MB (includes Python, model, batches, database and caches; not model-only RAM).

August errors informed engineering choices, so this is a development benchmark, not an untouched holdout.

[Final detailed report](semantic_august_2026/benchmark.md) · [Raw metrics](semantic_august_2026/benchmark.json) · [Hash ablation](ordered_hash_august_2026/benchmark.json) · [Previous best](before_semantic_2026/benchmark.json) · [CSV predictions](semantic_august_2026/predictions.csv) · [Per-row evidence](semantic_august_2026/evidence.jsonl)
