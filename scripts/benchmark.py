import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.benchmark import run_benchmark
from app.core import Core
from app.db import make_engine, default_url

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--raw', default='Data/Ngan hang thang 08.2026.xlsx')
    parser.add_argument('--truth', default='Data/Ngan hang thang 8-2026 FN.xlsx')
    parser.add_argument('--output', default='reports/august_2026')
    parser.add_argument('--batch-size', type=int, default=1000)
    args = parser.parse_args()
    def progress(counts):
        print('Progress:', counts, flush=True)
    url = default_url()
    if not url.startswith('postgresql'):
        parser.error('Benchmark uses saved July knowledge in your initialized PostgreSQL database')
    engine = make_engine(url)
    try:
        report = run_benchmark(args.raw, args.truth, args.output, batch_size=args.batch_size, progress=progress,
                               knowledge_engine=engine, core=Core())
    finally:
        engine.dispose()
    print(report['metrics'])
    print('Report:', Path(args.output).resolve() / 'benchmark.md')
