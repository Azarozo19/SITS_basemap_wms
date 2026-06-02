import numpy as np
import warnings


def forcepy_init(dates, sensors, bandnames):
    """
    dates:     numpy.ndarray[nDates](int) days since epoch (1970-01-01)
    sensors:   numpy.ndarray[nDates](str)
    bandnames: numpy.ndarray[nBands](str)
    """

    return ["RED_RAW", "GREEN_RAW", "BLUE_RAW", "NIR_RAW", "VALID_SCENES"]


def _write_rgb_nir_medoid(inarray, outarray, bandnames, nodata):
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
    valid_scenes = np.all(np.isfinite(rgbn), axis=1)
    valid_scene_count = np.sum(valid_scenes, axis=0)
    valid_pixels = valid_scene_count > 0

    outarray[0][:] = nodata
    outarray[1][:] = nodata
    outarray[2][:] = nodata
    outarray[3][:] = nodata
    outarray[4][:] = nodata

    if not np.any(valid_pixels):
        return

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="All-NaN slice encountered")
        median_vector = np.nanmedian(rgbn, axis=0)

    distances = np.sum((rgbn - median_vector[np.newaxis, :, :, :]) ** 2, axis=1)
    distances[~valid_scenes] = np.inf
    medoid_idx = np.argmin(distances, axis=0)

    rows = np.arange(rgbn.shape[2])[:, np.newaxis]
    cols = np.arange(rgbn.shape[3])[np.newaxis, :]

    red_medoid = rgbn[:, 0, :, :][medoid_idx, rows, cols]
    green_medoid = rgbn[:, 1, :, :][medoid_idx, rows, cols]
    blue_medoid = rgbn[:, 2, :, :][medoid_idx, rows, cols]
    nir_medoid = rgbn[:, 3, :, :][medoid_idx, rows, cols]

    outarray[0][valid_pixels] = np.rint(red_medoid[valid_pixels])
    outarray[1][valid_pixels] = np.rint(green_medoid[valid_pixels])
    outarray[2][valid_pixels] = np.rint(blue_medoid[valid_pixels])
    outarray[3][valid_pixels] = np.rint(nir_medoid[valid_pixels])
    outarray[4][valid_pixels] = valid_scene_count[valid_pixels]


def forcepy_chunk(inarray, outarray, dates, sensors, bandnames, nodata, nproc):
    _write_rgb_nir_medoid(inarray, outarray, bandnames, nodata)


def forcepy_block(inarray, outarray, dates, sensors, bandnames, nodata, nproc):
    _write_rgb_nir_medoid(inarray, outarray, bandnames, nodata)
