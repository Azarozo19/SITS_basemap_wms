'''python force_wms.py \
  --project-name zGermany_full_tiles \
  --aoi "/rvt_mount/3DTests/data/harm_data/shp_germany_border.shp" \
  --workflow udf \
  --udf-source utils/skel/udf_rgb_p25_least_cloudy_block.py \
  --product rgb \
  --product cir'''


import argparse
import glob
import os
from pathlib import Path

from utils.force_class_utils import force_class, force_class_udf
from utils.utils import create_folder_structure, execute_cmd
from utils.wms_rgb import PRODUCTS, batch_export_product, collect_raw_tifs, mosaic_wms_rgb


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
    parser.add_argument("--no-mosaic", action="store_true")
    parser.add_argument("--min-valid-scenes", type=int, default=3)
    parser.add_argument("--low-pct", type=float, default=5.0)
    parser.add_argument("--high-pct", type=float, default=95.0)
    parser.add_argument("--sample-step", type=int, default=64)
    parser.add_argument("--smooth-size", type=int)
    parser.add_argument("--balance-mode", choices=("mean_std",))
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


def render_products(args, jobs, products):
    raw_tifs = []
    for job in jobs:
        job_raw_tifs = collect_raw_tifs(job["raw_tiles_root"], suffix=args.raw_suffix)
        if not job_raw_tifs:
            print(f"No raw tiles found for {job['name']} under {job['raw_tiles_root']}")
            continue
        raw_tifs.extend(job_raw_tifs)

    if not raw_tifs:
        raise FileNotFoundError("No raw FORCE tiles were found for rendering.")

    mosaic_dir = Path(args.base_path) / "process" / "temp" / args.project_name / "FORCE"
    for product_name in products:
        outputs, stretch = batch_export_product(
            raw_tifs,
            product_name=product_name,
            low_pct=args.low_pct,
            high_pct=args.high_pct,
            sample_step=args.sample_step,
            min_valid_scenes=args.min_valid_scenes,
            smooth_size=args.smooth_size,
            balance_mode=args.balance_mode,
        )
        print(f"[{product_name}] Processed {len(outputs)} tiles")
        print(f"[{product_name}] Global stretch: {stretch[0]:.3f} - {stretch[1]:.3f}")

        if args.no_mosaic:
            continue

        mosaic_path = mosaic_dir / f"{args.project_name}_{product_name}_mosaic.tif"
        mosaic_wms_rgb(
            outputs,
            mosaic_path,
            band_descriptions=PRODUCTS[product_name]["descriptions"],
        )
        print(f"[{product_name}] Mosaic written to: {mosaic_path}")


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
