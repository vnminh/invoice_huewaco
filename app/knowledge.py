"""Confirmed imports persist independently of benchmark evaluation."""
from collections import Counter
import hashlib
import json
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.exc import DataError, IntegrityError

from .db import Customer, KnowledgeReceipt, Metadata, Pattern, sessions
from .excel import ConfirmedCsv, StreamingWorkbook, LAYOUT_VERSION, layout_guidance
from .core import id_key, learning_receipt
from . import id_slots
from .id_slots import BATCH_MIN_CUSTOMERS
from .normalize import fold
from .payment_templates import collection_channel, unique_transfer_reference
from .row_errors import ROW_DATA_ERRORS, RowErrors, prepare_rows


def learning_units(batches, core, size=32):
    """Yield (rows, split): split > 0 marks a provider batch-settlement run of ``split`` customers.

    Confirmed files list a wallet/collection settlement as consecutive rows sharing one
    narrative, one row per customer, with the collection channel in the bank/channel column.
    Such runs are learned compactly (registry, receipt and layout statistics only); every
    other row keeps full per-customer pattern learning.
    """
    pending, run, run_key = [], [], None

    def close(run):
        customers = {id_key(row.customer_id) for row in run}
        # Only collection services (MoMo, Payoo, VNPAY...) are stored compactly. An organisation
        # paying many meters at once keeps full per-customer learning for later allocation.
        if (len(customers) >= BATCH_MIN_CUSTOMERS and collection_channel(run[0].payer)
                and unique_transfer_reference(core.normalize(run[0].raw))):
            return [(run[start:start + size], len(customers)) for start in range(0, len(run), size)], []
        return [], run

    for batch in batches:
        for row in batch:
            key = ' '.join(fold(row.raw).split()) if row.label_status == 'confirmed' and row.customer_id else None
            if key is not None and key == run_key:
                run.append(row)
                continue
            if run:
                units, normal = close(run)
                yield from units
                pending.extend(normal)
            run, run_key = ([row], key) if key is not None else ([], None)
            if key is None:
                pending.append(row)
            while len(pending) >= size:
                yield pending[:size], 0
                pending = pending[size:]
    if run:
        units, normal = close(run)
        yield from units
        pending.extend(normal)
    for start in range(0, len(pending), size):
        yield pending[start:start + size], 0


class ImportCancelled(Exception):
    """Cooperative stop; only earlier committed batches remain in knowledge."""
    def __init__(self, summary=None):
        super().__init__('Knowledge import cancelled')
        self.summary = summary or {}


def import_confirmed(engine, core, path, batch_size=1000, progress=None, before=None, check_cancel=None,
                     row_errors=None, only_sheets=None):
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
               'alias_extractor': alias_extractor, 'layout_reader': LAYOUT_VERSION}
    with factory.begin() as session:
        record = session.get(Metadata, key)
        if record:
            # Preserve a completed import when repeating it; receipts prevent duplication.
            previous = json.loads(record.value)
            if previous.get('status') == 'completed':
                if previous.get('feature_extractor') != core.extractor.name or previous.get('embedding_model') != core.embedder.name:
                    raise ValueError('Imported file uses a different model; reimport confirmed history into a fresh database')
                if (previous.get('alias_extractor') == alias_extractor
                        and previous.get('layout_reader') == LAYOUT_VERSION
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
            sheets = [sheet for sheet in workbook.confirmed_sheets() if not only_sheets or sheet in only_sheets]
            skipped_sheets = [] if is_csv else workbook.skipped_sheets('confirmed')
            if progress:
                progress({'processed': 0, 'sheets': sheets, 'skipped_sheets': skipped_sheets,
                          'sheet_counts': {sheet: 0 for sheet in sheets}, 'phase': 'reading'})
            if not sheets:
                raise ValueError(layout_guidance('confirmed'))
            for sheet in sheets:
                for batch, split in learning_units(workbook.batches(sheet=sheet, batch_size=min(batch_size, 32)), core):
                    check_cancel()
                    failed = set() if split else prepare_rows(core, [row for row in batch if row.label_status == 'confirmed'],
                                                              row_errors, check_cancel, embedding_chunk)
                    if not split:
                        core.prepare_names([row.raw for row in batch if row.label_status == 'confirmed' and id(row) not in failed],
                                           check_cancel)
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
                                    if split:
                                        learned = core.learn_settlement(session, row, customer_id, row.customer_name, split,
                                                                        receipt_key=receipt)
                                        batch_counts['batch_settlement_rows'] += 1
                                    else:
                                        learned = core.learn(session, row, customer_id, row.customer_name, receipt_key=receipt)
                                    # Trusted label, but report when the payer wrote other IDs (audit only).
                                    written = {id_key(value) for value in core.normalize(row.raw).customer_ids}
                                    if written and id_key(customer_id) not in written:
                                        batch_counts['label_not_in_text_ids'] += 1
                                    if not split and core.extract_names(row.raw)['name_extraction']['status'] == 'unavailable':
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
        if progress:
            progress({**dict(counts), 'phase': 'learning_id_slots'})
        with factory.begin() as session:
            if engine.dialect.name == 'postgresql':
                session.execute(text('SELECT pg_advisory_xact_lock(731026)'))
            # Customer-ID positions are recounted from all confirmed links after each import.
            summary['id_slots'] = id_slots.rebuild(session, check_cancel=check_cancel)
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
