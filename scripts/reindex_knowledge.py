"""Run explicitly with the web service stopped, after migration 005. No filter files are read."""
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core import Core
from app.db import default_url, make_engine
from app.maintenance import reindex_knowledge


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-size', type=int, default=200)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 1000:
        parser.error('--batch-size must be between 1 and 1000')
    if not default_url().startswith('postgresql'):
        parser.error('This maintenance command requires PostgreSQL')
    engine = make_engine(default_url())
    try:
        result = reindex_knowledge(engine, Core(), args.batch_size,
            progress=lambda count: print(f'Đã cập nhật {count:,} mẫu đã học.', flush=True))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
