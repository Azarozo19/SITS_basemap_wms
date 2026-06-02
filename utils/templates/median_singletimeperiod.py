import numpy as np
from datetime import date

def forcepy_init(dates, sensors, bandnames):
    """
    dates:     numpy.ndarray[nDates](int) days since epoch (1970-01-01)
    sensors:   numpy.ndarray[nDates](str)
    bandnames: numpy.ndarray[nBands](str)
    """

    #out_bands = ['fw_2018','fw_2019','fw_2020','fw_2021','fw_2022']
    out_bands = ['fw_baseline']
    return out_bands



def forcepy_block(inarray, outarray, dates, sensors, bandnames, nodata, nproc):
    """
    inarray:   numpy.ndarray[nDates, nBands, nrows, ncols](Int16)
    outarray:  numpy.ndarray[nOutBands](Int16) initialized with no vitalitat_3cities_data values
    dates:     numpy.ndarray[nDates](int) days since epoch (1970-01-01)
    sensors:   numpy.ndarray[nDates](str)
    bandnames: numpy.ndarray[nBands](str)
    nodata:    int
    nproc:     number of allowed processes/threads
    Write results into outarray.
    """
    # prepare vitalitat_3cities_data
    inarray = inarray.astype(np.float32)  # cast to float ...
    invalid = inarray == nodata
    invalid_masks = inarray == 0

    if np.all(invalid):
        return
    inarray[invalid] = np.nan        # ... and inject NaN to enable np.nan*-functions
    inarray[invalid_masks] = np.nan

    #print(dates)
    green = np.argwhere(bandnames == b'GREEN')[0][0]
    red = np.argwhere(bandnames == b'RED')[0][0]
    nir = np.argwhere(bandnames == b'BROADNIR')[0][0]
    #nir = np.argwhere(bandnames == b'NIR')[0][0]
    swir1 = np.argwhere(bandnames == b'SWIR1')[0][0]
    # calculate DSWI ((Band 8 (NIR) + Band 3 (Green)) / (Band 11 (SWIR1) + Band 4 (Red)))
    base_dswi = np.divide(np.sum(inarray[:, [green, nir]], axis=1),np.sum(inarray[:, [red, swir1]], axis=1))

    # store results
    #base_array = np.round(np.nanmedian(base_dswi, axis=0) * 1000)
    outarray[0] = np.round(np.nanmedian(base_dswi, axis=0) * 1000)
    outarray[0][outarray[0] == 0] = nodata