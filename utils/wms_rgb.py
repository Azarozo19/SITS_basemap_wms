from __future__ import annotations

import json
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import numpy as np
import rasterio
from rasterio.enums import ColorInterp, Resampling
from scipy.ndimage import uniform_filter
from tqdm import tqdm

PRODUCTS = {
    "rgb": {
        "bands": (1, 2, 3),
        "descriptions": ("RED", "GREEN", "BLUE"),
        "suffix": "_wms_rgb.tif",
    },
    "cir": {
        "bands": (4, 1, 2),
        "descriptions": ("NIR", "RED", "GREEN"),
        "suffix": "_wms_cir.tif",
    },
}

REQUIRED_GDAL_TOOLS = ["gdalbuildvrt", "gdal_translate", "gdalwarp"]
DEFAULT_COMPRESSION = "ZSTD"
DEFAULT_ZLEVEL = "9"
DEFAULT_BIGTIFF = "IF_NEEDED"
DEFAULT_BLOCKSIZE = 512
DEFAULT_OVERVIEW_LEVELS = [2, 4, 8, 16, 32, 64]
DEFAULT_OVERVIEW_RESAMPLING = "nearest"


def collect_raw_tifs(root_dir, suffix="_HL_UDF_SEN2L_PYP.tif"):
    root = Path(root_dir)
    return sorted(path for path in root.rglob(f"*{suffix}") if path.is_file())


def ensure_gdal_tools() -> None:
    missing = [tool for tool in REQUIRED_GDAL_TOOLS if shutil.which(tool) is None]
    if missing:
        raise RuntimeError(
            "Missing required GDAL tool(s): "
            + ", ".join(missing)
            + ". Install gdal-bin on the target machine."
        )


def validate_compression_method(compression_method: str) -> str:
    supported = {"DEFLATE", "LZW", "ZSTD", "PACKBITS", "NONE"}
    value = compression_method.upper()
    if value not in supported:
        raise ValueError(
            f"Unsupported compression method '{compression_method}'. "
            f"Supported values: {', '.join(sorted(supported))}."
        )
    return value


def normalize_bigtiff(value: str) -> str:
    supported = {"YES", "NO", "IF_NEEDED", "IF_SAFER"}
    normalized = value.upper()
    if normalized not in supported:
        raise ValueError(
            f"Unsupported BIGTIFF option '{value}'. "
            f"Supported values: {', '.join(sorted(supported))}."
        )
    return normalized


def infer_predictor(dtype: str, compression_method: str) -> str | None:
    if compression_method not in {"DEFLATE", "LZW", "ZSTD"}:
        return None
    if "float" in dtype.lower():
        return "3"
    return "2"


def build_creation_options(
    compression_method: str,
    predictor: str | None,
    zlevel: str,
    bigtiff: str,
    blocksize: int,
) -> list[str]:
    options = [
        "-co",
        f"COMPRESS={compression_method}",
        "-co",
        f"BIGTIFF={bigtiff}",
        "-co",
        "TILED=YES",
        "-co",
        f"BLOCKXSIZE={blocksize}",
        "-co",
        f"BLOCKYSIZE={blocksize}",
    ]
    if predictor is not None:
        options.extend(["-co", f"PREDICTOR={predictor}"])
    if compression_method == "DEFLATE":
        options.extend(["-co", f"ZLEVEL={zlevel}"])
    elif compression_method == "ZSTD":
        options.extend(["-co", f"ZSTD_LEVEL={zlevel}"])
    return options


def run_command(cmd: list[str]) -> None:
    import subprocess

    print("Running command:")
    print(" ".join(cmd))
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {' '.join(cmd)}")


def format_duration(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def write_tile_list(tile_paths: list[Path], temp_dir: Path) -> Path:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", dir=temp_dir, delete=False) as tmp_file:
        for tile_path in tile_paths:
            tmp_file.write(f"{tile_path}\n")
        return Path(tmp_file.name)


def write_report(report_path: Path, payload: dict) -> Path:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_output_path(report_path) as temporary_path:
        temporary_path.write_text(json.dumps(payload, indent=2))
    return report_path


@contextmanager
def atomic_output_path(final_path: Path):
    """Yield a same-directory temporary path and replace the target on success."""
    final_path = Path(final_path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = final_path.with_name(
        f".{final_path.stem}.{uuid4().hex}.building{final_path.suffix}"
    )
    try:
        yield temporary_path
        temporary_path.replace(final_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def make_output_tile_name(tile_path: Path, prefix: str | None = None) -> str:
    name_parts = []
    parent = tile_path.parent
    if parent.name:
        name_parts.append(parent.name)
    grandparent = parent.parent
    if grandparent.name and grandparent.name.startswith("X"):
        name_parts.insert(0, grandparent.name)

    base_name = "_".join(name_parts + [tile_path.name])
    if prefix:
        sanitized = prefix.replace("/", "_").replace(" ", "_")
        return f"{sanitized}_{base_name}"
    return base_name


def derive_nodata_value(src, fallback_nodata: int | float | None = None) -> int | float:
    src_nodata = src.nodata
    if src_nodata is not None:
        return src_nodata

    if fallback_nodata is not None:
        return fallback_nodata

    src_dtype = src.dtypes[0]
    if src_dtype in ["int8", "byte"]:
        return -128
    if src_dtype == "uint8":
        return 255
    if src_dtype == "int16":
        return -32768
    if src_dtype == "uint16":
        return 65535
    if src_dtype == "int32":
        return -2147483648
    if src_dtype == "uint32":
        return 4294967295
    if src_dtype in ["float32", "float64"]:
        return -9999.0

    raise ValueError(f"Unsupported dtype for nodata derivation: {src_dtype}")


def build_overviews_inplace(
    raster_path: Path,
    levels: list[int] | None = None,
    resampling: str = DEFAULT_OVERVIEW_RESAMPLING,
) -> None:
    if levels is None:
        levels = DEFAULT_OVERVIEW_LEVELS

    with rasterio.open(raster_path, "r+") as src:
        if src.overviews(1):
            return
        src.build_overviews(levels, Resampling[resampling])
        src.update_tags(ns="rio_overview", resampling=resampling)


def prepare_aoi_for_gdal(aoi_path: Path, target_crs, temp_dir: Path):
    import geopandas as gpd

    aoi = gpd.read_file(aoi_path)
    if aoi.empty:
        raise ValueError(f"AOI file contains no features: {aoi_path}")
    if target_crs and aoi.crs != target_crs:
        aoi = aoi.to_crs(target_crs)

    if hasattr(aoi.geometry, "make_valid"):
        aoi["geometry"] = aoi.geometry.make_valid()
    else:
        aoi["geometry"] = aoi.buffer(0)

    aoi = aoi[~aoi.geometry.is_empty & aoi.geometry.notnull()].copy()
    if aoi.empty:
        raise ValueError(f"AOI file has no valid geometries after repair: {aoi_path}")

    union_geom = aoi.union_all() if hasattr(aoi, "union_all") else aoi.unary_union
    return union_geom, aoi.crs


def write_geometry_cutline(geometry, crs, temp_dir: Path) -> Path:
    import geopandas as gpd
    import os

    cutline = gpd.GeoDataFrame({"id": [1]}, geometry=[geometry], crs=crs)
    fd, prepared_path = tempfile.mkstemp(suffix=".gpkg", dir=temp_dir)
    prepared_cutline_path = Path(prepared_path)
    prepared_cutline_path.unlink(missing_ok=True)
    try:
        os.close(fd)
    except OSError:
        pass
    cutline.to_file(prepared_cutline_path, driver="GPKG")
    return prepared_cutline_path


def clip_raw_tile(
    tile_path: Path,
    output_tile_path: Path,
    aoi_union,
    aoi_crs,
    temp_dir: Path,
    num_threads: str,
    cachemax_mb: int,
    overwrite_tiles: bool,
    compression_method: str,
    predictor: str | None,
    zlevel: str,
    bigtiff: str,
    blocksize: int,
    fallback_nodata: int | float | None = None,
) -> dict:
    from shapely.geometry import box

    if output_tile_path.exists() and not overwrite_tiles:
        return {
            "tile": tile_path.name,
            "source_path": str(tile_path),
            "output_path": str(output_tile_path),
            "status": "reused",
        }

    with rasterio.open(tile_path) as src:
        tile_bounds = box(*src.bounds)
        source_dtype = src.dtypes[0]
        source_nodata = derive_nodata_value(src, fallback_nodata=fallback_nodata)

    tile_cutline_path = None
    if aoi_union is not None:
        if not tile_bounds.intersects(aoi_union):
            return {
                "tile": tile_path.name,
                "source_path": str(tile_path),
                "status": "skipped_non_intersecting",
            }
        # Interior FORCE tiles need recompression but not a cutline warp.
        if not aoi_union.covers(tile_bounds):
            tile_cutline_geom = tile_bounds.intersection(aoi_union)
            if tile_cutline_geom.is_empty:
                return {
                    "tile": tile_path.name,
                    "source_path": str(tile_path),
                    "status": "skipped_empty_intersection",
                }
            tile_cutline_path = write_geometry_cutline(tile_cutline_geom, aoi_crs, temp_dir)

    output_tile_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with atomic_output_path(output_tile_path) as temporary_output_path:
            if tile_cutline_path is not None:
                cmd = [
                    "gdalwarp",
                    "-overwrite",
                    "-cutline",
                    str(tile_cutline_path),
                    "-crop_to_cutline",
                    "-srcnodata",
                    str(source_nodata),
                    "-dstnodata",
                    str(source_nodata),
                    "-multi",
                    "-wo",
                    f"NUM_THREADS={num_threads}",
                    "-wm",
                    str(cachemax_mb),
                    "-ot",
                    source_dtype.upper(),
                ]
                cmd.extend(build_creation_options(compression_method, predictor, zlevel, bigtiff, blocksize))
                cmd.extend([str(tile_path), str(temporary_output_path)])
            else:
                cmd = [
                    "gdal_translate",
                    "-of",
                    "GTiff",
                    "-a_nodata",
                    str(source_nodata),
                    "-ot",
                    source_dtype.upper(),
                ]
                cmd.extend(build_creation_options(compression_method, predictor, zlevel, bigtiff, blocksize))
                cmd.extend([str(tile_path), str(temporary_output_path)])
            run_command(cmd)
    finally:
        if tile_cutline_path is not None and tile_cutline_path.exists():
            tile_cutline_path.unlink()

    return {
        "tile": tile_path.name,
        "source_path": str(tile_path),
        "output_path": str(output_tile_path),
        "status": "written",
    }


def clip_force_raw_tiles(
    raw_tifs: list[Path],
    job_name: str,
    aoi_path: Path | None,
    output_dir: Path,
    num_threads: str = "ALL_CPUS",
    cachemax_mb: int = 512,
    overwrite_tiles: bool = False,
    compression_method: str = DEFAULT_COMPRESSION,
    bigtiff: str = DEFAULT_BIGTIFF,
    zlevel: str = DEFAULT_ZLEVEL,
    blocksize: int = DEFAULT_BLOCKSIZE,
    report_path: Path | None = None,
    fallback_nodata: int | float | None = None,
) -> tuple[list[Path], dict]:
    ensure_gdal_tools()
    if not raw_tifs:
        raise ValueError(f"No raw FORCE TIFFs provided for job '{job_name}'.")

    compression_method = validate_compression_method(compression_method)
    bigtiff = normalize_bigtiff(bigtiff)

    start_total = time.time()
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = output_dir.parent / "_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    aoi_union = None
    aoi_crs = None
    if aoi_path is not None:
        with rasterio.open(raw_tifs[0]) as src:
            aoi_union, aoi_crs = prepare_aoi_for_gdal(aoi_path, src.crs, temp_dir)

    clipped_tile_paths: list[Path] = []
    tile_reports: list[dict] = []
    skipped_tiles = 0
    reused_tiles = 0

    progress = tqdm(raw_tifs, desc=f"Clipping raw tiles for {job_name}", unit="tile", dynamic_ncols=True)
    for tile_path in progress:
        tile_started = time.time()
        with rasterio.open(tile_path) as src:
            predictor = infer_predictor(src.dtypes[0], compression_method)
        output_tile_path = output_dir / make_output_tile_name(tile_path)
        tile_report = clip_raw_tile(
            tile_path=tile_path,
            output_tile_path=output_tile_path,
            aoi_union=aoi_union,
            aoi_crs=aoi_crs,
            temp_dir=temp_dir,
            num_threads=num_threads,
            cachemax_mb=cachemax_mb,
            overwrite_tiles=overwrite_tiles,
            compression_method=compression_method,
            predictor=predictor,
            zlevel=zlevel,
            bigtiff=bigtiff,
            blocksize=blocksize,
            fallback_nodata=fallback_nodata,
        )
        tile_report["runtime_seconds"] = time.time() - tile_started
        tile_reports.append(tile_report)
        status = tile_report["status"]
        progress.set_postfix_str(f"{status}={tile_path.name}")
        if status in {"written", "reused"}:
            clipped_tile_paths.append(output_tile_path)
            if status == "reused":
                reused_tiles += 1
        else:
            skipped_tiles += 1
    progress.close()

    runtime_seconds = time.time() - start_total
    report_payload = {
        "job_name": job_name,
        "aoi_path": str(aoi_path) if aoi_path else None,
        "output_dir": str(output_dir),
        "raw_tile_count": len(raw_tifs),
        "written_or_reused_tiles": len(clipped_tile_paths),
        "reused_tiles": reused_tiles,
        "skipped_tiles": skipped_tiles,
        "runtime_seconds": runtime_seconds,
        "runtime_human": format_duration(runtime_seconds),
        "tiles": tile_reports,
    }
    if report_path is not None:
        write_report(report_path, report_payload)

    if not clipped_tile_paths:
        raise RuntimeError(f"No clipped raw tiles were produced for job '{job_name}'.")

    return clipped_tile_paths, report_payload


def _read_valid_mask(
    src,
    rgb_bands,
    valid_scene_band,
    min_valid_scenes=1,
    rgb_values=None,
    scene_count=None,
):
    if rgb_values is None:
        rgb_values = src.read(rgb_bands)
    if scene_count is None:
        scene_count = src.read(valid_scene_band)

    valid_mask = np.ones(rgb_values.shape[1:], dtype=bool)
    for offset, band_idx in enumerate(rgb_bands):
        values = rgb_values[offset]
        if np.issubdtype(values.dtype, np.floating):
            valid_mask &= np.isfinite(values)
        nodata = src.nodatavals[band_idx - 1]
        if nodata is not None:
            valid_mask &= values != nodata

    scene_nodata = src.nodatavals[valid_scene_band - 1]
    valid_mask &= scene_count >= min_valid_scenes
    if scene_nodata is not None:
        valid_mask &= scene_count != scene_nodata
    return valid_mask


def _sample_valid_values(input_tif, rgb_bands, valid_scene_band, sample_step, min_valid_scenes):
    with rasterio.open(input_tif) as src:
        rgb_values = src.read(rgb_bands)
        scene_count = src.read(valid_scene_band)
        valid_mask = _read_valid_mask(
            src,
            rgb_bands,
            valid_scene_band,
            min_valid_scenes=min_valid_scenes,
            rgb_values=rgb_values,
            scene_count=scene_count,
        )
        sampled_mask = valid_mask[::sample_step, ::sample_step]
        if not np.any(sampled_mask):
            return None

        samples = [arr[::sample_step, ::sample_step][sampled_mask] for arr in rgb_values]

    return np.concatenate(samples)


def _sample_scaled_values(
    input_tif,
    rgb_bands,
    valid_scene_band,
    sample_step,
    min_valid_scenes,
    stretch,
):
    lo, hi = stretch
    with rasterio.open(input_tif) as src:
        rgb_values = src.read(rgb_bands)
        scene_count = src.read(valid_scene_band)
        valid_mask = _read_valid_mask(
            src,
            rgb_bands,
            valid_scene_band,
            min_valid_scenes=min_valid_scenes,
            rgb_values=rgb_values,
            scene_count=scene_count,
        )
        sampled_mask = valid_mask[::sample_step, ::sample_step]
        if not np.any(sampled_mask):
            return None

        samples = []
        for arr in rgb_values:
            scaled = _scale_to_byte(arr, valid_mask, lo, hi)
            samples.append(scaled[::sample_step, ::sample_step][sampled_mask].astype(np.float32))

    return samples


def compute_global_stretch(
    input_tifs,
    rgb_bands=(1, 2, 3),
    valid_scene_band=5,
    low_pct=5.0,
    high_pct=95.0,
    sample_step=32,
    min_valid_scenes=1,
):
    all_samples = []
    for input_tif in input_tifs:
        samples = _sample_valid_values(
            input_tif,
            rgb_bands,
            valid_scene_band,
            sample_step,
            min_valid_scenes,
        )
        if samples is not None and samples.size > 0:
            all_samples.append(samples)

    if not all_samples:
        raise ValueError("No valid RGB samples found across input TIFFs.")

    stacked = np.concatenate(all_samples)
    lo, hi = np.nanpercentile(stacked, [low_pct, high_pct])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        raise ValueError("Invalid global stretch computed from input TIFFs.")

    return float(lo), float(hi)


def _scale_to_byte(values, valid_mask, lo, hi):
    scaled = np.zeros(values.shape, dtype=np.uint8)
    stretched = values.astype(np.float32)
    stretched -= np.float32(lo)
    stretched *= np.float32(255.0 / (hi - lo))
    np.clip(stretched, 0, 255, out=stretched)
    scaled[valid_mask] = np.rint(stretched[valid_mask]).astype(np.uint8)
    return scaled


def _compute_tile_stats(samples_by_band):
    stats = []
    for samples in samples_by_band:
        if samples is None or samples.size == 0:
            stats.append((None, None))
            continue
        stats.append((float(np.nanmean(samples)), float(np.nanstd(samples))))
    return stats


def compute_balance_targets(
    input_tifs,
    rgb_bands=(1, 2, 3),
    valid_scene_band=5,
    sample_step=64,
    min_valid_scenes=1,
    stretch=None,
):
    if stretch is None:
        raise ValueError("stretch is required to compute balance targets.")

    per_band_samples = [[] for _ in rgb_bands]
    for input_tif in input_tifs:
        samples = _sample_scaled_values(
            input_tif,
            rgb_bands,
            valid_scene_band,
            sample_step,
            min_valid_scenes,
            stretch,
        )
        if samples is None:
            continue
        for idx, band_samples in enumerate(samples):
            if band_samples.size > 0:
                per_band_samples[idx].append(band_samples)

    targets = []
    for band_samples in per_band_samples:
        if not band_samples:
            targets.append((None, None))
            continue
        stacked = np.concatenate(band_samples)
        targets.append((float(np.nanmean(stacked)), float(np.nanstd(stacked))))
    return targets


def _balance_byte_band(values, valid_mask, target_stats, tile_stats):
    target_mean, target_std = target_stats
    tile_mean, tile_std = tile_stats
    if (
        target_mean is None
        or target_std is None
        or tile_mean is None
        or tile_std is None
        or tile_std <= 0
    ):
        return values

    balanced = values.astype(np.float32)
    balanced[valid_mask] = ((balanced[valid_mask] - tile_mean) / tile_std) * target_std + target_mean
    balanced = np.clip(np.rint(balanced), 0, 255).astype(np.uint8)
    return balanced


def _smooth_byte_band(values, valid_mask, size):
    if size is None or size <= 1:
        return values

    float_values = values.astype(np.float32)
    weights = valid_mask.astype(np.float32)

    summed = uniform_filter(float_values * weights, size=size, mode="nearest")
    counts = uniform_filter(weights, size=size, mode="nearest")

    smoothed = values.copy()
    valid_out = counts > 0
    smoothed_values = np.divide(
        summed,
        counts,
        out=np.zeros_like(summed),
        where=valid_out,
    )
    smoothed[valid_mask] = np.rint(smoothed_values[valid_mask]).astype(np.uint8)
    return smoothed


def adjust_rgb_tone(
    scaled_rgb,
    valid_mask,
    gamma=1.0,
    saturation=1.0,
    channel_gains=(1.0, 1.0, 1.0),
    neutral_protection=0.0,
    green_suppression=0.0,
    green_dominance_threshold=0.2,
):
    """Apply display tone and RGB balance while keeping invalid pixels black."""
    if gamma <= 0:
        raise ValueError("gamma must be positive")
    if saturation < 0:
        raise ValueError("saturation must be non-negative")
    if len(channel_gains) != 3 or any(gain <= 0 for gain in channel_gains):
        raise ValueError("channel_gains must contain three positive values")
    if neutral_protection < 0:
        raise ValueError("neutral_protection must be non-negative")
    if not 0 <= green_suppression < 1:
        raise ValueError("green_suppression must be at least 0 and less than 1")
    if green_dominance_threshold <= 0:
        raise ValueError("green_dominance_threshold must be positive")
    if len(scaled_rgb) != 3:
        raise ValueError("RGB tone adjustment requires exactly three bands")
    if (
        gamma == 1.0
        and saturation == 1.0
        and all(gain == 1.0 for gain in channel_gains)
        and green_suppression == 0.0
    ):
        return scaled_rgb

    rgb = np.stack(scaled_rgb).astype(np.float32)
    rgb *= np.float32(1.0 / 255.0)
    if gamma != 1.0:
        np.power(rgb, np.float32(1.0 / gamma), out=rgb)

    if saturation != 1.0:
        luminance = (
            np.float32(0.2126) * rgb[0]
            + np.float32(0.7152) * rgb[1]
            + np.float32(0.0722) * rgb[2]
        )
        rgb = luminance[np.newaxis] + np.float32(saturation) * (
            rgb - luminance[np.newaxis]
        )

    gains = np.asarray(channel_gains, dtype=np.float32)[:, np.newaxis, np.newaxis]
    if neutral_protection > 0 and not np.all(gains == 1.0):
        peak = np.max(rgb, axis=0)
        chroma = np.max(rgb, axis=0) - np.min(rgb, axis=0)
        relative_chroma = np.divide(
            chroma,
            peak,
            out=np.zeros_like(chroma),
            where=peak > 0,
        )
        gain_strength = np.clip(relative_chroma / neutral_protection, 0.0, 1.0)
        gains = 1.0 + gain_strength[np.newaxis] * (gains - 1.0)
    rgb *= gains

    if green_suppression > 0:
        strongest_other = np.maximum(rgb[0], rgb[2])
        green_dominance = np.maximum(rgb[1] - strongest_other, 0.0)
        relative_dominance = np.divide(
            green_dominance,
            rgb[1],
            out=np.zeros_like(green_dominance),
            where=rgb[1] > 0,
        )
        suppression_strength = np.clip(
            relative_dominance / green_dominance_threshold,
            0.0,
            1.0,
        )
        rgb[1] *= 1.0 - green_suppression * suppression_strength

    np.clip(rgb, 0.0, 1.0, out=rgb)
    adjusted = []
    for band in rgb:
        output = np.zeros(valid_mask.shape, dtype=np.uint8)
        output[valid_mask] = np.rint(band[valid_mask] * 255.0).astype(np.uint8)
        adjusted.append(output)
    return adjusted


def export_wms_rgb(
    input_tif,
    output_tif=None,
    rgb_bands=(1, 2, 3),
    valid_scene_band=5,
    stretch=None,
    min_valid_scenes=1,
    add_alpha=True,
    band_descriptions=("RED", "GREEN", "BLUE"),
    smooth_size=None,
    balance_targets=None,
    gamma=1.0,
    saturation=1.0,
    channel_gains=(1.0, 1.0, 1.0),
    neutral_protection=0.0,
    green_suppression=0.0,
    green_dominance_threshold=0.2,
    overwrite=False,
    compression_method=DEFAULT_COMPRESSION,
    bigtiff=DEFAULT_BIGTIFF,
    zlevel=DEFAULT_ZLEVEL,
    blocksize=DEFAULT_BLOCKSIZE,
):
    input_path = Path(input_tif)
    if output_tif is None:
        output_tif = input_path.with_name(f"{input_path.stem}_wms_rgb.tif")
    output_path = Path(output_tif)

    if output_path.exists() and not overwrite:
        return str(output_path)

    compression_method = validate_compression_method(compression_method)
    bigtiff = normalize_bigtiff(bigtiff)

    if stretch is None:
        stretch = compute_global_stretch([input_tif], rgb_bands=rgb_bands, valid_scene_band=valid_scene_band)
    lo, hi = stretch

    with atomic_output_path(output_path) as temporary_output_path, rasterio.open(input_tif) as src:
        rgb_values = src.read(rgb_bands)
        valid_scenes = src.read(valid_scene_band)
        valid_mask = _read_valid_mask(
            src,
            rgb_bands,
            valid_scene_band,
            min_valid_scenes=min_valid_scenes,
            rgb_values=rgb_values,
            scene_count=valid_scenes,
        )
        if not np.any(valid_mask):
            raise ValueError(f"No valid RGB pixels found for WMS export: {input_tif}")

        scaled_rgb = []
        for arr in rgb_values:
            scaled = _scale_to_byte(arr, valid_mask, lo, hi)
            scaled_rgb.append(scaled)
        if balance_targets is not None:
            tile_stats = _compute_tile_stats([scaled[valid_mask] for scaled in scaled_rgb])
            scaled_rgb = [
                _balance_byte_band(arr, valid_mask, target_stats, band_stats)
                for arr, target_stats, band_stats in zip(scaled_rgb, balance_targets, tile_stats)
            ]
        scaled_rgb = [_smooth_byte_band(arr, valid_mask, smooth_size) for arr in scaled_rgb]
        scaled_rgb = adjust_rgb_tone(
            scaled_rgb,
            valid_mask,
            gamma=gamma,
            saturation=saturation,
            channel_gains=channel_gains,
            neutral_protection=neutral_protection,
            green_suppression=green_suppression,
            green_dominance_threshold=green_dominance_threshold,
        )
        valid_scene_nodata = src.nodatavals[valid_scene_band - 1]

        profile = src.profile.copy()
        profile.update(
            driver="GTiff",
            dtype="uint8",
            count=5 if add_alpha else 4,
            nodata=None,
            tiled=True,
            compress=compression_method.lower(),
            bigtiff=bigtiff,
            blockxsize=blocksize,
            blockysize=blocksize,
        )
        if compression_method in {"DEFLATE", "LZW", "ZSTD"}:
            profile["predictor"] = 2
        if compression_method == "DEFLATE":
            profile["zlevel"] = int(zlevel)
        elif compression_method == "ZSTD":
            profile["zstd_level"] = int(zlevel)

        with rasterio.open(temporary_output_path, "w", **profile) as dst:
            for idx, arr in enumerate(scaled_rgb, start=1):
                dst.write(arr, idx)
            colorinterp = [ColorInterp.red, ColorInterp.green, ColorInterp.blue]
            dst.set_band_description(1, band_descriptions[0])
            dst.set_band_description(2, band_descriptions[1])
            dst.set_band_description(3, band_descriptions[2])
            valid_scenes_out = np.zeros(valid_scenes.shape, dtype=np.uint8)
            valid_scene_mask = valid_mask.copy()
            if valid_scene_nodata is not None:
                valid_scene_mask &= valid_scenes != valid_scene_nodata
            valid_scenes_out[valid_scene_mask] = np.clip(
                np.rint(valid_scenes[valid_scene_mask]), 0, 255
            ).astype(np.uint8)
            if add_alpha:
                alpha = np.zeros(valid_mask.shape, dtype=np.uint8)
                alpha[valid_mask] = 255
                dst.write(alpha, 4)
                dst.set_band_description(4, "ALPHA")
                dst.write(valid_scenes_out, 5)
                dst.set_band_description(5, "VALID_SCENES")
                colorinterp.extend((ColorInterp.alpha, ColorInterp.undefined))
            else:
                dst.write(valid_scenes_out, 4)
                dst.set_band_description(4, "VALID_SCENES")
                colorinterp.append(ColorInterp.undefined)
            dst.colorinterp = tuple(colorinterp)

    return str(output_path)


def build_vrt(tile_paths: list[Path], vrt_output_path: Path, temp_dir: Path) -> Path:
    tile_list_path = write_tile_list(tile_paths, temp_dir)
    try:
        with atomic_output_path(vrt_output_path) as temporary_output_path:
            run_command(
                [
                    "gdalbuildvrt",
                    "-input_file_list",
                    str(tile_list_path),
                    str(temporary_output_path),
                ]
            )
        return vrt_output_path
    finally:
        if tile_list_path.exists():
            tile_list_path.unlink()


def build_final_raster(
    vrt_path: Path,
    final_output_path: Path,
    build_overviews: bool,
    overview_resampling: str,
    compression_method: str,
    zlevel: str,
    bigtiff: str,
    blocksize: int,
    band_descriptions: tuple[str, ...],
    add_alpha: bool,
) -> Path:
    final_output_path.parent.mkdir(parents=True, exist_ok=True)
    predictor = infer_predictor("uint8", compression_method)
    cmd = ["gdal_translate", "-of", "GTiff"]
    cmd.extend(build_creation_options(compression_method, predictor, zlevel, bigtiff, blocksize))
    with atomic_output_path(final_output_path) as temporary_output_path:
        cmd.extend([str(vrt_path), str(temporary_output_path)])
        run_command(cmd)

        with rasterio.open(temporary_output_path, "r+") as dst:
            dst.set_band_description(1, band_descriptions[0])
            dst.set_band_description(2, band_descriptions[1])
            dst.set_band_description(3, band_descriptions[2])
            if add_alpha:
                if dst.count >= 4:
                    dst.set_band_description(4, "ALPHA")
                if dst.count >= 5:
                    dst.set_band_description(5, "VALID_SCENES")
                if dst.count >= 5:
                    dst.colorinterp = (
                        ColorInterp.red,
                        ColorInterp.green,
                        ColorInterp.blue,
                        ColorInterp.alpha,
                        ColorInterp.undefined,
                    )
            else:
                if dst.count >= 4:
                    dst.set_band_description(4, "VALID_SCENES")
                if dst.count >= 4:
                    dst.colorinterp = (
                        ColorInterp.red,
                        ColorInterp.green,
                        ColorInterp.blue,
                        ColorInterp.undefined,
                    )

        if build_overviews:
            build_overviews_inplace(temporary_output_path, resampling=overview_resampling)
    return final_output_path


def render_product_tiles(
    input_tifs,
    product_name,
    output_dir: Path,
    vrt_output_path: Path | None = None,
    final_output_path: Path | None = None,
    report_path: Path | None = None,
    valid_scene_band=5,
    low_pct=5.0,
    high_pct=95.0,
    sample_step=32,
    min_valid_scenes=1,
    add_alpha=True,
    smooth_size=None,
    balance_mode=None,
    gamma=1.0,
    saturation=1.0,
    channel_gains=(1.0, 1.0, 1.0),
    neutral_protection=0.0,
    green_suppression=0.0,
    green_dominance_threshold=0.2,
    overwrite_tiles=False,
    build_overviews=True,
    build_tile_overviews=False,
    overview_resampling=DEFAULT_OVERVIEW_RESAMPLING,
    skip_vrt=False,
    skip_final_raster=False,
    compression_method=DEFAULT_COMPRESSION,
    bigtiff=DEFAULT_BIGTIFF,
    zlevel=DEFAULT_ZLEVEL,
    blocksize=DEFAULT_BLOCKSIZE,
):
    ensure_gdal_tools()
    compression_method = validate_compression_method(compression_method)
    bigtiff = normalize_bigtiff(bigtiff)

    if product_name not in PRODUCTS:
        raise ValueError(f"Unsupported product '{product_name}'. Supported products: {sorted(PRODUCTS)}")
    if not input_tifs:
        raise ValueError(f"No clipped raw TIFFs provided for product '{product_name}'.")
    if skip_vrt and not skip_final_raster:
        raise RuntimeError("A final raster requires a VRT. Remove skip_vrt or add skip_final_raster.")

    product = PRODUCTS[product_name]
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = output_dir.parent / "_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    start_total = time.time()

    stretch_stage_start = time.time()
    stretch = compute_global_stretch(
        input_tifs,
        rgb_bands=product["bands"],
        valid_scene_band=valid_scene_band,
        low_pct=low_pct,
        high_pct=high_pct,
        sample_step=sample_step,
        min_valid_scenes=min_valid_scenes,
    )
    stretch_stage_elapsed = time.time() - stretch_stage_start
    balance_targets = None
    balance_stage_elapsed = None
    if balance_mode == "mean_std":
        balance_stage_start = time.time()
        balance_targets = compute_balance_targets(
            input_tifs,
            rgb_bands=product["bands"],
            valid_scene_band=valid_scene_band,
            sample_step=sample_step,
            min_valid_scenes=min_valid_scenes,
            stretch=stretch,
        )
        balance_stage_elapsed = time.time() - balance_stage_start

    outputs: list[Path] = []
    reused_tiles = 0
    tile_reports: list[dict] = []
    tile_stage_start = time.time()
    progress = tqdm(input_tifs, desc=f"Rendering {product_name}", unit="tile", dynamic_ncols=True)
    for input_tif in progress:
        tile_started = time.time()
        input_path = Path(input_tif)
        output_tif = output_dir / f"{input_path.stem}{product['suffix']}"
        existed_before = output_tif.exists()
        output_path = Path(
            export_wms_rgb(
                input_tif,
                output_tif=output_tif,
                rgb_bands=product["bands"],
                valid_scene_band=valid_scene_band,
                stretch=stretch,
                min_valid_scenes=min_valid_scenes,
                add_alpha=add_alpha,
                band_descriptions=product["descriptions"],
                smooth_size=smooth_size,
                balance_targets=balance_targets,
                gamma=gamma,
                saturation=saturation,
                channel_gains=channel_gains,
                neutral_protection=neutral_protection,
                green_suppression=green_suppression,
                green_dominance_threshold=green_dominance_threshold,
                overwrite=overwrite_tiles,
                compression_method=compression_method,
                bigtiff=bigtiff,
                zlevel=zlevel,
                blocksize=blocksize,
            )
        )
        if existed_before and not overwrite_tiles:
            reused_tiles += 1
            status = "reused"
        else:
            status = "written"
            if build_overviews and (skip_final_raster or build_tile_overviews):
                build_overviews_inplace(output_path, resampling=overview_resampling)
        outputs.append(output_path)
        progress.set_postfix_str(f"{status}={input_path.name}")
        tile_reports.append(
            {
                "source_path": str(input_path),
                "output_path": str(output_path),
                "status": status,
                "runtime_seconds": time.time() - tile_started,
            }
        )
    progress.close()
    tile_stage_elapsed = time.time() - tile_stage_start

    final_vrt = None
    final_raster = None
    vrt_stage_elapsed = None
    final_stage_elapsed = None

    if not skip_vrt:
        if vrt_output_path is None:
            vrt_output_path = output_dir.parent / f"{product_name}_mosaic.vrt"
        vrt_stage_start = time.time()
        final_vrt = build_vrt(outputs, vrt_output_path, temp_dir)
        vrt_stage_elapsed = time.time() - vrt_stage_start

        if not skip_final_raster:
            if final_output_path is None:
                final_output_path = output_dir.parent / f"{product_name}_mosaic.tif"
            final_stage_start = time.time()
            final_raster = build_final_raster(
                final_vrt,
                final_output_path,
                build_overviews=build_overviews,
                overview_resampling=overview_resampling,
                compression_method=compression_method,
                zlevel=zlevel,
                bigtiff=bigtiff,
                blocksize=blocksize,
                band_descriptions=product["descriptions"],
                add_alpha=add_alpha,
            )
            final_stage_elapsed = time.time() - final_stage_start

    runtime_seconds = time.time() - start_total
    report_payload = {
        "product_name": product_name,
        "tile_output_dir": str(output_dir),
        "vrt_output": str(final_vrt) if final_vrt else None,
        "final_raster_output": str(final_raster) if final_raster else None,
        "tile_count": len(outputs),
        "reused_tiles": reused_tiles,
        "stretch": {"low": stretch[0], "high": stretch[1]},
        "low_pct": low_pct,
        "high_pct": high_pct,
        "sample_step": sample_step,
        "min_valid_scenes": min_valid_scenes,
        "gamma": gamma,
        "saturation": saturation,
        "channel_gains": list(channel_gains),
        "neutral_protection": neutral_protection,
        "green_suppression": green_suppression,
        "green_dominance_threshold": green_dominance_threshold,
        "compression_method": compression_method,
        "bigtiff": bigtiff,
        "zlevel": str(zlevel),
        "blocksize": blocksize,
        "overview_resampling": overview_resampling,
        "runtime_seconds": runtime_seconds,
        "runtime_human": format_duration(runtime_seconds),
        "stage_timings": {
            "stretch_seconds": stretch_stage_elapsed,
            "stretch_human": format_duration(stretch_stage_elapsed),
            "balance_seconds": balance_stage_elapsed,
            "balance_human": format_duration(balance_stage_elapsed) if balance_stage_elapsed is not None else None,
            "tile_render_seconds": tile_stage_elapsed,
            "tile_render_human": format_duration(tile_stage_elapsed),
            "vrt_seconds": vrt_stage_elapsed,
            "vrt_human": format_duration(vrt_stage_elapsed) if vrt_stage_elapsed is not None else None,
            "final_raster_seconds": final_stage_elapsed,
            "final_raster_human": format_duration(final_stage_elapsed) if final_stage_elapsed is not None else None,
        },
        "tiles": tile_reports,
    }
    if report_path is not None:
        write_report(report_path, report_payload)

    return {
        "tile_outputs": [str(path) for path in outputs],
        "stretch": stretch,
        "vrt_output": str(final_vrt) if final_vrt else None,
        "final_raster_output": str(final_raster) if final_raster else None,
        "report": report_payload,
    }
