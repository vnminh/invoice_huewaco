"""Confirmed imports persist independently of benchmark evaluation."""
from collections import Counter
import hashlib
import json
from pathlib import Path

from sqlalchemy import func, select, text

from .db import Customer, KnowledgeReceipt, Metadata, Pattern, sessions
from .excel import ConfirmedCsv, StreamingWorkbook
from .core import learning_receipt


class ImportCancelled(Exception):
    """Cooperative stop; only earlier committed batches remain in knowledge."""
    def __init__(self, summary=None):
        super().__init__('Knowledge import cancelled')
        self.summary = summary or {}


def import_confirmed(engine, core, path, batch_size=1000, progress=None, before=None, check_cancel=None):
    factory = sessions(engine)
    counts = Counter()
    payers = Counter()
    periods = set()
    embedding_chunk = 32 if check_cancel else batch_size
    check_cancel = check_cancel or (lambda: None)
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        while chunk := source.read(1024 * 1024):
            check_cancel()
            digest.update(chunk)
    check_cancel()
    key = 'import:' + digest.hexdigest()
    summary = {'file': Path(path).name, 'sha256': digest.hexdigest(), 'status': 'running'}
    with factory.begin() as session:
        record = session.get(Metadata, key)
        if record:
            # Preserve a completed import when repeating it; receipts prevent duplication.
            previous = json.loads(record.value)
            if previous.get('status') == 'completed':
                if previous.get('feature_extractor') != core.extractor.name or previous.get('embedding_model') != core.embedder.name:
                    raise ValueError('Imported file uses a different model; reimport confirmed history into a fresh database')
                return {**previous, 'already_imported': True}
            record.value = json.dumps(summary)
        else:
            session.add(Metadata(key=key, value=json.dumps(summary)))
    sheets = []
    try:
        check_cancel()
        is_csv = Path(path).suffix.lower() == '.csv'
        reader = ConfirmedCsv if is_csv else StreamingWorkbook
        with reader(path, check_cancel=check_cancel) as workbook:
            sheets = workbook.confirmed_sheets()
            if not sheets:
                raise ValueError('Knowledge workbook needs labeled sheets with an IDKH header')
            for sheet in sheets:
                for batch in workbook.batches(sheet=sheet, batch_size=batch_size):
                    check_cancel()
                    texts = [row.raw for row in batch if row.label_status == 'confirmed']
                    # Bound time between stop checks while running the encoder.
                    for start in range(0, len(texts), embedding_chunk):
                        check_cancel()
                        core.prepare_batch(texts[start:start + embedding_chunk])
                    batch_counts, batch_payers, batch_periods = Counter(), Counter(), set()
                    with factory.begin() as session:
                        if engine.dialect.name == 'postgresql':
                            session.execute(text('SELECT pg_advisory_xact_lock(731026)'))
                        for row in batch:
                            check_cancel()
                            batch_counts['processed'] += 1
                            if row.label_status != 'confirmed':
                                batch_counts[row.label_status] += 1
                                continue
                            if not row.customer_id:
                                raise ValueError('Knowledge import requires confirmed customer labels')
                            if before and (not row.date or row.date[:10] >= before):
                                raise ValueError('Training must precede all test transactions')
                            batch_periods.add(row.date[:7])
                            batch_payers[row.payer] += 1
                            batch_counts['confirmed'] += 1
                            customer_id = core.ensure_customer(session, row.customer_id, row.customer_name).id if is_csv else row.customer_id
                            receipt = learning_receipt(row.raw, customer_id, row.date, row.amount, row.payer) if is_csv else None
                            batch_counts['learned'] += int(core.learn(session, row, customer_id, row.customer_name, receipt_key=receipt))
                        check_cancel()
                    counts.update(batch_counts)
                    payers.update(batch_payers)
                    periods.update(batch_periods)
                    if progress:
                        progress(dict(counts))
        check_cancel()
    except ImportCancelled:
        summary.update(status='cancelled', sheets=sheets, periods=sorted(periods), payers=dict(payers),
                       feature_extractor=core.extractor.name, embedding_model=core.embedder.name,
                       counts=dict(counts))
        with factory.begin() as session:
            session.get(Metadata, key).value = json.dumps(summary, ensure_ascii=False)
        raise ImportCancelled(summary) from None
    summary.update(status='completed', sheets=sheets, periods=sorted(periods), payers=dict(payers),
                   feature_extractor=core.extractor.name, embedding_model=core.embedder.name,
                   counts=dict(counts))
    with factory.begin() as session:
        session.get(Metadata, key).value = json.dumps(summary, ensure_ascii=False)
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
