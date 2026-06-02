import numpy as np
import warnings


PERCENTILE = 25
LEAST_CLOUDY_FRACTION = 0.5
MIN_SCENES = 1


def forcepy_init(dates, sensors, bandnames):
    """
    dates:     numpy.ndarray[nDates](int) days since epoch (1970-01-01)
    sensors:   numpy.ndarray[nDates](str)
    bandnames: numpy.ndarray[nBands](str)
    """

    return ["RED_P25", "GREEN_P25", "BLUE_P25", "NIR_P25", "VALID_SCENES"]


def _select_least_cloudy_scenes(rgbn):
    # Use the fraction of valid pixels in the current block as a scene-level
    # cloudiness proxy: more valid pixels means less cloud/shadow masking.
    valid_scenes = np.all(np.isfinite(rgbn), axis=1)
    valid_fraction = valid_scenes.reshape(valid_scenes.shape[0], -1).mean(axis=1)

    ranked_idx = np.argsort(-valid_fraction, kind="stable")
    ranked_idx = ranked_idx[valid_fraction[ranked_idx] > 0]
    if ranked_idx.size == 0:
        return ranked_idx

    keep_count = max(MIN_SCENES, int(np.ceil(ranked_idx.size * LEAST_CLOUDY_FRACTION)))
    return ranked_idx[:keep_count]


def _write_rgb_p25(inarray, outarray, bandnames, nodata):
    inarray = inarray.astype(np.float32)
    invalid = inarray == nodata
    invalid_masks = inarray == 0
    if np.all(invalid):
        return

    inarray[invalid] = np.nan
    inarray[invalid_masks] = np.nan

    red = np.argwhere(bandnames == b"RED")[0][0]
    green = np.argwhere(bandnames == b"GREEN")[0][0]
    blue = np.argwhere(bandnames == b"BLUE")[0][0]
    nir = np.argwhere(bandnames == b"BROADNIR")[0][0]

    rgbn = inarray[:, [red, green, blue, nir], :, :]
    selected_idx = _select_least_cloudy_scenes(rgbn)

    outarray[0][:] = nodata
    outarray[1][:] = nodata
    outarray[2][:] = nodata
    outarray[3][:] = nodata
    outarray[4][:] = nodata

    if selected_idx.size == 0:
        return

    selected_rgbn = rgbn[selected_idx]
    valid_scenes = np.all(np.isfinite(selected_rgbn), axis=1)
    valid_scene_count = np.sum(valid_scenes, axis=0)
    valid_pixels = valid_scene_count > 0

    if not np.any(valid_pixels):
        return

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="All-NaN slice encountered")
        red_p25 = np.nanpercentile(selected_rgbn[:, 0, :, :], PERCENTILE, axis=0)
        green_p25 = np.nanpercentile(selected_rgbn[:, 1, :, :], PERCENTILE, axis=0)
        blue_p25 = np.nanpercentile(selected_rgbn[:, 2, :, :], PERCENTILE, axis=0)
        nir_p25 = np.nanpercentile(selected_rgbn[:, 3, :, :], PERCENTILE, axis=0)

    outarray[0][valid_pixels] = np.rint(red_p25[valid_pixels])
    outarray[1][valid_pixels] = np.rint(green_p25[valid_pixels])
    outarray[2][valid_pixels] = np.rint(blue_p25[valid_pixels])
    outarray[3][valid_pixels] = np.rint(nir_p25[valid_pixels])
    outarray[4][valid_pixels] = valid_scene_count[valid_pixels]


def forcepy_chunk(inarray, outarray, dates, sensors, bandnames, nodata, nproc):
    _write_rgb_p25(inarray, outarray, bandnames, nodata)


def forcepy_block(inarray, outarray, dates, sensors, bandnames, nodata, nproc):
    _write_rgb_p25(inarray, outarray, bandnames, nodata)
