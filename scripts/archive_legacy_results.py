"""User-run, read-only PostgreSQL export before migration 002. Does not drop/learn anything."""
import argparse
import base64
import csv
import json
from pathlib import Path
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import default_url, make_engine


def csv_value(value):
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='runtime/legacy-export')
    args = parser.parse_args()
    if not default_url().startswith('postgresql'):
        parser.error('Export requires the existing PostgreSQL database')
    folder = Path(args.output)
    folder.mkdir(parents=True, exist_ok=True)
    engine = make_engine(default_url())
    try:
        with engine.connect() as connection:
            connection.execute(text('SET TRANSACTION READ ONLY'))
            available = set(inspect(connection).get_table_names(schema='public'))
            for table in ('transactions', 'feedback', 'jobs'):
                if table not in available:
                    continue
                path = folder / (table + '.csv')
                if path.exists():
                    raise ValueError(f'Archive already exists: {path}. Choose a new --output directory.')
                result = connection.execution_options(stream_results=True).execute(text(f'SELECT * FROM public.{table} ORDER BY id'))
                with path.open('w', encoding='utf-8-sig', newline='') as output:
                    writer = csv.writer(output)
                    writer.writerow(result.keys())
                    for row in result:
                        writer.writerow([csv_value(value) for value in row])
                print(path)
            if 'transactions' in available:
                path = folder / 'confirmed_learning.csv'
                if path.exists():
                    raise ValueError(f'Archive already exists: {path}. Choose a new --output directory.')
                records = connection.execution_options(stream_results=True).execute(text('''
                    SELECT t.raw, t.transaction_date, t.amount, t.payer, t.confirmed_customer_id,
                           c.canonical_name
                    FROM public.transactions t JOIN public.customers c ON c.id=t.confirmed_customer_id
                    WHERE t.status='confirmed' AND t.learned=FALSE ORDER BY t.id
                '''))
                count = 0
                with path.open('w', encoding='utf-8-sig', newline='') as output:
                    writer = csv.writer(output)
                    writer.writerow(['IDKH', 'TENKH', 'NOIDUNG', 'NGAY', 'SOTIEN', 'NGANHANG', 'NOIDUNG_GOC_B64'])
                    for raw, date, amount, payer, customer_id, name in records:
                        writer.writerow([customer_id, name, raw, date, amount, payer,
                                         base64.b64encode(raw.encode('utf-8')).decode('ascii')])
                        count += 1
                print(f'{path}: {count} confirmed rows awaiting learning. Import this CSV through the new UI.')
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
