#!/usr/bin/env python3
"""Run a resumable FORCE/WMS production preset from one command."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import rasterio

from utils.force_resume import parse_force_parameters


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = REPO_ROOT / "configs" / "germany_dop10_2025.json"
EXPECTED_DESCRIPTIONS = {
    "rgb": ("RED", "GREEN", "BLUE", "ALPHA", "VALID_SCENES"),
    "cir": ("NIR", "RED", "GREEN", "ALPHA", "VALID_SCENES"),
}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--rebuild-products",
        action="store_true",
        help="Rebuild RGB and CIR even when their reports and rasters are current.",
    )
    parser.add_argument(
        "--metadata-validation",
        action="store_true",
        help="Use faster metadata-only FORCE validation for this run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned commands and current-product decisions without executing them.",
    )
    return parser.parse_args()


def load_config(path: Path) -> dict:
    config_path = path.resolve()
    config = json.loads(config_path.read_text())
    if config.get("schema_version") != 1:
        raise ValueError(f"Unsupported production config schema: {config.get('schema_version')}")
    for key in ("project_name", "base_path", "aoi", "force", "render", "products"):
        if key not in config:
            raise ValueError(f"Production config is missing '{key}': {config_path}")
    unknown_products = set(config["products"]) - set(EXPECTED_DESCRIPTIONS)
    if unknown_products:
        raise ValueError(f"Unsupported configured products: {sorted(unknown_products)}")
    if not config["products"]:
        raise ValueError("Production config must contain at least one product")
    return config


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _job_paths(config: dict) -> tuple[Path, Path]:
    aoi_name = Path(config["aoi"]).name
    job_root = (
        Path(config["base_path"])
        / "process"
        / "temp"
        / config["project_name"]
        / "FORCE"
        / aoi_name
    )
    param_name = "tsa_UDF.prm" if config["force"]["workflow"] == "udf" else "tsa.prm"
    return job_root, job_root / param_name


def validate_existing_job(config: dict, params_path: Path) -> None:
    """Refuse to mix an existing FORCE project with a different preset."""
    parameters = parse_force_parameters(params_path)
    force = config["force"]
    expected = {
        "DATE_RANGE": " ".join(force["date_range"]),
        "CHUNK_SIZE": " ".join(str(value) for value in force["chunk_size"]),
        "RESOLUTION": str(force["resolution"]),
        "SENSORS": " ".join(force["sensors"]),
        "TARGET_SENSOR": force["target_sensor"],
        "ABOVE_NOISE": str(force["above_noise"]),
        "BELOW_NOISE": str(force["below_noise"]),
        "NTHREAD_READ": str(force["threads"]["read"]),
        "NTHREAD_COMPUTE": str(force["threads"]["compute"]),
        "NTHREAD_WRITE": str(force["threads"]["write"]),
    }
    if force["workflow"] == "udf":
        expected.update({"PYTHON_TYPE": force["python_type"], "OUTPUT_PYP": "TRUE"})

    mismatches = [
        f"{key}: existing={parameters.get(key)!r}, configured={value!r}"
        for key, value in expected.items()
        if parameters.get(key) != value
    ]

    if force["workflow"] == "udf":
        configured_udf = (REPO_ROOT / force["udf_source"]).resolve()
        copied_udf = params_path.parent / configured_udf.name
        if not copied_udf.is_file():
            mismatches.append(f"copied UDF is missing: {copied_udf}")
        elif _sha256(configured_udf) != _sha256(copied_udf):
            mismatches.append("configured UDF differs from the UDF used by the existing FORCE job")

    if mismatches:
        details = "\n  - ".join(mismatches)
        raise RuntimeError(
            "Existing FORCE job does not match the production preset. Do not mix outputs.\n"
            f"  - {details}\nUse a new project_name or restore the matching preset."
        )


def _numbers_equal(left, right, tolerance=1e-9) -> bool:
    if isinstance(right, (list, tuple)):
        if not isinstance(left, (list, tuple)):
            return False
        return len(left) == len(right) and all(
            _numbers_equal(actual, expected, tolerance) for actual, expected in zip(left, right)
        )
    if isinstance(right, (int, float)):
        try:
            return abs(float(left) - float(right)) <= tolerance
        except (TypeError, ValueError):
            return False
    return left == right


def product_is_current(config: dict, product_name: str) -> tuple[bool, str]:
    result_root = Path(config["base_path"]) / "process" / "results" / config["project_name"]
    report_path = result_root / f"{config['project_name']}_{product_name}_report.json"
    raster_path = result_root / f"{config['project_name']}_{product_name}_mosaic.tif"
    if not report_path.is_file() or not raster_path.is_file():
        return False, "report or final raster is missing"

    try:
        report = json.loads(report_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"report is unreadable: {exc}"

    render = config["render"]
    product = config["products"][product_name]
    expected_report = {
        "product_name": product_name,
        "gamma": product["gamma"],
        "saturation": product["saturation"],
        "channel_gains": product["rgb_gains"],
        "neutral_protection": product["neutral_protection"],
        "green_suppression": product["green_suppression"],
        "green_dominance_threshold": product["green_dominance_threshold"],
    }
    for key, expected in expected_report.items():
        if key not in report or not _numbers_equal(report[key], expected):
            return False, f"report setting '{key}' does not match the preset"

    percentile_fields = ("low_pct", "high_pct", "sample_step", "min_valid_scenes")
    if all(key in report for key in percentile_fields):
        expected_render = {
            "low_pct": render["low_pct"],
            "high_pct": render["high_pct"],
            "sample_step": render["sample_step"],
            "min_valid_scenes": render["min_valid_scenes"],
        }
        for key, expected in expected_render.items():
            if not _numbers_equal(report[key], expected):
                return False, f"report setting '{key}' does not match the preset"
    else:
        # Compatibility for reports produced before percentile controls were recorded.
        stretch = report.get("stretch", {})
        actual_stretch = [stretch.get("low"), stretch.get("high")]
        if not _numbers_equal(actual_stretch, product["expected_stretch"]):
            return False, "legacy report stretch does not match the preset"

    if report.get("tile_count", 0) <= 0:
        return False, "report contains no rendered tiles"
    report_mtime = report_path.stat().st_mtime
    for tile in report.get("tiles", []):
        source_path = Path(tile.get("source_path", ""))
        output_path = Path(tile.get("output_path", ""))
        if not source_path.is_file() or not output_path.is_file():
            return False, "a reported source or rendered tile is missing"
        if source_path.stat().st_mtime > report_mtime:
            return False, "a source tile is newer than the product report"

    try:
        with rasterio.open(raster_path) as raster:
            if raster.count != 5 or raster.width <= 0 or raster.height <= 0:
                return False, "final raster dimensions or band count are invalid"
            if raster.descriptions != EXPECTED_DESCRIPTIONS[product_name]:
                return False, "final raster band descriptions are incorrect"
            if str(raster.crs) != "EPSG:3035":
                return False, "final raster CRS is not EPSG:3035"
            if raster.compression is None or raster.compression.name.lower() != "zstd":
                return False, "final raster is not ZSTD-compressed"
            if not raster.overviews(1):
                return False, "final raster has no internal overviews"
            center = ((raster.height // 2, raster.height // 2 + 1), (raster.width // 2, raster.width // 2 + 1))
            raster.read(window=center)
    except Exception as exc:
        return False, f"final raster validation failed: {exc}"

    return True, "report, source timestamps, and final raster match"


def _common_args(config: dict) -> list[str]:
    force = config["force"]
    args = [
        sys.executable,
        str(REPO_ROOT / "force_wms.py"),
        "--base-path",
        config["base_path"],
        "--project-name",
        config["project_name"],
        "--aoi",
        config["aoi"],
        "--force-dir",
        force["force_mount"],
        "--local-dir",
        force["local_mount"],
        "--force-image",
        force["image"],
        "--workflow",
        force["workflow"],
    ]
    if not force["use_sudo"]:
        args.append("--no-sudo")
    return args


def force_stage_command(config: dict, params_exist: bool, metadata_validation: bool) -> list[str]:
    force = config["force"]
    command = _common_args(config)
    command.extend(
        [
            "--udf-source",
            force["udf_source"],
            "--python-type",
            force["python_type"],
            "--date-range",
            *force["date_range"],
            "--chunk-size",
            *(str(value) for value in force["chunk_size"]),
            "--resolution",
            str(force["resolution"]),
            "--sensors",
            *force["sensors"],
            "--target-sensor",
            force["target_sensor"],
            "--above-noise",
            str(force["above_noise"]),
            "--below-noise",
            str(force["below_noise"]),
            "--nthread-read",
            str(force["threads"]["read"]),
            "--nthread-compute",
            str(force["threads"]["compute"]),
            "--nthread-write",
            str(force["threads"]["write"]),
            "--force-validation",
            "metadata" if metadata_validation else force["validation"],
            "--force-resume-batch-size",
            str(force["resume_batch_size"]),
            "--skip-render",
        ]
    )
    if params_exist:
        command.append("--skip-prepare")
    return command


def render_stage_command(config: dict, product_name: str, skip_clip: bool) -> list[str]:
    render = config["render"]
    product = config["products"][product_name]
    command = _common_args(config)
    command.extend(
        [
            "--skip-prepare",
            "--skip-force",
            "--no-resume-force",
            "--product",
            product_name,
            "--min-valid-scenes",
            str(render["min_valid_scenes"]),
            "--low-pct",
            str(render["low_pct"]),
            "--high-pct",
            str(render["high_pct"]),
            "--sample-step",
            str(render["sample_step"]),
            "--gamma",
            str(product["gamma"]),
            "--saturation",
            str(product["saturation"]),
            "--rgb-gains",
            *(str(value) for value in product["rgb_gains"]),
            "--neutral-protection",
            str(product["neutral_protection"]),
            "--green-suppression",
            str(product["green_suppression"]),
            "--green-dominance-threshold",
            str(product["green_dominance_threshold"]),
            "--compression-method",
            render["compression_method"],
            "--bigtiff",
            render["bigtiff"],
            "--zlevel",
            str(render["zlevel"]),
            "--blocksize",
            str(render["blocksize"]),
            "--overview-resampling",
            render["overview_resampling"],
            "--num-threads",
            render["num_threads"],
            "--cachemax-mb",
            str(render["cachemax_mb"]),
            "--overwrite-rendered-tiles",
        ]
    )
    if skip_clip:
        command.append("--skip-clip")
    return command


def run_logged(command: list[str], log_path: Path, dry_run: bool) -> None:
    printable = shlex.join(command)
    print(f"\n$ {printable}", flush=True)
    if dry_run:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n$ {printable}\n")
        process = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
            log.flush()
        return_code = process.wait()
    if return_code:
        raise subprocess.CalledProcessError(return_code, command)


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    aoi_path = Path(config["aoi"])
    if not aoi_path.is_file():
        raise FileNotFoundError(f"Configured AOI does not exist: {aoi_path}")

    job_root, params_path = _job_paths(config)
    params_exist = params_path.is_file()
    if params_exist:
        validate_existing_job(config, params_path)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = REPO_ROOT / "logs" / f"{config['project_name']}_{timestamp}.log"
    print(f"Production preset: {args.config.resolve()}")
    print(f"Project: {config['project_name']}")
    print(f"Log: {log_path}")

    run_logged(
        force_stage_command(config, params_exist, args.metadata_validation),
        log_path,
        args.dry_run,
    )

    stale_products = []
    for product_name in config["products"]:
        current, reason = product_is_current(config, product_name)
        if args.rebuild_products:
            current, reason = False, "--rebuild-products was requested"
        print(f"[{product_name}] {'current' if current else 'rebuild'}: {reason}")
        if not current:
            stale_products.append(product_name)

    for index, product_name in enumerate(stale_products):
        run_logged(
            render_stage_command(config, product_name, skip_clip=index > 0),
            log_path,
            args.dry_run,
        )
        if not args.dry_run:
            current, reason = product_is_current(config, product_name)
            if not current:
                raise RuntimeError(f"{product_name} failed final validation: {reason}")

    if args.dry_run:
        print("\nDry run complete; no commands were executed.")
    else:
        print("\nProduction run complete; all configured products are current.")


if __name__ == "__main__":
    main()
