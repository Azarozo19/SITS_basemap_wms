import numpy as np


PERCENTILE = 25
LEAST_CLOUDY_FRACTION = 0.5
MIN_SCENES = 1
_RGBN_INDICES = None


def forcepy_init(dates, sensors, bandnames):
    """
    dates:     numpy.ndarray[nDates](int) days since epoch (1970-01-01)
    sensors:   numpy.ndarray[nDates](str)
    bandnames: numpy.ndarray[nBands](str)
    """

    global _RGBN_INDICES
    _RGBN_INDICES = _resolve_band_indices(bandnames)
    return ["RED_P25", "GREEN_P25", "BLUE_P25", "NIR_P25", "VALID_SCENES"]


def _resolve_band_indices(bandnames):
    lookup = {name: index for index, name in enumerate(bandnames)}
    required = (b"RED", b"GREEN", b"BLUE", b"BROADNIR")
    missing = [name.decode("ascii") for name in required if name not in lookup]
    if missing:
        raise ValueError(f"Missing required input band(s): {', '.join(missing)}")
    return tuple(lookup[name] for name in required)


def _select_least_cloudy_scenes(valid_scenes):
    # Use the fraction of valid pixels in the current block as a scene-level
    # cloudiness proxy: more valid pixels means less cloud/shadow masking.
    valid_fraction = valid_scenes.reshape(valid_scenes.shape[0], -1).mean(axis=1)

    ranked_idx = np.argsort(-valid_fraction, kind="stable")
    ranked_idx = ranked_idx[valid_fraction[ranked_idx] > 0]
    if ranked_idx.size == 0:
        return ranked_idx

    keep_count = max(MIN_SCENES, int(np.ceil(ranked_idx.size * LEAST_CLOUDY_FRACTION)))
    return ranked_idx[:keep_count]


def _nanpercentile_linear_inplace(values, percentile):
    """Compute a temporal NaN percentile using an in-place partial partition."""
    finite_counts = np.sum(np.isfinite(values), axis=0)
    positions = (finite_counts - 1) * (percentile / 100.0)
    positions = np.maximum(positions, 0)
    lower = np.floor(positions).astype(np.intp)
    upper = np.ceil(positions).astype(np.intp)

    # Only the order statistics up to the requested percentile are needed.
    max_upper = int(np.max(upper))
    values.partition(np.arange(max_upper + 1), axis=0)
    lower_values = np.take_along_axis(values, lower[np.newaxis], axis=0)[0]
    upper_values = np.take_along_axis(values, upper[np.newaxis], axis=0)[0]
    fraction = positions - lower
    return lower_values + (upper_values - lower_values) * fraction


def _write_rgb_p25(inarray, outarray, bandnames, nodata):
    global _RGBN_INDICES
    if _RGBN_INDICES is None:
        _RGBN_INDICES = _resolve_band_indices(bandnames)

    # Allocate only the four bands needed by this product. FORCE provides all
    # input bands as Int16, so casting the complete input cube is unnecessary.
    shape = (inarray.shape[0], 4, inarray.shape[2], inarray.shape[3])
    rgbn = np.empty(shape, dtype=np.float32)
    for output_index, input_index in enumerate(_RGBN_INDICES):
        rgbn[:, output_index, :, :] = inarray[:, input_index, :, :]

    invalid = rgbn == nodata
    if np.all(invalid):
        return
    rgbn[invalid] = np.nan
    del invalid
    zero_mask = rgbn == 0
    rgbn[zero_mask] = np.nan
    del zero_mask

    valid_scenes = np.all(np.isfinite(rgbn), axis=1)
    selected_idx = _select_least_cloudy_scenes(valid_scenes)

    outarray[...] = nodata

    if selected_idx.size == 0:
        return

    selected_rgbn = rgbn[selected_idx]
    selected_valid_scenes = valid_scenes[selected_idx]
    valid_scene_count = np.sum(selected_valid_scenes, axis=0)
    valid_pixels = valid_scene_count > 0

    if not np.any(valid_pixels):
        return

    percentiles = _nanpercentile_linear_inplace(selected_rgbn, PERCENTILE)

    outarray[:4, valid_pixels] = np.rint(percentiles[:, valid_pixels])
    outarray[4][valid_pixels] = valid_scene_count[valid_pixels]


def forcepy_chunk(inarray, outarray, dates, sensors, bandnames, nodata, nproc):
    _write_rgb_p25(inarray, outarray, bandnames, nodata)


def forcepy_block(inarray, outarray, dates, sensors, bandnames, nodata, nproc):
    _write_rgb_p25(inarray, outarray, bandnames, nodata)
