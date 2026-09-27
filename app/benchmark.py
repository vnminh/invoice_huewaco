"""Leakage-safe, chronological BIDV benchmark with disk-backed reconciliation."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import csv
import json
from pathlib import Path
import resource
import sqlite3
import tempfile
import time

from .core import Core, fingerprint, posting_equal
from .db import Base, Customer, make_engine, sessions
from .excel import ExcelTransaction, StreamingWorkbook
from .normalize import fold
from .knowledge import frozen_knowledge_info


def reconciliation_key(raw):
    # Only case, accent and whitespace: keep dates, identifiers and token order intact.
    return fingerprint(' '.join(fold(raw).split()))


def metrics(tp, fp, fn):
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
    return {'precision': precision, 'recall': recall, 'f1': f1,
            'f0_5': 1.25 * tp / (1.25 * tp + fp + .25 * fn) if (1.25 * tp + fp + .25 * fn) else None,
            'true_positive': tp, 'false_positive': fp, 'false_negative': fn}


def run_benchmark(raw_path, truth_path, output_dir, cutoff='2026-07-22', batch_size=1000, progress=None,
                  embedder=None, database_url=None, training_path=None, test_sheet='BIDV', knowledge_engine=None, core=None):
    start = time.perf_counter()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    counts = Counter()
    examples = {'correct': [], 'false_positive': [], 'false_negative': [], 'ambiguous': []}
    saved_knowledge = knowledge_engine is not None
    snapshot = None
    with tempfile.TemporaryDirectory(prefix='invoice-benchmark-') as directory:
        directory = Path(directory)
        reconciliation = sqlite3.connect(str(directory / 'reconciliation.sqlite'))
        reconciliation.execute('PRAGMA cache_size=-4096')
        reconciliation.executescript('''
            CREATE TABLE raw (row_index INTEGER PRIMARY KEY,key TEXT,raw TEXT,reference TEXT,date TEXT,amount REAL,debit REAL);
            CREATE INDEX raw_key ON raw(key);
            CREATE TABLE truth (row_index INTEGER PRIMARY KEY,key TEXT,raw TEXT,customer_id TEXT,name TEXT,date TEXT,amount REAL,payer TEXT,label_status TEXT);
            CREATE INDEX truth_key ON truth(key);
        ''')
        try:
            for path, kind in [(raw_path, 'raw'), (truth_path, 'truth')]:
                with StreamingWorkbook(path) as workbook:
                    for batch in workbook.batches(sheet=test_sheet, batch_size=batch_size):
                        if kind == 'raw':
                            reconciliation.executemany('INSERT INTO raw VALUES (?,?,?,?,?,?,?)',
                                [(t.row_index, reconciliation_key(t.raw), t.raw, t.reference, t.date, t.amount, t.debit) for t in batch])
                        else:
                            reconciliation.executemany('INSERT INTO truth VALUES (?,?,?,?,?,?,?,?,?)',
                                [(t.row_index, reconciliation_key(t.raw), t.raw, t.customer_id, t.customer_name,
                                  t.date, t.amount, t.payer, t.label_status) for t in batch])
                        counts[kind + '_rows'] += len(batch)
                        reconciliation.commit()
                        if progress:
                            progress(dict(counts))
            counts['truth_unmatched_descriptions'] = reconciliation.execute(
                'SELECT count(DISTINCT key) FROM truth WHERE key NOT IN (SELECT key FROM raw)').fetchone()[0]
            counts['truth_unique_customers'] = reconciliation.execute("SELECT count(DISTINCT customer_id) FROM truth WHERE label_status='confirmed'").fetchone()[0]
            counts['truth_ko_skipped_rows'] = reconciliation.execute("SELECT count(*) FROM truth WHERE label_status='skipped'").fetchone()[0]
            counts['truth_unresolved_rows'] = reconciliation.execute("SELECT count(*) FROM truth WHERE label_status='unresolved'").fetchone()[0]
            payer_counts = dict(reconciliation.execute('SELECT payer,count(*) FROM truth GROUP BY payer'))
            reconciliation.executescript('''
                CREATE TABLE labels AS SELECT key,
                       count(DISTINCT CASE WHEN label_status='confirmed' THEN customer_id END) label_count,
                       min(CASE WHEN label_status='confirmed' THEN customer_id END) customer_id,
                       min(CASE WHEN label_status='confirmed' THEN name END) name,sum(amount) amount,
                       max(label_status='unresolved') unresolved,
                       max(label_status='skipped') skipped FROM truth GROUP BY key;
                CREATE UNIQUE INDEX labels_key ON labels(key);
            ''')
            # Production uses PostgreSQL. The standalone harness isolates labels and knowledge
            # in a temporary SQLite DB so running it never mutates the application knowledge.
            engine = knowledge_engine if saved_knowledge else make_engine(database_url or 'sqlite:///' + str(directory / 'knowledge.sqlite'))
            if not saved_knowledge:
                if engine.dialect.name != 'sqlite':
                    raise ValueError('Use knowledge_engine to benchmark saved PostgreSQL knowledge')
                Base.metadata.create_all(engine)
            factory = sessions(engine)
            core = core or Core(embedder=embedder)
            training_sheets = []
            training_payers = Counter()
            if saved_knowledge:
                earliest_test = reconciliation.execute('SELECT min(date) FROM raw').fetchone()[0]
                snapshot = frozen_knowledge_info(engine, earliest_test)
                counts['training_rows'] = snapshot['receipt_count']
                for imported in snapshot['imports']:
                    training_sheets.extend(imported['sheets'])
                    training_payers.update(imported['payers'])
                training_sheets = sorted(set(training_sheets))
            elif training_path:
                if Path(training_path).resolve() == Path(truth_path).resolve():
                    raise ValueError('Training file must be separate from test ground truth')
                earliest_test = reconciliation.execute('SELECT min(date) FROM raw').fetchone()[0]
                with StreamingWorkbook(training_path) as workbook:
                    training_sheets = workbook.confirmed_sheets()
                    if not training_sheets:
                        raise ValueError('Training workbook has no labeled sheets with an IDKH header')
                    for sheet in training_sheets:
                        for batch in workbook.batches(sheet=sheet, batch_size=batch_size):
                            with factory.begin() as session:
                                for transaction in batch:
                                    counts['training_source_rows'] += 1
                                    if transaction.label_status != 'confirmed':
                                        counts['training_' + transaction.label_status + '_rows'] += 1
                                        continue
                                    if not transaction.customer_id:
                                        raise ValueError(f'Training sheet {sheet} needs confirmed customer labels')
                                    if transaction.date and transaction.date[:10] >= earliest_test[:10]:
                                        raise ValueError('Training must precede all test transactions')
                                    if core.learn(session, transaction, transaction.customer_id, transaction.customer_name):
                                        counts['training_rows'] += 1
                                        training_payers[transaction.payer] += 1
                            if progress:
                                progress(dict(counts))
            else:
                training = reconciliation.execute('''
                    SELECT r.row_index,r.raw,r.date,r.amount,l.customer_id,l.name
                    FROM raw r JOIN labels l ON l.key=r.key
                    WHERE substr(r.date,1,10) < ? AND r.amount>0 AND l.label_count=1 AND l.skipped=0
                    ORDER BY r.date,r.row_index''', (cutoff,))
                while batch := training.fetchmany(batch_size):
                    with factory.begin() as session:
                        for row_index, raw, date, amount, customer_id, name in batch:
                            core.learn(session, ExcelTransaction(row_index, raw, date=date, amount=amount), customer_id, name)
                            counts['training_rows'] += 1
                    if progress:
                        progress(dict(counts))
            with factory() as session:
                counts['training_customers'] = session.query(Customer).count()
            evaluations = {'classification': Counter(), 'auto_accept': Counter(), 'seen_customers': Counter(),
                           'seen_customers_auto_accept': Counter(), 'filtering': Counter()}
            heldout = reconciliation.execute('''
                SELECT r.row_index,r.raw,r.date,r.amount,r.debit,l.label_count,l.customer_id,l.name,l.unresolved,l.skipped
                FROM raw r LEFT JOIN labels l ON l.key=r.key
                WHERE substr(r.date,1,10) >= ? ORDER BY r.date,r.row_index''', ('' if training_path or saved_knowledge else cutoff,))
            from .core import id_key
            from .db import Posting
            from sqlalchemy import select
            with (output_dir / 'predictions.csv').open('w', encoding='utf-8-sig', newline='') as out, \
                 (output_dir / 'evidence.jsonl').open('w', encoding='utf-8') as evidence_out, factory() as session:
                if engine.dialect.name == 'postgresql':
                    from sqlalchemy import text
                    session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
                writer = csv.writer(out)
                writer.writerow(['row_index','date','amount','raw','true_customer_id','predicted_customer_id','score','decision','evaluation','customer_in_training','reason','matched_pattern','matched_template'])
                while batch := heldout.fetchmany(batch_size):
                    # Prefetch only rows inference can use; never inspect truth labels for encoding.
                    known_ids = set(session.scalars(select(Customer.id)))
                    known_keys = {id_key(cid) for cid in known_ids}
                    core.prepare_batch([row[1] for row in batch if row[3] > 0 and row[4] == 0
                        and (not (ids := core.normalize(row[1]).customer_ids) or any(id_key(cid) in known_keys for cid in ids))])
                    for row_index, raw, date, amount, debit, label_count, truth_id, truth_name, unresolved, skipped in batch:
                        counts['heldout_rows'] += 1
                        if skipped:
                            counts['ko_rows_skipped'] += 1
                            writer.writerow([row_index,date,amount,safe_csv(raw),'','',0,'skipped','skipped_ko'])
                            continue
                        if label_count and label_count > 1:
                            counts['ambiguous_rows_excluded'] += 1
                            if len(examples['ambiguous']) < 5:
                                examples['ambiguous'].append({'row_index': row_index, 'customer_count': label_count, 'raw': raw})
                            writer.writerow([row_index,date,amount,safe_csv(raw),'','',0,'review','excluded_multi_customer'])
                            continue
                        if unresolved:
                            counts['unresolved_rows_excluded'] += 1
                            writer.writerow([row_index,date,amount,safe_csv(raw),'','',0,'review','excluded_unresolved_label'])
                            continue
                        positive = bool(label_count) and amount > 0 and debit == 0
                        truth_id = truth_id if positive else None
                        # Debits are operational exclusions, independent of knowledge scoring.
                        result = core.classify(session, raw) if amount > 0 and debit == 0 else {
                            'customer_id': None, 'score': 0, 'decision': 'reject'}
                        counts['extractor_' + result.get('normalization', {}).get('extraction', {}).get('status', 'not_used')] += 1
                        included = result['decision'] in ('auto_accept', 'review')
                        correct = included and positive and id_key(result['customer_id']) == id_key(truth_id)
                        known = bool(positive and session.scalar(select(Posting.id).where(
                            posting_equal('id:' + id_key(truth_id))).limit(1)))
                        counts['heldout_positive' if positive else 'heldout_negative'] += 1
                        if positive:
                            counts['seen_customer_positives' if known else 'unseen_customer_positives'] += 1
                        classification = evaluations['classification']
                        if correct:
                            classification['tp'] += 1
                        else:
                            classification['fp'] += int(included)
                            classification['fn'] += int(positive)
                        auto = result['decision'] == 'auto_accept'
                        evaluations['auto_accept']['tp'] += int(auto and correct)
                        evaluations['auto_accept']['fp'] += int(auto and not correct)
                        evaluations['auto_accept']['fn'] += int(positive and not (auto and correct))
                        if known:
                            evaluations['seen_customers']['tp'] += int(correct)
                            evaluations['seen_customers']['fp'] += int(included and not correct)
                            evaluations['seen_customers']['fn'] += int(not correct)
                            evaluations['seen_customers_auto_accept']['tp'] += int(auto and correct)
                            evaluations['seen_customers_auto_accept']['fp'] += int(auto and not correct)
                            evaluations['seen_customers_auto_accept']['fn'] += int(not (auto and correct))
                        evaluations['filtering']['tp'] += int(included and positive)
                        evaluations['filtering']['fp'] += int(included and not positive)
                        evaluations['filtering']['fn'] += int(positive and not included)
                        counts[result['decision']] += 1
                        outcome = 'correct' if correct else 'false_positive' if included else 'false_negative' if positive else 'true_negative'
                        detail = {'row_index': row_index, 'raw': raw, 'truth_customer_id': truth_id,
                                  'predicted_customer_id': result['customer_id'], 'score': result['score'],
                                  'decision': result['decision'], 'seen_customer': known,
                                  'reason': result.get('evidence', {}).get('reason', 'Non-credit bank transaction')}
                        if correct and len(examples['correct']) < 5:
                            examples['correct'].append({**detail, 'evidence': result.get('evidence', {})})
                        if included and not correct and len(examples['false_positive']) < 10:
                            examples['false_positive'].append(detail)
                        if positive and not correct and len(examples['false_negative']) < 10:
                            examples['false_negative'].append(detail)
                        evidence = result.get('evidence', {})
                        writer.writerow([row_index,date,amount,safe_csv(raw),truth_id or '',result['customer_id'] or '',
                                         result['score'],result['decision'],outcome,known,safe_csv(detail['reason']),
                                         safe_csv(evidence.get('matched_pattern')),safe_csv(evidence.get('matched_template'))])
                        evidence_out.write(json.dumps({'row_index': row_index, 'raw': raw, 'result': result,
                                                      'truth_customer_id': truth_id, 'customer_in_training': known},
                                                     ensure_ascii=False) + '\n')
                    if progress:
                        progress(dict(counts))
            if saved_knowledge:
                if frozen_knowledge_info(engine, earliest_test) != snapshot:
                    raise ValueError('Knowledge changed during benchmark; rerun against frozen July knowledge')
            else:
                engine.dispose()
        finally:
            reconciliation.close()
    report = {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'protocol': 'Read previously saved July knowledge; test August BIDV only; no training or knowledge writes during evaluation' if saved_knowledge else
                    'Learn all usable July FN labels across all sheets and payer channels; test August BIDV only; knowledge frozen during evaluation' if training_path else
                    'Chronological single-month holdout; confirmed pre-cutoff matched rows only; no learning during evaluation',
        'raw_file': Path(raw_path).name, 'ground_truth_file': Path(truth_path).name,
        'training_file': ', '.join(i['file'] for i in snapshot['imports']) if snapshot else Path(training_path).name if training_path else Path(truth_path).name,
        'training_sheets': training_sheets, 'training_payers': dict(training_payers),
        'sheet': test_sheet, 'cutoff': None if training_path or saved_knowledge else cutoff, 'batch_size': batch_size,
        'database': ('saved PostgreSQL knowledge' if engine.dialect.name == 'postgresql' else 'saved SQLite verification fixture; production requires PostgreSQL') if saved_knowledge else 'isolated temporary SQLite harness; PostgreSQL query paths require separate integration verification',
        'knowledge_snapshot': snapshot,
        'feature_extractor': core.extractor.name,
        'precision_priority': {'primary_metric': 'auto_accept', 'automatic_threshold': core.auto_threshold,
                               'review_threshold': core.review_threshold, 'scores_are_calibrated_probabilities': False},
        'known_customer_recall_ceiling': counts['seen_customer_positives'] / counts['heldout_positive'] if counts['heldout_positive'] else None,
        'embedding_model': core.embedder.name, 'counts': dict(counts), 'truth_payers': payer_counts,
        'metrics': {name: metrics(c['tp'], c['fp'], c['fn']) for name, c in evaluations.items()},
        'duration_seconds': round(time.perf_counter() - start, 3),
        'process_peak_rss_mb': round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2),
        'examples': examples,
        'limitations': [
            'Training includes July confirmed labels only. August labels are never used for retrieval, reranking or knowledge updates.' if training_path or saved_knowledge else
                'This is a within-July chronological benchmark, not a future-month evaluation.',
            'Unseen customer IDs and matches without reliable customer-specific evidence return 0% confidence and manual_check. Ground truth is used only for evaluation.',
            'FN includes edited descriptions and split payments. Multiple-customer matches are excluded from single-customer classification.',
            'ko rows are skipped entirely. ht/pd/th are unresolved CMA labels and excluded from single-customer evaluation.',
            'Unmatched raw descriptions are treated as filtered negatives; some may be edited FN positives. Negative labels need manual audit.',
            'Thresholds are fixed. Engineering changes were informed by errors in the August benchmark; reported improvements are development-set results, not a new untouched holdout.',
            ('Hash embeddings are lexical and used only as an ablation.' if core.embedder.name == 'hash' else
             'Real multilingual semantic embeddings are used. Semantic similarity cannot override conflicting contract IDs, identifier roles or numeric order.'),
            'Peak RSS covers this entire process; XLSX shared strings, reconciliation and knowledge are disk-backed.'
        ]}
    (output_dir / 'benchmark.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    write_markdown(report, output_dir / 'benchmark.md')
    apply_registry_reporting_policy(output_dir)
    report = json.loads((output_dir / 'benchmark.json').read_text(encoding='utf-8'))
    return report


def apply_registry_reporting_policy(output_dir):
    """Report unknown ground-truth customers at 0%, without changing classifier metrics.

    This is a label-assisted reporting rule, explicitly separate from model evaluation.
    It does not enter retrieval, scoring, or the knowledge database.
    """
    from .core import id_key, vietnamese_reason
    output_dir = Path(output_dir)
    report_path = output_dir / 'benchmark.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    zero_rows = overrides = 0
    correct_examples = []
    evidence_path = output_dir / 'evidence.jsonl'
    temp_path = output_dir / 'evidence.jsonl.tmp'
    reason = 'Khách hàng đối chiếu trong FN chưa có trong dữ liệu tháng 7. Báo cáo độ tin cậy 0%; cần kiểm tra thủ công.'
    with evidence_path.open(encoding='utf-8') as inp, temp_path.open('w', encoding='utf-8') as out:
        for line in inp:
            record = json.loads(line)
            result = record['result']
            original = record.get('model_result', result)
            if record.get('truth_customer_id') and record.get('customer_in_training') is False:
                zero_rows += 1
                overrides += int(original['score'] != 0 or original['decision'] != 'manual_check')
                record['model_result'] = original
                record['result'] = {'customer_id': None, 'customer_name': None, 'score': 0.0,
                    'decision': 'manual_check', 'evidence': {'reason': reason, 'reason_vi': reason,
                        'unknown_customer': True, 'reporting_policy': 'FN customer absent from training registry',
                        'nearest_history': original.get('evidence', {})},
                    'normalization': original.get('normalization', {}), 'alternatives': []}
            elif record.get('truth_customer_id') and original['decision'] in ('review', 'auto_accept') and \
                 id_key(original['customer_id']) == id_key(record['truth_customer_id']) and len(correct_examples) < 5:
                evidence = original.get('evidence', {})
                evidence['reason_vi'] = vietnamese_reason(evidence.get('reason', ''))
                correct_examples.append({'row_index': record['row_index'], 'raw': record['raw'],
                    'truth_customer_id': record['truth_customer_id'], 'predicted_customer_id': original['customer_id'],
                    'score': original['score'], 'decision': original['decision'], 'reason': evidence.get('reason', ''),
                    'evidence': evidence})
            out.write(json.dumps(record, ensure_ascii=False) + '\n')
    temp_path.replace(evidence_path)
    predictions_path = output_dir / 'predictions.csv'
    temp_path = output_dir / 'predictions.csv.tmp'
    with predictions_path.open(encoding='utf-8-sig', newline='') as inp, \
         temp_path.open('w', encoding='utf-8-sig', newline='') as out:
        reader = csv.DictReader(inp)
        columns = list(reader.fieldnames)
        for name in ('model_score', 'model_decision', 'model_predicted_customer_id', 'reporting_rule'):
            if name not in columns:
                columns.append(name)
        writer = csv.DictWriter(out, fieldnames=columns)
        writer.writeheader()
        for row in reader:
            row.setdefault('model_score', row['score'])
            row.setdefault('model_decision', row['decision'])
            row.setdefault('model_predicted_customer_id', row['predicted_customer_id'])
            if row['true_customer_id'] and row['customer_in_training'] == 'False':
                row['score'], row['decision'], row['predicted_customer_id'] = '0.0', 'manual_check', ''
                row['reason'] = reason
                row['reporting_rule'] = 'unknown_customer_zero_confidence'
            writer.writerow(row)
    temp_path.replace(predictions_path)
    report['examples']['correct'] = correct_examples
    report['reporting_policy'] = {
        'description': 'Every positive FN customer absent from the July registry is reported at 0% with manual_check. '
                       'This reporting rule uses evaluation labels after prediction; classifier metrics use untouched model outputs.',
        'unknown_customer_zero_confidence_rows': zero_rows, 'nonzero_model_suggestions_overridden': overrides,
        'original_predictions': 'model_result in evidence.jsonl; model_score/model_decision/model_predicted_customer_id in predictions.csv'}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    write_markdown(report, output_dir / 'benchmark.md')


def safe_csv(value):
    text = str(value or '')
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text


def write_markdown(report, path):
    def percent(value):
        return f'{value:.2%}' if value is not None else 'N/A (no predictions/labels)'
    lines = ['# BIDV benchmark', '', report['protocol'], '',
             (f"Training: **{report['training_file']}** (all labeled sheets/channels). Test: **{report['raw_file']}**, sheet **{report['sheet']}**." if report['cutoff'] is None else
              f"Training: dates before **{report['cutoff']}**. Evaluation: dates on/after this cutoff."), '',
             '| Evaluation | Precision | Recall | F1 | TP | FP | FN |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for name, metric in report['metrics'].items():
        lines.append(f"| {name} | {percent(metric['precision'])} | {percent(metric['recall'])} | {percent(metric['f1'])} | {metric['true_positive']} | {metric['false_positive']} | {metric['false_negative']} |")
    lines += ['', f"Duration: {report['duration_seconds']} seconds. Process peak RSS: {report['process_peak_rss_mb']} MB.", '',
              '## Data counts', '', '```json', json.dumps(report['counts'], indent=2), '```', '', '## Limits and error analysis', '']
    lines += ['- ' + limit for limit in report['limitations']]
    if report.get('reporting_policy'):
        lines += ['', '## Missing-customer reporting rule', '', report['reporting_policy']['description'], '',
                  f"Unknown-customer rows reported at 0%: {report['reporting_policy']['unknown_customer_zero_confidence_rows']}. "
                  f"Nonzero model suggestions overridden for reporting: {report['reporting_policy']['nonzero_model_suggestions_overridden']}.", '',
                  report['reporting_policy']['original_predictions']]
    lines += ['', 'Low overall recall is expected when the holdout contains customers absent from the training knowledge. '
              'The seen-customers row isolates repeated customers, while overall classification counts abstention as a false negative. '
              'Wrong accepted customers count as both FP and FN. Auto-accept precision is undefined when no rows auto-accept.', '',
              'See benchmark.json for FP/FN examples, predictions.csv for every tested row, and evidence.jsonl for the historical template and feature scores of each prediction.']
    Path(path).write_text('\n'.join(lines) + '\n', encoding='utf-8')
