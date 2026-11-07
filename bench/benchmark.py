"""Medicion reproducible local. No representa rendimiento de produccion."""

import argparse
from pathlib import Path
import statistics
import tempfile
import time

from synapsys_memory import MemoryStore


def run(entries: int, queries: int):
    with tempfile.TemporaryDirectory() as directory:
        with MemoryStore(Path(directory) / "memory.sqlite3", space="bench") as db:
            start = time.perf_counter()
            for idx in range(entries):
                db.remember(
                    f"Proyecto {idx % 25}: decision sobre archivos, arquitectura, consenso, "
                    f"rendimiento y version {idx}. Resultado persistido sin dependencias.",
                    title=f"Decision tecnica {idx}", kind="decision",
                )
            write_elapsed = time.perf_counter() - start
            latencies = []
            for idx in range(queries):
                before = time.perf_counter()
                db.recall(f"arquitectura decision {idx % 25}", limit=5)
                latencies.append((time.perf_counter() - before) * 1000)
            ordered = sorted(latencies)
            print(f"memorias={entries} consultas={queries} inserciones_por_segundo={entries / write_elapsed:.1f}")
            print(f"busqueda_ms_mediana={statistics.median(latencies):.3f} "
                  f"p95={ordered[max(0, int(len(ordered)*.95)-1)]:.3f} "
                  f"max={max(latencies):.3f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--entries", type=int, default=2000)
    parser.add_argument("--queries", type=int, default=100)
    opts = parser.parse_args()
    run(opts.entries, opts.queries)
