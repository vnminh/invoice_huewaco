"""Recount learned customer-ID and name positions from the existing knowledge database.

Run on the server after sql/migrations/008_name_positions.sql. The web service may keep
running: the rebuild takes the knowledge write lock and commits in one transaction.
Reads only stored confirmed data; no Excel files, no semantic model, and patterns,
numbers and embeddings are not changed.
"""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import inspect

from app.db import default_url, make_engine
from app.maintenance import rebuild_positions


def main():
    if not default_url().startswith('postgresql'):
        sys.exit('DATABASE_URL phải trỏ tới database PostgreSQL của kho kiến thức.')
    engine = make_engine(default_url())
    try:
        missing = {'customer_id_slots', 'customer_name_slots'} - set(inspect(engine).get_table_names())
        if missing:
            sys.exit('Thiếu bảng ' + ', '.join(sorted(missing)) + '. Chạy sql/migrations/008_name_positions.sql trước.')
        print('Đang đếm lại vị trí IDKH và tên từ dữ liệu đã học...', flush=True)
        print(json.dumps(rebuild_positions(engine), ensure_ascii=False, indent=2))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
