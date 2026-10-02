"""Persist confirmed historical knowledge in the user's initialized PostgreSQL database."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core import Core
from app.db import default_url, make_engine
from app.knowledge import import_confirmed
from app.name_extraction import NameExtractor

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--train', required=True, help='Confirmed historical XLSX or learning CSV')
    parser.add_argument('--batch-size', type=int, default=1000)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 5000:
        parser.error('--batch-size must be between 1 and 5000')
    url = default_url()
    if not url.startswith('postgresql'):
        parser.error('Learning uses your initialized PostgreSQL database')
    engine = make_engine(url)
    try:
        print(import_confirmed(engine, Core(name_extractor=NameExtractor()), args.train, args.batch_size, lambda c: print(c, flush=True)))
    finally:
        engine.dispose()
