"""Explicitly backfill confirmed recurring links; no embedding model or result file is read."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import default_url, make_engine
from app.recurring import rebuild


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-size', type=int, default=500)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 1000:
        parser.error('--batch-size must be between 1 and 1000')
    if not default_url().startswith('postgresql'):
        parser.error('This maintenance command requires PostgreSQL')
    engine = make_engine(default_url())
    try:
        print(json.dumps(rebuild(engine, args.batch_size,
            progress=lambda count: print(f'Đã bổ sung bộ nhớ cho {count:,} mẫu.', flush=True)), ensure_ascii=False))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
