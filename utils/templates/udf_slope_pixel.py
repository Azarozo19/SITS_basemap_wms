import numpy as np
from scipy.stats import linregress
from datetime import date, timedelta

# Global start date
start = date.fromisoformat('1970-01-01')


def forcepy_init(dates, sensors, bandnames):
    """
    Initialize output label for the slope raster.
    Returns:
        List with a single element: ["slope"].
    """
    return ["slope"]


def forcepy_pixel(inarray, outarray, dates, sensors, bandnames, nodata, nproc):
    """
    Compute the slope of the time series per pixel using DSWI index.

    inarray:   numpy.ndarray[nDates, nBands, nrows, ncols](Int16)
    outarray:  numpy.ndarray[nOutBands](Int16) - Initialized with no data values
    dates:     numpy.ndarray[nDates](int) - Days since epoch (1970-01-01)
    sensors:   numpy.ndarray[nDates](str) - Sensor names
    bandnames: numpy.ndarray[nBands](str) - Band names
    nodata:    int - No data value
    nproc:     int - Number of allowed processes/threads
    """

    inarray = inarray.astype(np.float32)  # Work with float32 for calculations
    inarray = inarray[:, :, 0, 0]  # Extract pixel time series
    invalid = inarray == nodata  # Identify no-data values

    valid = np.where(inarray[:, 0] != nodata)[0]  # Select valid time points

    if len(valid) == 0:
        return  # No valid data

    inarray[invalid] = np.nan  # Set no-data to NaN

    # Extract band indices
    green = np.argwhere(bandnames == b'GREEN')[0][0]
    red = np.argwhere(bandnames == b'RED')[0][0]
    nir = np.argwhere(bandnames == b'NIR')[0][0]
    swir1 = np.argwhere(bandnames == b'SWIR1')[0][0]

    # Get valid values
    vals = inarray[valid, :]

    # Compute DSWI = (NIR + GREEN) / (SWIR1 + RED)
    dswi = (vals[:, nir] + vals[:, green]) / (vals[:, swir1] + vals[:, red])

    # Convert dates to numerical format (days since epoch)
    time_values = np.array([dates[i] for i in valid], dtype=np.float32)

    # Perform linear regression (time vs DSWI)
    if len(time_values) > 1:  # Ensure at least 2 points for fitting
        slope, intercept, r_value, p_value, std_err = linregress(time_values, dswi)
        scaled_slope = int(slope * 100)  # Scale for int16 storage
        outarray[0] = np.int16(scaled_slope)  # Store as int16
    else:
        outarray[0] = nodata  # Not enough data to compute slope
