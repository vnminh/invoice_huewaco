# Workbook investigation

The initial plan targets BIDV. The final evaluation requested is **learn all usable July FN labels, then test August BIDV against August FN**.

| Workbook | Relevant sheets | Transaction rows |
|---|---|---:|
| Ngan hang ngay thang 7-2026.xlsx | BIDV, among 25 sheets | 7,988 |
| Ngan hang thang 7-2026 FN.xlsx | BIDV (only sheet), including BIDV/VNPAY/VNPT channels | 12,149 |
| Ngan hang thang 08.2026.xlsx | BIDV, among 27 sheets | 8,342 |
| Ngan hang thang 8-2026 FN.xlsx | BIDV (only sheet), including several payer channels | 11,481 |

## Column mapping

Raw BIDV statements have headers at row 12; data begins at row 14. Columns: B reference, C effective transaction date/time, D debit, E credit, I description. Bank account/customer metadata above the header belongs to Huewaco, not the paying customer.

FN sheets have headers at row 4. Columns: A bank transfer date (Excel serial), B actual date when supplied, C IDKH, D customer/payer display name, E payment amount, F CMA, G original bank narrative, H channel. August calls G `EBL`; header detection uses C `IDKH`, so both work. Transaction row numbers must be preserved from their original sheets.

July FN channels: BIDV 8,161, VNPAY 3,445, VNPT 543. Customer IDs retain leading zeros where supplied. The wire narrative can use a padded identifier while FN stores an integer; identifier lookup ignores leading-zero padding, while displayed IDs and original values remain intact.

## Labels to skip

Per the user's instruction, `ko` is skipped entirely; no customer, negative model, or negative label is learned from it. July has 105 such rows; August has 127. A small number of `ht/pd/th` entries are CMA assignments without an individual customer ID: July 8, August 8. These remain unresolved and are not used as customer profiles.

July therefore supplies **12,036 usable labeled rows** across all channels. They are learned even when a single bank narrative was distributed across multiple customers in reconciliation.

## Narrative evidence

Typical BIDV protocol:

```text
REM Tfr Ac:<payer account> O@L_040001_212501_0_0_<reference>_<IDKH>_<invoice code>_TT tien nuoc ky <month/year> ...
```

The IDKH slot is strong identifying evidence. Shared protocol fields such as `040001` and `212501` are not customer IDs. `Ac:` and `TKThe:` identify payer-account evidence. Invoice/reference codes and customer IDs have separate numeric types. Month/year, timestamp, amounts and transfer references must not substitute for customer identity.

Some payments contain names and prose without an explicit ID, and many use generic templates shared by numerous customers. Such templates require independent alias/payer/stable-number evidence. Missing known identity returns 0% confidence and manual checking.

## Reconciliation limits

FN is not row-aligned with the raw statement. One raw bank payment may have multiple customer allocations, and human edits can change its narrative. July contains 199 distinct FN descriptions without a raw exact-normalized match. August contains 330. A comparison of row numbers would be invalid.

The benchmark reconciles narratives using a disk-backed index, keeps date/number/order information, skips ko descriptions, and excludes multiple-customer/unresolved rows from single-customer classification. It reports all exclusions and matched versus unmatched counts. Precision/recall/F1 and auto-accept precision are the primary metrics; the August labels are never learned during testing.
