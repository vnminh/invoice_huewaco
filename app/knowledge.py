"""Confirmed imports persist independently of benchmark evaluation."""
from collections import Counter
import hashlib
import json
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.exc import DataError, IntegrityError

from .db import Customer, KnowledgeReceipt, Metadata, Pattern, sessions
from .excel import ConfirmedCsv, StreamingWorkbook, layout_guidance
from .core import learning_receipt
from .row_errors import ROW_DATA_ERRORS, RowErrors, prepare_rows


class ImportCancelled(Exception):
    """Cooperative stop; only earlier committed batches remain in knowledge."""
    def __init__(self, summary=None):
        super().__init__('Knowledge import cancelled')
        self.summary = summary or {}


def import_confirmed(engine, core, path, batch_size=1000, progress=None, before=None, check_cancel=None,
                     row_errors=None):
    factory = sessions(engine)
    counts = Counter()
    payers = Counter()
    periods = set()
    embedding_chunk = 32 if check_cancel else batch_size
    check_cancel = check_cancel or (lambda: None)
    row_errors = row_errors if row_errors is not None else RowErrors(file=Path(path).name)
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        while chunk := source.read(1024 * 1024):
            check_cancel()
            digest.update(chunk)
    check_cancel()
    key = 'import:' + digest.hexdigest()
    alias_extractor = core.learning_alias_signature()
    summary = {'file': Path(path).name, 'sha256': digest.hexdigest(), 'status': 'running',
               'alias_extractor': alias_extractor}
    with factory.begin() as session:
        record = session.get(Metadata, key)
        if record:
            # Preserve a completed import when repeating it; receipts prevent duplication.
            previous = json.loads(record.value)
            if previous.get('status') == 'completed':
                if previous.get('feature_extractor') != core.extractor.name or previous.get('embedding_model') != core.embedder.name:
                    raise ValueError('Imported file uses a different model; reimport confirmed history into a fresh database')
                if (previous.get('alias_extractor') == alias_extractor
                        and not previous.get('counts', {}).get('errors', previous.get('row_error_count', 0))
                        and not previous.get('counts', {}).get('name_extraction_unavailable', 0)):
                    return {**previous, 'already_imported': True}
                # Retry errored files; receipts prevent relearning successful rows.
            record.value = json.dumps(summary)
        else:
            session.add(Metadata(key=key, value=json.dumps(summary)))
    sheets, skipped_sheets, sheet_counts = [], [], Counter()
    try:
        check_cancel()
        is_csv = Path(path).suffix.lower() == '.csv'
        reader = ConfirmedCsv if is_csv else StreamingWorkbook
        with reader(path, check_cancel=check_cancel, row_errors=row_errors) as workbook:
            sheets = workbook.confirmed_sheets()
            skipped_sheets = [] if is_csv else workbook.skipped_sheets('confirmed')
            if progress:
                progress({'processed': 0, 'sheets': sheets, 'skipped_sheets': skipped_sheets,
                          'sheet_counts': {sheet: 0 for sheet in sheets}, 'phase': 'reading'})
            if not sheets:
                raise ValueError(layout_guidance('confirmed'))
            for sheet in sheets:
                for batch in workbook.batches(sheet=sheet, batch_size=min(batch_size, 32)):
                    check_cancel()
                    failed = prepare_rows(core, [row for row in batch if row.label_status == 'confirmed'],
                                          row_errors, check_cancel, embedding_chunk)
                    names_to_prepare = [row.raw for row in batch if row.label_status == 'confirmed' and id(row) not in failed]
                    core.prepare_names(names_to_prepare, check_cancel)
                    batch_counts, batch_payers, batch_periods = Counter(), Counter(), set()
                    with factory.begin() as session:
                        if engine.dialect.name == 'postgresql':
                            session.execute(text('SELECT pg_advisory_xact_lock(731026)'))
                        # Configuration failures affect every row; do not misreport them as bad input.
                        core.check_model(session, writing=True)
                        for row in batch:
                            check_cancel()
                            batch_counts['processed'] += 1
                            if row.label_status != 'confirmed':
                                batch_counts[row.label_status] += 1
                                continue
                            if id(row) in failed:
                                continue
                            if before and (not row.date or row.date[:10] >= before):
                                raise ValueError('Training must precede all test transactions')
                            try:
                                with session.begin_nested():
                                    if not row.customer_id:
                                        raise ValueError('Dòng học thiếu mã khách hàng đã xác nhận.')
                                    customer_id = core.ensure_customer(session, row.customer_id, row.customer_name).id if is_csv else row.customer_id
                                    receipt = learning_receipt(row.raw, customer_id, row.date, row.amount, row.payer) if is_csv else None
                                    learned = core.learn(session, row, customer_id, row.customer_name, receipt_key=receipt)
                                    if core.extract_names(row.raw)['name_extraction']['status'] == 'unavailable':
                                        batch_counts['name_extraction_unavailable'] += 1
                                    # Surface all pending inserts while this row's savepoint is active.
                                    session.flush()
                            except (*ROW_DATA_ERRORS, DataError, IntegrityError) as error:
                                row_errors.record(row.sheet, row.row_index, error, stage='learn')
                                continue
                            batch_periods.add(row.date[:7])
                            batch_payers[row.payer] += 1
                            batch_counts['confirmed'] += 1
                            batch_counts['learned'] += int(learned)
                        check_cancel()
                    counts.update(batch_counts)
                    sheet_counts[sheet] += batch_counts['processed']
                    payers.update(batch_payers)
                    periods.update(batch_periods)
                    if progress:
                        progress({**dict(counts), 'sheet_counts': dict(sheet_counts), 'skipped_sheets': skipped_sheets,
                                  **row_errors.summary()})
        check_cancel()
    except ImportCancelled:
        summary.update(status='cancelled', sheets=sheets, sheet_counts=dict(sheet_counts), skipped_sheets=skipped_sheets,
                       periods=sorted(periods), payers=dict(payers),
                       feature_extractor=core.extractor.name, embedding_model=core.embedder.name,
                       counts={**dict(counts), 'errors': row_errors.count}, **row_errors.summary())
        with factory.begin() as session:
            session.get(Metadata, key).value = json.dumps({k: v for k, v in summary.items() if k != 'row_errors'}, ensure_ascii=False)
        raise ImportCancelled(summary) from None
    summary.update(status='completed', sheets=sheets, sheet_counts=dict(sheet_counts), skipped_sheets=skipped_sheets,
                   periods=sorted(periods), payers=dict(payers),
                   feature_extractor=core.extractor.name, embedding_model=core.embedder.name,
                   counts={**dict(counts), 'errors': row_errors.count}, **row_errors.summary())
    with factory.begin() as session:
        # Row indices/reasons are operational reports, stored in working files, not knowledge.
        session.get(Metadata, key).value = json.dumps({k: v for k, v in summary.items() if k != 'row_errors'}, ensure_ascii=False)
    return summary


def frozen_knowledge_info(engine, test_start):
    factory = sessions(engine)
    with factory() as session:
        imports = [json.loads(m.value) for m in session.scalars(select(Metadata).where(Metadata.key.like('import:%')))]
        completed = [i for i in imports if i.get('status') == 'completed']
        if any(i.get('status') != 'completed' for i in imports):
            raise ValueError('A knowledge import is unfinished; finish it before benchmarking')
        if not completed:
            raise ValueError('Import confirmed July knowledge into the database before running the August benchmark')
        # Also catches feedback updates that introduced the test month into knowledge.
        latest = session.scalar(select(func.max(KnowledgeReceipt.period)))
        if not latest or latest >= test_start[:7] or latest == 'unknown':
            raise ValueError('Knowledge contains unknown dates or test-month data; use a July-only database for this benchmark')
        return {'imports': completed, 'receipt_count': session.scalar(select(func.count(KnowledgeReceipt.id))),
                'customer_count': session.scalar(select(func.count(Customer.id))),
                'pattern_count': session.scalar(select(func.count(Pattern.id))), 'last_period': latest}
