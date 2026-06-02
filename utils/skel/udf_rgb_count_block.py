import numpy as np
import warnings

def forcepy_init(dates, sensors, bandnames):
    """
    dates:     numpy.ndarray[nDates](int) days since epoch (1970-01-01)
    sensors:   numpy.ndarray[nDates](str)
    bandnames: numpy.ndarray[nBands](str)
    """

    return ["RED_RAW", "GREEN_RAW", "BLUE_RAW", "VALID_SCENES", "VALID_MASK"]


def _write_rgb_count(inarray, outarray, bandnames, nodata):
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

    rgb = inarray[:, [red, green, blue], :, :]
    valid_scenes = np.all(np.isfinite(rgb), axis=1)
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
        red_median = np.nanmedian(rgb[:, 0, :, :], axis=0)
        green_median = np.nanmedian(rgb[:, 1, :, :], axis=0)
        blue_median = np.nanmedian(rgb[:, 2, :, :], axis=0)

    outarray[0][valid_pixels] = np.rint(red_median[valid_pixels])
    outarray[1][valid_pixels] = np.rint(green_median[valid_pixels])
    outarray[2][valid_pixels] = np.rint(blue_median[valid_pixels])
    outarray[3][valid_pixels] = valid_scene_count[valid_pixels]
    outarray[4][valid_pixels] = 1


def forcepy_chunk(inarray, outarray, dates, sensors, bandnames, nodata, nproc):
    """
    inarray:   numpy.ndarray[nDates, nBands, nrows, ncols](Int16)
    outarray:  numpy.ndarray[nOutBands, nrows, ncols](Int16)
    dates:     numpy.ndarray[nDates](int) days since epoch (1970-01-01)
    sensors:   numpy.ndarray[nDates](str)
    bandnames: numpy.ndarray[nBands](str)
    nodata:    int
    nproc:     number of allowed processes/threads
    """
    _write_rgb_count(inarray, outarray, bandnames, nodata)


def forcepy_block(inarray, outarray, dates, sensors, bandnames, nodata, nproc):
    _write_rgb_count(inarray, outarray, bandnames, nodata)
