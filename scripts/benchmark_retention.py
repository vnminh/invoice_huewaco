"""Replay approved observations before/after knowledge growth in isolated SQLite.

This measures retention, not unseen-month F1 or semantic-model quality. No deployment
database is accessed. Output contains aggregate metrics only, plus local knowledge.sqlite.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select

from app.core import Core
from app.db import Base, Pattern, RecurringLink, make_engine, sessions
from app.embedding import Embedder
from app.excel import StreamingWorkbook
from app.identifiers import confirmed_customer_ids
from app.name_extraction import NameExtractor
from app.normalize import normalize_text
from app.recurring import owner_query


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--train', required=True)
    parser.add_argument('--sheet', default='BIDV')
    parser.add_argument('--initial', type=int, default=200)
    parser.add_argument('--growth', type=int, default=1000)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    if min(args.initial, args.growth) < 1:
        parser.error('--initial and --growth must be positive')
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    database = out / 'knowledge.sqlite'
    if database.exists():
        parser.error('Choose a fresh --out; existing knowledge is never overwritten')
    rows, skipped = [], Counter()
    with StreamingWorkbook(args.train) as workbook:
        for batch in workbook.batches(sheet=args.sheet, kind='confirmed', batch_size=500):
            for row in batch:
                if row.label_status != 'confirmed' or row.validation_errors:
                    skipped['unconfirmed_or_invalid'] += 1
                    continue
                try:
                    ids = confirmed_customer_ids(row.customer_id)
                except ValueError:
                    skipped['invalid_customer_id'] += 1
                    continue
                if (row.label_status == 'confirmed' and len(ids) == 1 and row.amount > 0
                        and not row.validation_errors):
                    rows.append((row, ids[0]))
                if len(rows) >= args.initial + args.growth:
                    break
            if len(rows) >= args.initial + args.growth:
                break
    if len(rows) < args.initial + args.growth:
        parser.error(f'Only {len(rows)} eligible rows; reduce the requested limits')
    engine = make_engine('sqlite:///' + database.resolve().as_posix())
    Base.metadata.create_all(engine)
    factory = sessions(engine)
    names = NameExtractor()
    names.enabled = False
    # Deliberately tiny retrieval budget: exact recurring memory must not rely on top K.
    core = Core(embedder=Embedder(model='hash'), name_extractor=names, candidate_limit=1)
    initial, growth = rows[:args.initial], rows[args.initial:]

    def learn(samples):
        started = time.perf_counter()
        for offset in range(0, len(samples), 32):
            with factory.begin() as session:
                for row, cid in samples[offset:offset + 32]:
                    core.learn(session, row, cid, row.customer_name)
        return round(time.perf_counter() - started, 3)

    def replay():
        counts, outcomes = Counter(), []
        started = time.perf_counter()
        with factory() as session:
            for row, cid in initial:
                owners = set(session.scalars(owner_query(normalize_text(row.raw), row.payer)))
                counts['retained_confirmed_owner'] += cid in owners
                counts['shared_signature_rows'] += len(owners) > 1
                result = core.classify(session, row.raw, row.payer)
                automatic = result['decision'] == 'auto_accept'
                counts[result['decision']] += 1
                counts['auto_correct'] += automatic and result['customer_id'] == cid
                counts['auto_wrong'] += automatic and result['customer_id'] != cid
                counts['memory_auto'] += automatic and bool(result['evidence'].get('memory_lookup'))
                counts['ambiguous_auto'] += automatic and len(owners) > 1
                outcomes.append((automatic, result['customer_id'], len(owners)))
        return dict(counts), outcomes, round(time.perf_counter() - started, 3)

    try:
        initial_seconds = learn(initial)
        before, before_outcomes, before_seconds = replay()
        with factory() as session:
            old_patterns = set(session.scalars(select(Pattern.id)))
            old_links = set(session.scalars(select(RecurringLink.id)))
        growth_seconds = learn(growth)
        after, after_outcomes, after_seconds = replay()
        with factory() as session:
            lost_patterns = len(old_patterns - set(session.scalars(select(Pattern.id))))
            lost_links = len(old_links - set(session.scalars(select(RecurringLink.id))))
        regressions = sum(was_auto and old_cid == cid and owners == 1 and
                          (not is_auto or new_cid != cid)
                          for (_, cid), (was_auto, old_cid, _), (is_auto, new_cid, owners)
                          in zip(initial, before_outcomes, after_outcomes))
        report = {'protocol': 'approved-observation replay before/after incremental learning; NOT unseen-data F1',
            'train': Path(args.train).name, 'sheet': args.sheet, 'initial_rows': len(initial),
            'growth_rows': len(growth), 'skipped_before_sample_complete': dict(skipped),
            'embedding': 'hash (not a semantic-model benchmark)',
            'ner_enabled': False, 'candidate_limit': 1, 'backend': 'isolated SQLite',
            'before': before, 'after': after, 'lost_pattern_links': lost_patterns,
            'lost_recurring_links': lost_links, 'unique_owner_auto_regressions': regressions,
            'seconds': {'initial_learning': initial_seconds, 'growth_learning': growth_seconds,
                        'before_replay': before_seconds, 'after_replay': after_seconds}}
        report['passed'] = (not lost_patterns and not lost_links and not regressions
            and after.get('retained_confirmed_owner', 0) == len(initial)
            and not after.get('auto_wrong', 0) and not after.get('ambiguous_auto', 0))
        (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        return 0 if report['passed'] else 1
    finally:
        engine.dispose()


if __name__ == '__main__':
    sys.exit(main())
