"""Development benchmark over all (or selected) sheets: learn a confirmed FN workbook, then match a raw bank workbook.

Example (isolated SQLite knowledge; never touches the application's PostgreSQL):
    .venv/bin/python scripts/benchmark_all.py --train 'Data/Ngan hang thang 7-2026 FN.xlsx' \
        --raw 'Data/Ngan hang thang 08.2026.xlsx' --truth 'Data/Ngan hang thang 8-2026 FN.xlsx' --out runtime/bench_bidv \
        --sheets BIDV --all-train-sheets  # learn every bank, test BIDV; omit --sheets to test every sheet

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
from app.identifiers import confirmed_customer_ids, channel_key
from app.name_extraction import NameExtractor
from app.row_errors import RowErrors


def narrative(raw):
    return ' '.join(fold(raw).split())


def metric(tp, fp, total):
    precision, recall = (tp / (tp + fp) if tp + fp else None), (tp / total if total else None)
    return {'precision': round(precision, 5) if precision is not None else None,
            'recall': round(recall, 5) if recall is not None else None,
            'f1': round(2 * tp / (tp + fp + total), 5) if tp + fp + total else None,
            'f0_5': round(1.25 * tp / (tp + fp + .25 * total), 5) if tp + fp + total else None,
            'correct': tp, 'wrong': fp, 'rows': total}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--train', help='Confirmed FN workbook to learn (omit with --reuse)')
    parser.add_argument('--raw', required=True, help='Raw bank workbook to match (all transaction sheets)')
    parser.add_argument('--truth', required=True, help='Confirmed FN workbook of the same period, for scoring only')
    parser.add_argument('--out', required=True, help='Output folder for knowledge.sqlite, predictions.jsonl, report.json')
    parser.add_argument('--reuse', action='store_true', help='Reuse knowledge.sqlite in --out instead of learning')
    parser.add_argument('--sheets', default='', help='Comma-separated sheet names to use in all three files (e.g. BIDV)')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--train-sheets', default=None, help='Override training selection; empty means every sheet')
    selection.add_argument('--all-train-sheets', action='store_true', help='Learn all historical sheets, independently of --sheets')
    parser.add_argument('--max-test-rows', type=int, default=0, help='Bound the evaluation sample (0 = all eligible rows)')
    parser.add_argument('--test-offset', type=int, default=0, help='Skip this many eligible rows before scoring a separate holdout')
    parser.add_argument('--hash', action='store_true', help='Rules/retrieval ablation with hash vectors; not semantic-model quality')
    parser.add_argument('--no-ner', action='store_true', help='Keep deterministic bank-field name extraction only')
    args = parser.parse_args()
    sheets = [name.strip() for name in args.sheets.split(',') if name.strip()] or None
    train_sheets = sheets if args.train_sheets is None else [name.strip() for name in args.train_sheets.split(',') if name.strip()] or None
    if args.all_train_sheets:
        train_sheets = None
    if args.max_test_rows < 0 or args.test_offset < 0:
        parser.error('Test row limits/offset must be nonnegative')
    if args.train and Path(args.train).resolve() in (Path(args.raw).resolve(), Path(args.truth).resolve()):
        parser.error('Training must be a separate historical file; truth cannot enter learning')
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    prior = json.loads((out/'report.json').read_text(encoding='utf-8')) if args.reuse and (out/'report.json').exists() else {}
    database = out / 'knowledge.sqlite'
    if not args.reuse:
        if not args.train:
            parser.error('--train is required unless --reuse is given')
        if database.exists():
            parser.error('Output already has knowledge.sqlite; choose a new --out or explicitly --reuse')
    engine = make_engine('sqlite:///' + database.as_posix())
    Base.metadata.create_all(engine)
    from app.embedding import Embedder
    names = NameExtractor()
    if args.no_ner:
        names.enabled = False
    core, factory = Core(embedder=Embedder(model='hash') if args.hash else None,
                         name_extractor=names), sessions(engine)
    started = time.perf_counter()
    if not args.reuse:
        with (out/'learning_errors.jsonl').open('w', encoding='utf-8') as error_file:
            errors = RowErrors(lambda issue: error_file.write(json.dumps(issue, ensure_ascii=False)+'\n'), file=Path(args.train).name)
            learned = import_confirmed(engine, core, args.train, 1000,
                                       lambda c: print('learn', c.get('processed', ''), c.get('phase', ''), flush=True),
                                       only_sheets=train_sheets, row_errors=errors)
        print('learned', learned['counts'], learned.get('id_slots'), flush=True)
        training_summary, training_periods = learned['counts'], learned.get('periods', [])
    else:
        training_summary, training_periods = prior.get('training_summary', {}), prior.get('training_periods', [])
    learn_seconds = time.perf_counter() - started

    truth, train_ids = defaultdict(set), set()
    def sheet_batches(workbook, kind, selection=sheets):
        for name in (workbook.confirmed_sheets() if kind == 'confirmed' else workbook.raw_sheets()):
            if not selection or name in selection:
                yield from workbook.batches(sheet=name, kind=kind, batch_size=500)

    with StreamingWorkbook(args.truth) as workbook:
        for batch in sheet_batches(workbook, 'confirmed'):
            for row in batch:
                if row.label_status == 'confirmed':
                    truth[(channel_key(row.payer), narrative(row.raw))].update(confirmed_customer_ids(row.customer_id))
    # Only labels actually learned into this frozen knowledge count as seen.
    with factory() as session:
        train_ids = {id_key(value) for value in session.scalars(select(Customer.id))}

    counts, per_sheet = Counter(), defaultdict(Counter)
    eligible_seen = 0
    latencies = []
    group_sizes, group_check_seconds = [], 0.
    started = time.perf_counter()
    with StreamingWorkbook(args.raw) as workbook, factory() as session, \
            (out / 'predictions.jsonl').open('w', encoding='utf-8') as predictions:
        for batch in sheet_batches(workbook, 'raw'):
            rows = [row for row in batch if row.amount > 0 and row.debit == 0 and not row.validation_errors]
            core.prepare_batch([row.raw for row in rows])
            for row in rows:
                eligible_seen += 1
                if eligible_seen <= args.test_offset:
                    continue
                if args.max_test_rows and counts['evaluated_rows'] >= args.max_test_rows:
                    break
                step = time.perf_counter()
                result = core.classify(session, row.raw, row.payer)
                latencies.append(time.perf_counter() - step)
                counts['evaluated_rows'] += 1
                gold = truth.get((channel_key(row.payer), narrative(row.raw)), set())
                kind = ('unlabeled' if not gold else 'batch' if len(gold) >= BATCH_MIN_CUSTOMERS else 'multi' if len(gold) > 1
                        else 'single_seen' if gold <= train_ids else 'single_unseen')
                evidence, decision = result.get('evidence', {}), result['decision']
                predicted = id_key(result['customer_id']) if result.get('customer_id') else None
                counts[kind] += 1
                per_sheet[row.sheet][kind] += 1
                if predicted and decision in ('auto_accept', 'review'):
                    outcome = 'unscored' if not gold else 'correct' if len(gold) == 1 and predicted in gold else 'wrong'
                    counts[f'{decision}:{outcome}'] += 1
                    counts[f'{decision}:{outcome}:{kind}'] += 1
                    per_sheet[row.sheet][f'{decision}:{outcome}'] += 1
                allocation = {id_key(v) for v in evidence.get('allocation_customer_ids', [])}
                if allocation:
                    counts['allocation:' + ('exact' if allocation == gold else 'partial' if allocation < gold else 'wrong')] += 1
                suggested = result.get('suggested_customer_id')
                if suggested and not predicted:
                    outcome = 'correct' if set(confirmed_customer_ids(suggested)) == gold else 'unlabeled' if not gold else 'wrong'
                    counts['suggestion:' + outcome] += 1
                    counts[f'suggestion:{outcome}:{kind}'] += 1
                recommendations = list(dict.fromkeys(([predicted] if predicted else []) +
                    [id_key(item['customer_id']) for item in result.get('recommendations', []) + result.get('alternatives', [])
                     if item.get('customer_id')]))[:5]
                if len(gold) == 1:
                    counts['recommendation:covered'] += bool(recommendations)
                    counts['recommendation:hit_at_1'] += bool(recommendations and recommendations[0] in gold)
                    counts['recommendation:hit_at_5'] += bool(gold.intersection(recommendations))
                group = result.get('candidate_group', {})
                if group.get('total'):
                    counts['group:available'] += 1
                    group_sizes.append(group['total'])
                    counts['group:broad'] += bool(group.get('broad'))
                if kind == 'single_seen' and decision == 'manual_check':
                    counts['group:seen_manual_rows'] += 1
                    counts['group:preview_hit'] += bool(gold.intersection(
                        item['customer_id'] for item in group.get('members', [])))
                    if group.get('total'):
                        check_start = time.perf_counter()
                        # Gold is used only to inspect membership in the already computed
                        # cohort, never as input to classify, ranking or learning.
                        membership = core.recommendation_group(session, row.raw, row.payer,
                            names=result.get('extracted_names', []), suggestions=result.get('recommendations', []),
                            q=next(iter(gold)), limit=50)
                        group_check_seconds += time.perf_counter() - check_start
                        counts['group:scope_hit'] += bool(gold.intersection(
                            item['customer_id'] for item in membership['members']))
                if evidence.get('batch_settlement'):
                    counts['batch_flag:' + kind] += 1
                predictions.write(json.dumps({'sheet': row.sheet, 'row': row.row_index, 'raw': row.raw, 'truth': sorted(gold),
                                              'kind': kind, 'predicted': predicted, 'decision': decision, 'score': result['score'],
                                              'reason': evidence.get('reason'), 'allocation': sorted(allocation),
                                              'suggested': suggested, 'recommendations': recommendations,
                                              'candidate_group': group}, ensure_ascii=False) + '\n')
            if args.max_test_rows and counts['evaluated_rows'] >= args.max_test_rows:
                break
    single = counts['single_seen'] + counts['single_unseen']
    report = {
        'train': args.train or prior.get('train'), 'raw': args.raw, 'truth': args.truth, 'embedding_model': core.embedder.name,
        'training_summary': training_summary, 'training_periods': training_periods,
        'original_learning_seconds': round(learn_seconds, 3) if not args.reuse else prior.get('original_learning_seconds', prior.get('learn_seconds')),
        'reused_knowledge': args.reuse,
        'train_sheets': train_sheets, 'test_sheets': sheets, 'max_test_rows': args.max_test_rows,
        'test_offset': args.test_offset,
        'ner_enabled': core.name_extractor.enabled, 'backend': 'isolated SQLite (not PostgreSQL query performance)',
        'truth_join': 'canonical bank + folded narrative; repeated narratives pool confirmed allocations',
        'unlabeled_policy': 'unscored, never treated as negative',
        'learn_seconds': round(learn_seconds, 3), 'classify_seconds': round(time.perf_counter() - started, 3),
        'match_latency_ms': {str(p): round(sorted(latencies)[min(len(latencies)-1, int((len(latencies)-1)*p/100))]*1000, 2)
                             for p in (50, 95, 99)} if latencies else {},
        'knowledge_mb': round(database.stat().st_size / 1e6, 1),
        'auto_accept': metric(counts['auto_accept:correct'], counts['auto_accept:wrong'], single),
        'auto_accept_seen_customers': metric(counts['auto_accept:correct:single_seen'],
                                             counts['auto_accept:wrong:single_seen'], counts['single_seen']),
        'auto_plus_review': metric(counts['auto_accept:correct'] + counts['review:correct'],
                                   counts['auto_accept:wrong'] + counts['review:wrong'], single),
        'suggested_id_for_unseen': metric(counts['suggestion:correct:single_unseen'], counts['suggestion:wrong:single_unseen'], counts['single_unseen']),
        'recommendations_single_customer': {
            'coverage': round(counts['recommendation:covered']/single, 5) if single else None,
            'hit_at_1': round(counts['recommendation:hit_at_1']/single, 5) if single else None,
            'hit_at_5': round(counts['recommendation:hit_at_5']/single, 5) if single else None,
            'rows': single},
        'candidate_groups': {
            'seen_manual_rows': counts['group:seen_manual_rows'],
            'preview_hits': counts['group:preview_hit'], 'scope_hits': counts['group:scope_hit'],
            'scope_recall': round(counts['group:scope_hit']/counts['group:seen_manual_rows'], 5)
                            if counts['group:seen_manual_rows'] else None,
            'available_rows': counts['group:available'], 'broad_rows': counts['group:broad'],
            'size_median': sorted(group_sizes)[len(group_sizes)//2] if group_sizes else None,
            'size_p95': sorted(group_sizes)[int((len(group_sizes)-1)*.95)] if group_sizes else None,
            'membership_scoring_seconds': round(group_check_seconds, 3)},
        'counts': dict(sorted(counts.items())), 'per_sheet': {sheet: dict(c) for sheet, c in per_sheet.items()}}
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'per_sheet'}, ensure_ascii=False, indent=1))
    engine.dispose()


if __name__ == '__main__':
    main()
