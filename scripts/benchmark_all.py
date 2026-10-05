"""Development benchmark over all (or selected) sheets: learn a confirmed FN workbook, then match a raw bank workbook.

Example (isolated SQLite knowledge; never touches the application's PostgreSQL):
    .venv/bin/python scripts/benchmark_all.py --train 'Data/Ngan hang thang 7-2026 FN.xlsx' \
        --raw 'Data/Ngan hang thang 08.2026.xlsx' --truth 'Data/Ngan hang thang 8-2026 FN.xlsx' --out runtime/bench_bidv \
        --sheets BIDV      # quick single-bank check; omit --sheets for every sheet

Truth labels are used only to score results. Raw rows are reconciled to FN rows by their narrative
(case/accent/whitespace-insensitive), so one bank credit may map to several FN customers.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select

from app.core import Core, id_key
from app.db import Base, Customer, make_engine, sessions
from app.excel import StreamingWorkbook
from app.id_slots import BATCH_MIN_CUSTOMERS
from app.knowledge import import_confirmed
from app.normalize import fold


def narrative(raw):
    return ' '.join(fold(raw).split())


def metric(tp, fp, total):
    return {'precision': round(tp / (tp + fp), 5) if tp + fp else None,
            'recall': round(tp / total, 5) if total else None, 'correct': tp, 'wrong': fp, 'rows': total}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--train', help='Confirmed FN workbook to learn (omit with --reuse)')
    parser.add_argument('--raw', required=True, help='Raw bank workbook to match (all transaction sheets)')
    parser.add_argument('--truth', required=True, help='Confirmed FN workbook of the same period, for scoring only')
    parser.add_argument('--out', required=True, help='Output folder for knowledge.sqlite, predictions.jsonl, report.json')
    parser.add_argument('--reuse', action='store_true', help='Reuse knowledge.sqlite in --out instead of learning')
    parser.add_argument('--sheets', default='', help='Comma-separated sheet names to use in all three files (e.g. BIDV)')
    args = parser.parse_args()
    sheets = [name.strip() for name in args.sheets.split(',') if name.strip()] or None
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    database = out / 'knowledge.sqlite'
    if not args.reuse:
        if not args.train:
            parser.error('--train is required unless --reuse is given')
        database.unlink(missing_ok=True)
    engine = make_engine('sqlite:///' + database.as_posix())
    Base.metadata.create_all(engine)
    core, factory = Core(), sessions(engine)
    started = time.perf_counter()
    if not args.reuse:
        learned = import_confirmed(engine, core, args.train, 1000,
                                   lambda c: print('learn', c.get('processed', ''), c.get('phase', ''), flush=True),
                                   only_sheets=sheets)
        print('learned', learned['counts'], learned.get('id_slots'), flush=True)
    learn_seconds = time.perf_counter() - started

    truth, train_ids = defaultdict(set), set()
    def sheet_batches(workbook, kind):
        for name in (workbook.confirmed_sheets() if kind == 'confirmed' else workbook.raw_sheets()):
            if not sheets or name in sheets:
                yield from workbook.batches(sheet=name, kind=kind, batch_size=500)

    with StreamingWorkbook(args.truth) as workbook:
        for batch in sheet_batches(workbook, 'confirmed'):
            for row in batch:
                if row.label_status == 'confirmed':
                    truth[narrative(row.raw)].add(id_key(row.customer_id))
    if args.train:
        with StreamingWorkbook(args.train) as workbook:
            for batch in sheet_batches(workbook, 'confirmed'):
                train_ids.update(id_key(row.customer_id) for row in batch if row.label_status == 'confirmed')
    else:
        with factory() as session:
            train_ids = {id_key(value) for value in session.scalars(select(Customer.id))}

    counts, per_sheet = Counter(), defaultdict(Counter)
    started = time.perf_counter()
    with StreamingWorkbook(args.raw) as workbook, factory() as session, \
            (out / 'predictions.jsonl').open('w', encoding='utf-8') as predictions:
        for batch in sheet_batches(workbook, 'raw'):
            rows = [row for row in batch if row.amount > 0 and row.debit == 0 and not row.validation_errors]
            core.prepare_batch([row.raw for row in rows])
            for row in rows:
                result = core.classify(session, row.raw, row.payer)
                gold = truth.get(narrative(row.raw), set())
                kind = ('unlabeled' if not gold else 'batch' if len(gold) >= BATCH_MIN_CUSTOMERS else 'multi' if len(gold) > 1
                        else 'single_seen' if gold <= train_ids else 'single_unseen')
                evidence, decision = result.get('evidence', {}), result['decision']
                predicted = id_key(result['customer_id']) if result.get('customer_id') else None
                counts[kind] += 1
                per_sheet[row.sheet][kind] += 1
                if predicted and decision in ('auto_accept', 'review'):
                    outcome = 'correct' if len(gold) == 1 and predicted in gold else 'wrong'
                    counts[f'{decision}:{outcome}'] += 1
                    counts[f'{decision}:{outcome}:{kind}'] += 1
                    per_sheet[row.sheet][f'{decision}:{outcome}'] += 1
                allocation = {id_key(v) for v in evidence.get('allocation_customer_ids', [])}
                if allocation:
                    counts['allocation:' + ('exact' if allocation == gold else 'partial' if allocation < gold else 'wrong')] += 1
                suggested = result.get('suggested_customer_id')
                if suggested and not predicted:
                    counts['suggestion:' + ('correct' if {id_key(suggested)} == gold else 'unlabeled' if not gold else 'wrong')] += 1
                if evidence.get('batch_settlement'):
                    counts['batch_flag:' + kind] += 1
                predictions.write(json.dumps({'sheet': row.sheet, 'row': row.row_index, 'raw': row.raw, 'truth': sorted(gold),
                                              'kind': kind, 'predicted': predicted, 'decision': decision, 'score': result['score'],
                                              'reason': evidence.get('reason'), 'allocation': sorted(allocation),
                                              'suggested': suggested}, ensure_ascii=False) + '\n')
    single = counts['single_seen'] + counts['single_unseen']
    report = {
        'train': args.train, 'raw': args.raw, 'truth': args.truth, 'embedding_model': core.embedder.name,
        'learn_seconds': round(learn_seconds), 'classify_seconds': round(time.perf_counter() - started),
        'knowledge_mb': round(database.stat().st_size / 1e6, 1),
        'auto_accept': metric(counts['auto_accept:correct'], counts['auto_accept:wrong'], single),
        'auto_accept_seen_customers': metric(counts['auto_accept:correct:single_seen'],
                                             counts['auto_accept:wrong:single_seen'], counts['single_seen']),
        'auto_plus_review': metric(counts['auto_accept:correct'] + counts['review:correct'],
                                   counts['auto_accept:wrong'] + counts['review:wrong'], single),
        'suggested_id_for_unseen': metric(counts['suggestion:correct'], counts['suggestion:wrong'], counts['single_unseen']),
        'counts': dict(sorted(counts.items())), 'per_sheet': {sheet: dict(c) for sheet, c in per_sheet.items()}}
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'per_sheet'}, ensure_ascii=False, indent=1))
    engine.dispose()


if __name__ == '__main__':
    main()
