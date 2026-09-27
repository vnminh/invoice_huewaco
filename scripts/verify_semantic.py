"""Development-only local fixture. Production commands always use PostgreSQL.

Learn July once, close/reopen its persisted fixture, then benchmark August BIDV.
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.benchmark import run_benchmark
from app.core import Core
from app.db import Base, make_engine
from app.embedding import Embedder
from app.knowledge import import_confirmed

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default='intfloat/multilingual-e5-small')
    parser.add_argument('--fixture', default='runtime/july_ordered_v5_e5.sqlite')
    parser.add_argument('--output', default='reports/semantic_august_2026')
    args = parser.parse_args()
    model = Embedder(model=args.model)
    core = Core(embedder=model)
    engine = make_engine('sqlite:///' + Path(args.fixture).resolve().as_posix())
    Base.metadata.create_all(engine)
    def progress(counts): print(counts, flush=True)
    print(import_confirmed(engine, core, 'Data/Ngan hang thang 7-2026 FN.xlsx', progress=progress), flush=True)
    engine.dispose()
    engine = make_engine('sqlite:///' + Path(args.fixture).resolve().as_posix())
    try:
        report = run_benchmark('Data/Ngan hang thang 08.2026.xlsx', 'Data/Ngan hang thang 8-2026 FN.xlsx',
                               args.output, knowledge_engine=engine, core=core, progress=progress)
        print(report['metrics'], flush=True)
    finally:
        engine.dispose()
