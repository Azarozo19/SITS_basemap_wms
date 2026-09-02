"""Create softer CIR display variants from an existing FORCE output tile."""

from __future__ import annotations

import argparse
from pathlib import Path

from utils.wms_rgb import export_wms_rgb


DEFAULT_SOURCE = Path(
    "/drive_mount/process/results/germany_dop10_2025/"
    "raw_clipped_tiles/2024_v1_0.shp/"
    "X0053_Y0049_PYP_2025-2025_001-365_HL_UDF_SEN2L_PYP.tif"
)
DEFAULT_OUTPUT_DIR = Path(
    "/drive_mount/process/results/germany_dop10_2025/cir_stretch_tests"
)

# The 166-2975 stretch is the original national 5th-95th percentile result.
# The 166-4124 stretch is the measured national 5th-99.9th percentile result.
DEFAULT_VARIANTS = {
    "cir_high_95_original": {"high": 2975.0, "gamma": 1.0, "saturation": 1.0},
    "cir_high_99_9": {"high": 4124.0, "gamma": 1.0, "saturation": 1.0},
    "cir_high_99_9_sat_0_85": {
        "high": 4124.0,
        "gamma": 1.0,
        "saturation": 0.85,
    },
    "cir_high_99_9_sat_0_70": {
        "high": 4124.0,
        "gamma": 1.0,
        "saturation": 0.70,
    },
    "cir_high_99_9_gamma_1_3_sat_0_80": {
        "high": 4124.0,
        "gamma": 1.3,
        "saturation": 0.80,
    },
}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--low", type=float, default=166.0)
    parser.add_argument("--min-valid-scenes", type=int, default=3)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace CIR test TIFFs if they already exist.",
    )
    args = parser.parse_args()
    if args.min_valid_scenes <= 0:
        parser.error("--min-valid-scenes must be positive")
    return args


def main():
    args = parse_args()
    source = args.source.resolve()
    output_dir = args.output_dir.resolve()

    if not source.is_file():
        raise FileNotFoundError(f"Source FORCE tile does not exist: {source}")

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Source: {source}")
    print(f"Output directory: {output_dir}")

    for name, settings in DEFAULT_VARIANTS.items():
        output_path = output_dir / f"{name}.tif"
        high = settings["high"]
        gamma = settings["gamma"]
        saturation = settings["saturation"]
        print(
            f"Creating {output_path.name}: stretch {args.low:g}-{high:g}, "
            f"gamma {gamma:g}, saturation {saturation:g}"
        )
        export_wms_rgb(
            source,
            output_tif=output_path,
            rgb_bands=(4, 1, 2),
            valid_scene_band=5,
            stretch=(args.low, high),
            min_valid_scenes=args.min_valid_scenes,
            band_descriptions=("NIR", "RED", "GREEN"),
            gamma=gamma,
            saturation=saturation,
            overwrite=args.overwrite,
        )

    print("Finished. Compare the CIR TIFFs in QGIS inside wms_test_smaller1tile.shp.")


if __name__ == "__main__":
    main()
