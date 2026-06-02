from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import ColorInterp
from rasterio.merge import merge
from scipy.ndimage import uniform_filter

PRODUCTS = {
    "rgb": {
        "bands": (1, 2, 3),
        "descriptions": ("RED_RGB", "GREEN_RGB", "BLUE_RGB"),
        "suffix": "_wms_rgb.tif",
    },
    "cir": {
        "bands": (4, 1, 2),
        "descriptions": ("NIR_CIR", "RED_CIR", "GREEN_CIR"),
        "suffix": "_wms_cir.tif",
    },
}
def _read_valid_mask(src, rgb_bands, valid_scene_band, min_valid_scenes=1):
    rgb_valid = []
    for band_idx in rgb_bands:
        arr = src.read(band_idx).astype(np.float32)
        nodata = src.nodatavals[band_idx - 1]
        valid = np.isfinite(arr)
        if nodata is not None:
            valid &= arr != nodata
        rgb_valid.append(valid)

    scene_count = src.read(valid_scene_band)
    scene_nodata = src.nodatavals[valid_scene_band - 1]
    valid_mask = scene_count >= min_valid_scenes
    if scene_nodata is not None:
        valid_mask &= scene_count != scene_nodata

    for valid in rgb_valid:
        valid_mask &= valid

    return valid_mask


def _sample_valid_values(input_tif, rgb_bands, valid_scene_band, sample_step, min_valid_scenes):
    with rasterio.open(input_tif) as src:
        valid_mask = _read_valid_mask(
            src,
            rgb_bands,
            valid_scene_band,
            min_valid_scenes=min_valid_scenes,
        )
        sampled_mask = valid_mask[::sample_step, ::sample_step]
        if not np.any(sampled_mask):
            return None

        samples = []
        for band_idx in rgb_bands:
            arr = src.read(band_idx).astype(np.float32)
            samples.append(arr[::sample_step, ::sample_step][sampled_mask])

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
        valid_mask = _read_valid_mask(
            src,
            rgb_bands,
            valid_scene_band,
            min_valid_scenes=min_valid_scenes,
        )
        sampled_mask = valid_mask[::sample_step, ::sample_step]
        if not np.any(sampled_mask):
            return None

        samples = []
        for band_idx in rgb_bands:
            arr = src.read(band_idx).astype(np.float32)
            scaled = _scale_to_byte(arr, valid_mask, lo, hi)
            samples.append(scaled[::sample_step, ::sample_step][sampled_mask].astype(np.float32))

    return samples


def collect_raw_tifs(root_dir, suffix="_HL_UDF_SEN2L_PYP.tif"):
    root = Path(root_dir)
    return sorted(path for path in root.rglob(f"*{suffix}") if path.is_file())


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
    stretched = (values - lo) / (hi - lo)
    stretched = np.clip(stretched * 255.0, 0, 255)
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


def export_wms_rgb(
    input_tif,
    output_tif=None,
    rgb_bands=(1, 2, 3),
    valid_scene_band=5,
    stretch=None,
    min_valid_scenes=1,
    add_alpha=True,
    band_descriptions=("RED_WMS", "GREEN_WMS", "BLUE_WMS"),
    smooth_size=None,
    balance_targets=None,
):
    input_path = Path(input_tif)
    if output_tif is None:
        output_tif = input_path.with_name(f"{input_path.stem}_wms_rgb.tif")
    output_path = Path(output_tif)

    if stretch is None:
        stretch = compute_global_stretch([input_tif], rgb_bands=rgb_bands, valid_scene_band=valid_scene_band)
    lo, hi = stretch

    with rasterio.open(input_tif) as src:
        valid_mask = _read_valid_mask(
            src,
            rgb_bands,
            valid_scene_band,
            min_valid_scenes=min_valid_scenes,
        )
        if not np.any(valid_mask):
            raise ValueError(f"No valid RGB pixels found for WMS export: {input_tif}")

        scaled_rgb = []
        tile_samples = []
        for band_idx in rgb_bands:
            arr = src.read(band_idx).astype(np.float32)
            scaled = _scale_to_byte(arr, valid_mask, lo, hi)
            tile_samples.append(scaled[valid_mask].astype(np.float32))
            scaled_rgb.append(scaled)
        tile_stats = _compute_tile_stats(tile_samples)
        if balance_targets is not None:
            scaled_rgb = [
                _balance_byte_band(arr, valid_mask, target_stats, band_stats)
                for arr, target_stats, band_stats in zip(scaled_rgb, balance_targets, tile_stats)
            ]
        scaled_rgb = [_smooth_byte_band(arr, valid_mask, smooth_size) for arr in scaled_rgb]
        valid_scenes = src.read(valid_scene_band)
        valid_scene_nodata = src.nodatavals[valid_scene_band - 1]

        profile = src.profile.copy()
        profile.update(
            driver="GTiff",
            dtype="uint8",
            count=5 if add_alpha else 4,
            nodata=None,
            tiled=True,
            compress="deflate",
            predictor=2,
        )

        with rasterio.open(output_path, "w", **profile) as dst:
            for idx, arr in enumerate(scaled_rgb, start=1):
                dst.write(arr, idx)
            colorinterp = [ColorInterp.red, ColorInterp.green, ColorInterp.blue]
            dst.set_band_description(1, band_descriptions[0])
            dst.set_band_description(2, band_descriptions[1])
            dst.set_band_description(3, band_descriptions[2])
            valid_scenes_out = np.zeros(valid_scenes.shape, dtype=np.uint8)
            valid_scene_values = valid_scenes.astype(np.float32)
            valid_scene_mask = valid_mask.copy()
            if valid_scene_nodata is not None:
                valid_scene_mask &= valid_scene_values != valid_scene_nodata
            valid_scenes_out[valid_scene_mask] = np.clip(
                np.rint(valid_scene_values[valid_scene_mask]), 0, 255
            ).astype(np.uint8)
            if add_alpha:
                alpha = np.where(valid_mask, 255, 0).astype(np.uint8)
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


def batch_export_wms_rgb(
    input_tifs,
    output_suffix="_wms_rgb.tif",
    rgb_bands=(1, 2, 3),
    valid_scene_band=5,
    low_pct=2.0,
    high_pct=98.0,
    sample_step=32,
    min_valid_scenes=1,
    add_alpha=True,
    band_descriptions=("RED_WMS", "GREEN_WMS", "BLUE_WMS"),
    smooth_size=None,
    balance_mode=None,
):
    stretch = compute_global_stretch(
        input_tifs,
        rgb_bands=rgb_bands,
        valid_scene_band=valid_scene_band,
        low_pct=low_pct,
        high_pct=high_pct,
        sample_step=sample_step,
        min_valid_scenes=min_valid_scenes,
    )
    balance_targets = None
    if balance_mode == "mean_std":
        balance_targets = compute_balance_targets(
            input_tifs,
            rgb_bands=rgb_bands,
            valid_scene_band=valid_scene_band,
            sample_step=sample_step,
            min_valid_scenes=min_valid_scenes,
            stretch=stretch,
        )

    outputs = []
    for input_tif in input_tifs:
        input_path = Path(input_tif)
        output_tif = input_path.with_name(f"{input_path.stem}{output_suffix}")
        outputs.append(
            export_wms_rgb(
                input_tif,
                output_tif=output_tif,
                rgb_bands=rgb_bands,
                valid_scene_band=valid_scene_band,
                stretch=stretch,
                min_valid_scenes=min_valid_scenes,
                add_alpha=add_alpha,
                band_descriptions=band_descriptions,
                smooth_size=smooth_size,
                balance_targets=balance_targets,
            )
        )

    return outputs, stretch


def batch_export_product(
    input_tifs,
    product_name,
    valid_scene_band=5,
    low_pct=5.0,
    high_pct=95.0,
    sample_step=32,
    min_valid_scenes=1,
    add_alpha=True,
    smooth_size=None,
    balance_mode=None,
):
    product = PRODUCTS[product_name]
    return batch_export_wms_rgb(
        input_tifs,
        output_suffix=product["suffix"],
        rgb_bands=product["bands"],
        valid_scene_band=valid_scene_band,
        low_pct=low_pct,
        high_pct=high_pct,
        sample_step=sample_step,
        min_valid_scenes=min_valid_scenes,
        add_alpha=add_alpha,
        band_descriptions=product["descriptions"],
        smooth_size=smooth_size,
        balance_mode=balance_mode,
    )


def mosaic_wms_rgb(
    input_tifs,
    output_tif,
    band_descriptions=("RED_WMS", "GREEN_WMS", "BLUE_WMS"),
):
    if not input_tifs:
        raise ValueError("No input WMS RGB TIFFs provided for mosaic.")

    datasets = [rasterio.open(path) for path in input_tifs]
    try:
        mosaic, transform = merge(datasets)
        profile = datasets[0].profile.copy()
        profile.update(
            driver="GTiff",
            height=mosaic.shape[1],
            width=mosaic.shape[2],
            transform=transform,
            count=mosaic.shape[0],
            dtype=str(mosaic.dtype),
            nodata=None,
            tiled=True,
            compress="deflate",
            predictor=2,
        )

        with rasterio.open(output_tif, "w", **profile) as dst:
            dst.write(mosaic)
            if mosaic.shape[0] == 5:
                dst.colorinterp = (
                    ColorInterp.red,
                    ColorInterp.green,
                    ColorInterp.blue,
                    ColorInterp.alpha,
                    ColorInterp.undefined,
                )
                dst.set_band_description(4, "ALPHA")
                dst.set_band_description(5, "VALID_SCENES")
            elif mosaic.shape[0] == 4:
                dst.colorinterp = (
                    ColorInterp.red,
                    ColorInterp.green,
                    ColorInterp.blue,
                    ColorInterp.undefined,
                )
            else:
                dst.colorinterp = (
                    ColorInterp.red,
                    ColorInterp.green,
                    ColorInterp.blue,
                )
            dst.set_band_description(1, band_descriptions[0])
            dst.set_band_description(2, band_descriptions[1])
            dst.set_band_description(3, band_descriptions[2])
    finally:
        for ds in datasets:
            ds.close()

    return str(output_tif)
