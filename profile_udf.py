"""Profile a FORCE chunk UDF inside the same Python runtime used in production."""

import argparse
import cProfile
import importlib.util
import resource
import time
from pathlib import Path

import numpy as np


DEFAULT_BANDS = np.array(
    [
        b"BLUE",
        b"GREEN",
        b"RED",
        b"BROADNIR",
        b"SWIR1",
        b"SWIR2",
        b"RE1",
        b"RE2",
        b"RE3",
        b"NIR",
    ]
)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--udf-source", default="utils/skel/udf_rgb_p25_least_cloudy_block.py")
    parser.add_argument("--dates", type=int, default=100)
    parser.add_argument("--rows", type=int, default=100)
    parser.add_argument("--cols", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--invalid-fraction", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--profile-output", default="udf_profile.prof")
    args = parser.parse_args()
    if min(args.dates, args.rows, args.cols, args.repeats) <= 0:
        parser.error("dates, rows, columns, and repeats must be positive")
    if not 0 <= args.invalid_fraction <= 1:
        parser.error("invalid-fraction must be between 0 and 1")
    return args


def load_udf(path):
    source = Path(path).resolve()
    spec = importlib.util.spec_from_file_location("profiled_force_udf", source)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load UDF: {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    args = parse_args()
    udf = load_udf(args.udf_source)
    rng = np.random.default_rng(args.seed)
    values = rng.integers(
        1,
        10001,
        size=(args.dates, len(DEFAULT_BANDS), args.rows, args.cols),
        dtype=np.int16,
    )
    values[rng.random(values.shape) < args.invalid_fraction] = -9999
    output = np.full((5, args.rows, args.cols), -9999, dtype=np.int16)
    udf.forcepy_init(None, None, DEFAULT_BANDS)

    # Warm NumPy paths before measuring.
    udf.forcepy_chunk(values, output, None, None, DEFAULT_BANDS, -9999, 1)
    profiler = cProfile.Profile()
    started = time.perf_counter()
    profiler.enable()
    for _ in range(args.repeats):
        udf.forcepy_chunk(values, output, None, None, DEFAULT_BANDS, -9999, 1)
    profiler.disable()
    elapsed = time.perf_counter() - started
    profiler.dump_stats(args.profile_output)

    peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(f"Input shape: {values.shape}")
    print(f"Elapsed: {elapsed:.3f}s total; {elapsed / args.repeats:.4f}s per UDF call")
    print(f"Process peak RSS: {peak_rss_mb:.1f} MiB")
    print(f"Profile written to: {Path(args.profile_output).resolve()}")


if __name__ == "__main__":
    main()
