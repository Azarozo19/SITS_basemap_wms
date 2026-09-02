"""Create RGB highlight-stretch variants from an existing FORCE output tile."""

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
    "/drive_mount/process/results/germany_dop10_2025/stretch_tests"
)
DEFAULT_VARIANTS = {
    "high_99_9": {"high": 2475.0, "gamma": 1.0, "saturation": 1.0},
    "high_99_9_gamma_2_0": {"high": 2475.0, "gamma": 2.0, "saturation": 1.0},
    "high_99_9_gamma_2_0_sat_0_9": {
        "high": 2475.0,
        "gamma": 2.0,
        "saturation": 0.9,
    },
    "high_99_9_gamma_2_0_sat_0_8": {
        "high": 2475.0,
        "gamma": 2.0,
        "saturation": 0.8,
    },
    "high_99_9_gamma_2_0_sat_0_8_rgb_balanced": {
        "high": 2475.0,
        "gamma": 2.0,
        "saturation": 0.8,
        "channel_gains": (1.07, 0.96, 1.15),
    },
    "high_99_9_gamma_2_0_sat_0_8_rgb_balanced_neutral_0_25": {
        "high": 2475.0,
        "gamma": 2.0,
        "saturation": 0.8,
        "channel_gains": (1.07, 0.96, 1.15),
        "neutral_protection": 0.25,
    },
    "high_99_9_gamma_2_0_sat_0_8_rgb_balanced_neutral_0_4": {
        "high": 2475.0,
        "gamma": 2.0,
        "saturation": 0.8,
        "channel_gains": (1.07, 0.96, 1.15),
        "neutral_protection": 0.4,
    },
    "high_99_9_gamma_2_0_sat_0_8_rgb_balanced_neutral_0_4_green_0_05": {
        "high": 2475.0,
        "gamma": 2.0,
        "saturation": 0.8,
        "channel_gains": (1.07, 0.96, 1.15),
        "neutral_protection": 0.4,
        "green_suppression": 0.05,
    },
    "high_99_9_gamma_2_0_sat_0_8_rgb_balanced_neutral_0_4_green_0_10": {
        "high": 2475.0,
        "gamma": 2.0,
        "saturation": 0.8,
        "channel_gains": (1.07, 0.96, 1.15),
        "neutral_protection": 0.4,
        "green_suppression": 0.10,
    },
    "high_99_9_gamma_2_0_sat_0_9_rgb_balanced": {
        "high": 2475.0,
        "gamma": 2.0,
        "saturation": 0.9,
        "channel_gains": (1.07, 0.96, 1.15),
    },
    "high_99_9_gamma_2_0_sat_0_6": {
        "high": 2475.0,
        "gamma": 2.0,
        "saturation": 0.6,
    },
    "high_99_9_gamma_2_2_sat_0_6": {
        "high": 2475.0,
        "gamma": 2.2,
        "saturation": 0.6,
    },
}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--low", type=float, default=139.0)
    parser.add_argument("--min-valid-scenes", type=int, default=3)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace test TIFFs if they already exist.",
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
        channel_gains = settings.get("channel_gains", (1.0, 1.0, 1.0))
        neutral_protection = settings.get("neutral_protection", 0.0)
        green_suppression = settings.get("green_suppression", 0.0)
        print(
            f"Creating {output_path.name}: stretch {args.low:g}-{high:g}, "
            f"gamma {gamma:g}, saturation {saturation:g}, "
            f"RGB gains {channel_gains}, neutral protection {neutral_protection:g}, "
            f"green suppression {green_suppression:g}"
        )
        export_wms_rgb(
            source,
            output_tif=output_path,
            rgb_bands=(1, 2, 3),
            valid_scene_band=5,
            stretch=(args.low, high),
            min_valid_scenes=args.min_valid_scenes,
            gamma=gamma,
            saturation=saturation,
            channel_gains=channel_gains,
            neutral_protection=neutral_protection,
            green_suppression=green_suppression,
            overwrite=args.overwrite,
        )

    print("Finished. Compare the TIFFs in QGIS inside wms_test_smaller1tile.shp.")


if __name__ == "__main__":
    main()
