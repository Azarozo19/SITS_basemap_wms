"""Prepare FORCE jobs and build WMS-ready raster products.

Use ``run_production.py`` for the versioned Germany 2025 production preset.
"""


import argparse
import glob
import os
import time
from datetime import date
from pathlib import Path

from utils.force_class_utils import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_FORCE_IMAGE,
    DEFAULT_RESOLUTION,
    DEFAULT_SENSORS,
    DEFAULT_TARGET_SENSOR,
    containerize_path,
    force_class,
    force_class_udf,
)
from utils.force_resume import (
    configure_force_resume_batch,
    finalize_force_resume,
    inspect_force_outputs,
    mark_force_batch_finished,
    mark_force_batch_running,
    parse_force_parameters,
    prepare_force_resume,
    resolve_tile_allowlist,
)
from utils.utils import create_folder_structure, execute_cmd
from utils.wms_rgb import (
    DEFAULT_BIGTIFF,
    DEFAULT_BLOCKSIZE,
    DEFAULT_COMPRESSION,
    DEFAULT_OVERVIEW_RESAMPLING,
    DEFAULT_ZLEVEL,
    PRODUCTS,
    clip_force_raw_tiles,
    collect_raw_tifs,
    render_product_tiles,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Prepare FORCE UDF jobs, execute them for all AOIs, and build WMS-ready products."
    )
    parser.add_argument("--base-path", default="/opb_mount")
    parser.add_argument("--project-name", required=True)
    parser.add_argument(
        "--aoi",
        action="append",
        required=True,
        help="AOI path or glob pattern. Repeat the flag to pass multiple patterns.",
    )
    parser.add_argument("--force-dir", default="/force:/force")
    parser.add_argument("--local-dir", default="/opb_mount:/opb_mount")
    parser.add_argument("--force-image", default=DEFAULT_FORCE_IMAGE)
    parser.add_argument("--no-sudo", action="store_true", help="Run Docker without sudo.")
    parser.add_argument("--hold", action="store_true", help="Keep xterm windows open after each FORCE command.")
    parser.add_argument(
        "--workflow",
        choices=("udf", "tsa"),
        default="udf",
        help="FORCE workflow used to generate parameter files.",
    )
    parser.add_argument("--udf-source", default="utils/skel/udf_rgb_p25_least_cloudy_block.py")
    parser.add_argument("--python-type", choices=("CHUNK", "PIXEL"), default="CHUNK")
    parser.add_argument(
        "--chunk-size",
        nargs=2,
        type=int,
        metavar=("X_METRES", "Y_METRES"),
        default=DEFAULT_CHUNK_SIZE,
        help="FORCE processing chunk in CRS units (metres in EPSG:3035), not pixels. Default: 1000 1000.",
    )
    parser.add_argument("--resolution", type=int, default=DEFAULT_RESOLUTION)
    parser.add_argument("--sensors", nargs="+", default=list(DEFAULT_SENSORS))
    parser.add_argument("--target-sensor", default=DEFAULT_TARGET_SENSOR)
    parser.add_argument(
        "--date-range",
        nargs=2,
        metavar=("START", "END"),
        help="FORCE input date range in YYYY-MM-DD format. Required when preparing a job.",
    )
    parser.add_argument("--above-noise", type=float, default=0)
    parser.add_argument("--below-noise", type=float, default=0)
    parser.add_argument("--nthread-read", type=int, default=8)
    parser.add_argument("--nthread-compute", type=int, default=22)
    parser.add_argument("--nthread-write", type=int, default=4)
    parser.add_argument(
        "--no-tile-allowlist",
        action="store_true",
        help="Process the complete rectangular X/Y tile range instead of the AOI tile allow-list.",
    )
    parser.add_argument(
        "--product",
        action="append",
        choices=tuple(PRODUCTS.keys()),
        help="Rendered output product. Repeat to build multiple products. Default: rgb",
    )
    parser.add_argument("--skip-prepare", action="store_true")
    parser.add_argument("--skip-force", action="store_true")
    resume_group = parser.add_mutually_exclusive_group()
    resume_group.add_argument(
        "--resume-force",
        dest="resume_force",
        action="store_true",
        default=True,
        help="Validate FORCE outputs and process only missing/corrupt tiles (default).",
    )
    resume_group.add_argument(
        "--no-resume-force",
        dest="resume_force",
        action="store_false",
        help="Disable tile-level recovery and execute the original FORCE parameter file.",
    )
    parser.add_argument(
        "--force-validation",
        choices=("metadata", "full"),
        default="full",
        help="Validation used before reusing FORCE outputs. Full decodes every raster block (default).",
    )
    parser.add_argument(
        "--force-resume-batch-size",
        type=int,
        default=1,
        metavar="TILES",
        help="Number of FORCE tiles per recoverable checkpoint. Default: 1 (strongest recovery).",
    )
    parser.add_argument("--skip-render", action="store_true")
    clip_group = parser.add_mutually_exclusive_group()
    clip_group.add_argument("--skip-clip", action="store_true", help="Reuse already clipped raw tiles.")
    clip_group.add_argument(
        "--render-raw",
        action="store_true",
        help="Render directly from masked FORCE tiles without creating a clipped raw copy.",
    )
    parser.add_argument("--skip-vrt", action="store_true", help="Skip VRT creation for rendered outputs.")
    parser.add_argument(
        "--skip-final-raster",
        action="store_true",
        help="Do not materialize the final GeoTIFF mosaic. By default the workflow writes both VRT and final mosaic.",
    )
    parser.add_argument(
        "--overwrite-tiles",
        action="store_true",
        help="Rebuild both clipped and rendered tile outputs (legacy combined option).",
    )
    parser.add_argument(
        "--overwrite-clipped-tiles",
        action="store_true",
        help="Rebuild clipped raw tile outputs while allowing rendered outputs to be reused.",
    )
    parser.add_argument(
        "--overwrite-rendered-tiles",
        action="store_true",
        help="Rebuild rendered product tiles while allowing clipped raw tiles to be reused.",
    )
    parser.add_argument(
        "--no-overviews",
        action="store_true",
        help="Disable overview creation on rendered tiles and final GeoTIFF outputs.",
    )
    parser.add_argument(
        "--tile-overviews",
        action="store_true",
        help="Also build per-tile overviews when a final mosaic is materialized.",
    )
    parser.add_argument("--min-valid-scenes", type=int, default=3)
    parser.add_argument("--low-pct", type=float, default=5.0)
    parser.add_argument("--high-pct", type=float, default=95.0)
    parser.add_argument("--sample-step", type=int, default=64)
    parser.add_argument(
        "--gamma",
        type=float,
        default=1.0,
        help="Display gamma. Values above 1 brighten shadows and midtones. Default: 1.",
    )
    parser.add_argument(
        "--saturation",
        type=float,
        default=1.0,
        help="Color saturation multiplier; 0 is grayscale and 1 is unchanged. Default: 1.",
    )
    parser.add_argument(
        "--rgb-gains",
        type=float,
        nargs=3,
        metavar=("RED", "GREEN", "BLUE"),
        default=(1.0, 1.0, 1.0),
        help="Post-render channel multipliers for RGB color balance. Default: 1 1 1.",
    )
    parser.add_argument(
        "--neutral-protection",
        type=float,
        default=0.0,
        help=(
            "Fade RGB gains toward neutral for low-chroma pixels. "
            "Try 0.25-0.4 to protect gray urban surfaces; 0 disables it."
        ),
    )
    parser.add_argument(
        "--green-suppression",
        type=float,
        default=0.0,
        help=(
            "Maximum proportional reduction of the green channel in "
            "green-dominant pixels. Try 0.05-0.1; 0 disables it."
        ),
    )
    parser.add_argument(
        "--green-dominance-threshold",
        type=float,
        default=0.2,
        help="Green dominance at which full green suppression is applied. Default: 0.2.",
    )
    parser.add_argument("--smooth-size", type=int)
    parser.add_argument("--balance-mode", choices=("mean_std",))
    parser.add_argument("--num-threads", default="ALL_CPUS")
    parser.add_argument("--cachemax-mb", type=int, default=512)
    parser.add_argument("--compression-method", default=DEFAULT_COMPRESSION)
    parser.add_argument("--bigtiff", default=DEFAULT_BIGTIFF)
    parser.add_argument("--zlevel", default=DEFAULT_ZLEVEL)
    parser.add_argument("--blocksize", type=int, default=DEFAULT_BLOCKSIZE)
    parser.add_argument("--overview-resampling", default=DEFAULT_OVERVIEW_RESAMPLING)
    parser.add_argument(
        "--raw-suffix",
        default="_HL_UDF_SEN2L_PYP.tif",
        help="Suffix used to find FORCE output tiles for rendering.",
    )
    args = parser.parse_args()
    if not args.skip_prepare and not args.date_range:
        parser.error("--date-range START END is required unless --skip-prepare is used")
    if args.date_range:
        try:
            start, end = (date.fromisoformat(value) for value in args.date_range)
        except ValueError as exc:
            parser.error(f"invalid --date-range: {exc}")
        if start > end:
            parser.error("--date-range START must be before or equal to END")
    if any(value <= 0 for value in args.chunk_size):
        parser.error("--chunk-size values must be positive")
    if args.resolution <= 0:
        parser.error("--resolution must be positive")
    if any(value <= 0 for value in (args.nthread_read, args.nthread_compute, args.nthread_write)):
        parser.error("FORCE thread counts must be positive")
    if args.force_resume_batch_size <= 0:
        parser.error("--force-resume-batch-size must be positive")
    if args.gamma <= 0:
        parser.error("--gamma must be positive")
    if args.saturation < 0:
        parser.error("--saturation must be non-negative")
    if any(gain <= 0 for gain in args.rgb_gains):
        parser.error("--rgb-gains values must be positive")
    if args.neutral_protection < 0:
        parser.error("--neutral-protection must be non-negative")
    if not 0 <= args.green_suppression < 1:
        parser.error("--green-suppression must be at least 0 and less than 1")
    if args.green_dominance_threshold <= 0:
        parser.error("--green-dominance-threshold must be positive")
    return args


def expand_aois(patterns):
    aois = []
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        if matches:
            aois.extend(matches)
            continue
        if os.path.exists(pattern):
            aois.append(pattern)
    unique_aois = list(dict.fromkeys(aois))
    if not unique_aois:
        raise FileNotFoundError(f"No AOIs matched the provided patterns: {patterns}")
    return unique_aois


def get_aoi_job_paths(base_path, project_name, aoi_path, workflow):
    aoi_name = os.path.basename(aoi_path)
    job_root = Path(base_path) / "process" / "temp" / project_name / "FORCE" / aoi_name
    param_name = "tsa_UDF.prm" if workflow == "udf" else "tsa.prm"
    return {
        "aoi": aoi_path,
        "name": aoi_name,
        "job_root": job_root,
        "params_path": job_root / param_name,
        "raw_tiles_root": job_root / "tiles_tss",
    }


def prepare_force_jobs(args, aois):
    create_folder_structure(args.base_path)
    force_options = {
        "force_image": args.force_image,
        "chunk_size": tuple(args.chunk_size),
        "resolution": args.resolution,
        "sensors": tuple(args.sensors),
        "target_sensor": args.target_sensor,
        "date_range": tuple(args.date_range) if args.date_range else None,
        "above_noise": args.above_noise,
        "below_noise": args.below_noise,
        "nthread_read": args.nthread_read,
        "nthread_compute": args.nthread_compute,
        "nthread_write": args.nthread_write,
        "use_tile_allowlist": not args.no_tile_allowlist,
        "use_sudo": not args.no_sudo,
    }
    if args.workflow == "udf":
        force_class_udf(
            args.project_name,
            args.force_dir,
            args.local_dir,
            args.base_path,
            aois,
            args.hold,
            udf_source=args.udf_source,
            python_type=args.python_type,
            **force_options,
        )
    else:
        force_class(
            args.project_name,
            args.force_dir,
            args.local_dir,
            args.base_path,
            aois,
            args.hold,
            **force_options,
        )


def execute_force_jobs(args, jobs):
    for job in jobs:
        if not job["params_path"].exists():
            raise FileNotFoundError(f"Missing FORCE parameter file: {job['params_path']}")

        if not args.resume_force:
            print(f"Running FORCE without resume validation for {job['name']}")
            started = time.time()
            execute_cmd(
                containerize_path(job["params_path"], args.local_dir),
                args.hold,
                args.local_dir,
                args.force_dir,
                force_image=args.force_image,
                use_sudo=not args.no_sudo,
            )
            print(f"FORCE completed for {job['name']} in {(time.time() - started) / 3600:.2f} hours")
            continue

        print(f"Checking FORCE outputs for {job['name']} ({args.force_validation} validation)")
        plan = prepare_force_resume(
            job_root=job["job_root"],
            params_path=job["params_path"],
            raw_tiles_root=job["raw_tiles_root"],
            force_image=args.force_image,
            raw_suffix=args.raw_suffix,
            validation_mode=args.force_validation,
        )
        print(
            f"FORCE resume scan for {job['name']}: {len(plan['complete'])} complete, "
            f"{len(plan['remaining'])} remaining"
        )
        for quarantine_path in plan["quarantine_paths"]:
            print(f"Untrusted/corrupt outputs were preserved under: {quarantine_path}")
        if not plan["remaining"]:
            print(f"FORCE already complete for {job['name']}; execution skipped")
            continue

        started = time.time()
        batches = [
            plan["remaining"][index : index + args.force_resume_batch_size]
            for index in range(0, len(plan["remaining"]), args.force_resume_batch_size)
        ]
        for batch_number, batch_tiles in enumerate(batches, start=1):
            batch_params = configure_force_resume_batch(plan, batch_tiles)
            mark_force_batch_running(plan, batch_tiles)
            print(
                f"Running FORCE checkpoint {batch_number}/{len(batches)} for {job['name']}: "
                f"{', '.join(batch_tiles)}"
            )
            execute_cmd(
                containerize_path(batch_params, args.local_dir),
                args.hold,
                args.local_dir,
                args.force_dir,
                force_image=args.force_image,
                use_sudo=not args.no_sudo,
            )
            batch_scan = inspect_force_outputs(
                job["raw_tiles_root"],
                batch_tiles,
                args.raw_suffix,
                validation_mode=args.force_validation,
                expected_resolution=plan["expected_resolution"],
            )
            batch_remaining = batch_scan["missing"] + list(batch_scan["corrupt"])
            if batch_remaining:
                raise RuntimeError(
                    f"FORCE checkpoint {batch_number} exited successfully but failed validation "
                    f"for: {', '.join(batch_remaining)}. Rerun the same command; this active "
                    "checkpoint will be quarantined and retried."
                )
            mark_force_batch_finished(plan, batch_tiles)
        state = finalize_force_resume(
            plan=plan,
            raw_tiles_root=job["raw_tiles_root"],
            raw_suffix=args.raw_suffix,
            validation_mode=args.force_validation,
        )
        if state["remaining_tiles"]:
            raise RuntimeError(
                f"FORCE exited successfully but {len(state['remaining_tiles'])} tile(s) are still "
                f"missing or corrupt for {job['name']}. See {plan['state_path']} and rerun the "
                "same command to retry only those tiles."
            )
        print(
            f"FORCE completed and validated {len(state['complete_tiles'])} tile(s) for "
            f"{job['name']} in {(time.time() - started) / 3600:.2f} hours"
        )


def validate_skipped_force_jobs(args, jobs):
    """Refuse to render an incomplete/corrupt FORCE job when FORCE is skipped."""
    if not args.resume_force:
        return
    for job in jobs:
        if not job["params_path"].exists():
            raise FileNotFoundError(f"Missing FORCE parameter file: {job['params_path']}")
        _, tile_ids = resolve_tile_allowlist(job["params_path"])
        parameters = parse_force_parameters(job["params_path"])
        expected_resolution = float(parameters["RESOLUTION"]) if "RESOLUTION" in parameters else None
        scan = inspect_force_outputs(
            job["raw_tiles_root"],
            tile_ids,
            args.raw_suffix,
            validation_mode=args.force_validation,
            expected_resolution=expected_resolution,
        )
        remaining = scan["missing"] + list(scan["corrupt"])
        if remaining:
            preview = ", ".join(remaining[:10])
            if len(remaining) > 10:
                preview += ", ..."
            raise RuntimeError(
                f"Cannot --skip-force for {job['name']}: {len(remaining)} required FORCE tile(s) "
                f"are missing or corrupt ({preview}). Remove --skip-force to resume them."
            )
        print(f"Validated {len(scan['complete'])} FORCE tile(s) for {job['name']}")


def _collect_clipped_tiles(job, clipped_root_dir: Path):
    clipped_job_dir = clipped_root_dir / job["name"]
    clipped_tifs = collect_raw_tifs(clipped_job_dir, suffix=".tif")
    if not clipped_tifs:
        raise FileNotFoundError(
            f"No clipped raw tiles found for {job['name']} under {clipped_job_dir}. "
            f"Remove --skip-clip or check the prior clipping run."
        )
    return clipped_tifs


def render_products(args, jobs, products):
    project_results_dir = Path(args.base_path) / "process" / "results" / args.project_name
    clipped_root_dir = project_results_dir / "raw_clipped_tiles"
    clipped_root_dir.mkdir(parents=True, exist_ok=True)

    clipped_raw_tifs = []
    for job in jobs:
        job_raw_tifs = collect_raw_tifs(job["raw_tiles_root"], suffix=args.raw_suffix)
        if not job_raw_tifs:
            print(f"No raw tiles found for {job['name']} under {job['raw_tiles_root']}")
            continue

        if args.render_raw:
            job_clipped_tifs = job_raw_tifs
        elif args.skip_clip:
            job_clipped_tifs = _collect_clipped_tiles(job, clipped_root_dir)
        else:
            clipped_output_dir = clipped_root_dir / job["name"]
            report_path = project_results_dir / f"{job['name']}_raw_clip_report.json"
            job_clipped_tifs, report = clip_force_raw_tiles(
                raw_tifs=job_raw_tifs,
                job_name=job["name"],
                aoi_path=Path(job["aoi"]),
                output_dir=clipped_output_dir,
                num_threads=args.num_threads,
                cachemax_mb=args.cachemax_mb,
                overwrite_tiles=args.overwrite_tiles or args.overwrite_clipped_tiles,
                compression_method=args.compression_method,
                bigtiff=args.bigtiff,
                zlevel=args.zlevel,
                blocksize=args.blocksize,
                report_path=report_path,
            )
            print(
                f"[clip:{job['name']}] prepared {report['written_or_reused_tiles']} tile(s); "
                f"reused {report['reused_tiles']}; skipped {report['skipped_tiles']}"
            )

        clipped_raw_tifs.extend(job_clipped_tifs)

    if not clipped_raw_tifs:
        raise FileNotFoundError("No clipped FORCE tiles were found for rendering.")

    for product_name in products:
        product_dir = project_results_dir / f"{product_name}_tiles"
        vrt_path = None if args.skip_vrt else project_results_dir / f"{args.project_name}_{product_name}_mosaic.vrt"
        final_raster_path = (
            None
            if args.skip_final_raster
            else project_results_dir / f"{args.project_name}_{product_name}_mosaic.tif"
        )
        report_path = project_results_dir / f"{args.project_name}_{product_name}_report.json"
        result = render_product_tiles(
            clipped_raw_tifs,
            product_name=product_name,
            output_dir=product_dir,
            vrt_output_path=vrt_path,
            final_output_path=final_raster_path,
            report_path=report_path,
            low_pct=args.low_pct,
            high_pct=args.high_pct,
            sample_step=args.sample_step,
            min_valid_scenes=args.min_valid_scenes,
            gamma=args.gamma,
            saturation=args.saturation,
            channel_gains=args.rgb_gains,
            neutral_protection=args.neutral_protection,
            green_suppression=args.green_suppression,
            green_dominance_threshold=args.green_dominance_threshold,
            smooth_size=args.smooth_size,
            balance_mode=args.balance_mode,
            overwrite_tiles=args.overwrite_tiles or args.overwrite_rendered_tiles,
            build_overviews=not args.no_overviews,
            build_tile_overviews=args.tile_overviews,
            overview_resampling=args.overview_resampling,
            skip_vrt=args.skip_vrt,
            skip_final_raster=args.skip_final_raster,
            compression_method=args.compression_method,
            bigtiff=args.bigtiff,
            zlevel=args.zlevel,
            blocksize=args.blocksize,
        )
        stretch = result["stretch"]
        print(f"[{product_name}] Processed {len(result['tile_outputs'])} tiles")
        print(f"[{product_name}] Global stretch: {stretch[0]:.3f} - {stretch[1]:.3f}")
        if result["vrt_output"]:
            print(f"[{product_name}] VRT written to: {result['vrt_output']}")
        if result["final_raster_output"]:
            print(f"[{product_name}] Final raster written to: {result['final_raster_output']}")


def main():
    args = parse_args()
    products = args.product or ["rgb"]
    aois = expand_aois(args.aoi)
    jobs = [get_aoi_job_paths(args.base_path, args.project_name, aoi, args.workflow) for aoi in aois]

    print(f"Resolved {len(aois)} AOI(s) for project {args.project_name}")
    for job in jobs:
        print(f"- {job['name']}")

    if not args.skip_prepare:
        prepare_force_jobs(args, aois)

    if not args.skip_force:
        execute_force_jobs(args, jobs)
    elif not args.skip_render:
        validate_skipped_force_jobs(args, jobs)

    if not args.skip_render:
        render_products(args, jobs, products)


if __name__ == "__main__":
    main()
