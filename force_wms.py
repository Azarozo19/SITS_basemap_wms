"""python /rvt_mount/SITS_basemap_wms/force_wms.py \
  --project-name zGermany_full_tiles \
  --aoi "/path/to/germany.shp" \
  --workflow udf \
  --udf-source utils/skel/udf_rgb_p25_least_cloudy_block.py \
  --product rgb \
  --product cir"""


import argparse
import glob
import os
from pathlib import Path

from utils.force_class_utils import force_class, force_class_udf
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
    parser.add_argument("--base-path", default="/rvt_mount")
    parser.add_argument("--project-name", required=True)
    parser.add_argument(
        "--aoi",
        action="append",
        required=True,
        help="AOI path or glob pattern. Repeat the flag to pass multiple patterns.",
    )
    parser.add_argument("--force-dir", default="/force:/force")
    parser.add_argument("--local-dir", default="/rvt_mount:/rvt_mount")
    parser.add_argument("--hold", action="store_true", help="Keep xterm windows open after each FORCE command.")
    parser.add_argument(
        "--workflow",
        choices=("udf", "tsa"),
        default="udf",
        help="FORCE workflow used to generate parameter files.",
    )
    parser.add_argument("--udf-source", default="utils/skel/udf_rgb_p25_least_cloudy_block.py")
    parser.add_argument("--python-type", default="CHUNK")
    parser.add_argument(
        "--product",
        action="append",
        choices=tuple(PRODUCTS.keys()),
        help="Rendered output product. Repeat to build multiple products. Default: rgb",
    )
    parser.add_argument("--skip-prepare", action="store_true")
    parser.add_argument("--skip-force", action="store_true")
    parser.add_argument("--skip-render", action="store_true")
    parser.add_argument("--skip-clip", action="store_true", help="Reuse already clipped raw tiles.")
    parser.add_argument("--skip-vrt", action="store_true", help="Skip VRT creation for rendered outputs.")
    parser.add_argument(
        "--skip-final-raster",
        action="store_true",
        help="Do not materialize the final GeoTIFF mosaic. By default the workflow writes both VRT and final mosaic.",
    )
    parser.add_argument("--overwrite-tiles", action="store_true", help="Rebuild clipped and rendered tile outputs.")
    parser.add_argument(
        "--no-overviews",
        action="store_true",
        help="Disable overview creation on rendered tiles and final GeoTIFF outputs.",
    )
    parser.add_argument("--min-valid-scenes", type=int, default=3)
    parser.add_argument("--low-pct", type=float, default=5.0)
    parser.add_argument("--high-pct", type=float, default=95.0)
    parser.add_argument("--sample-step", type=int, default=64)
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
    return parser.parse_args()


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
        )
    else:
        force_class(
            args.project_name,
            args.force_dir,
            args.local_dir,
            args.base_path,
            aois,
            args.hold,
        )


def execute_force_jobs(args, jobs):
    for job in jobs:
        if not job["params_path"].exists():
            raise FileNotFoundError(f"Missing FORCE parameter file: {job['params_path']}")
        print(f"Running FORCE for {job['name']}")
        execute_cmd(str(job["params_path"]), args.hold, args.local_dir, args.force_dir)


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

        if args.skip_clip:
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
                overwrite_tiles=args.overwrite_tiles,
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
            smooth_size=args.smooth_size,
            balance_mode=args.balance_mode,
            overwrite_tiles=args.overwrite_tiles,
            build_overviews=not args.no_overviews,
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

    if not args.skip_render:
        render_products(args, jobs, products)


if __name__ == "__main__":
    main()
