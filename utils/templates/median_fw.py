import numpy as np
from datetime import date


def forcepy_init(dates, sensors, bandnames):
    """
    dates:     numpy.ndarray[nDates](int) days since epoch (1970-01-01)
    sensors:   numpy.ndarray[nDates](str)
    bandnames: numpy.ndarray[nBands](str)
    """

    #out_bands = ['fw_2018','fw_2019','fw_2020','fw_2021','fw_2022']
    out_bands = ['dswi_2017']
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

    if np.all(invalid):
        return
    inarray[invalid] = np.nan        # ... and inject NaN to enable np.nan*-functions

    # get month-indices
    #base_dates = []
    fw18_dates = []
    fw19_dates = []
    fw20_dates = []
    fw21_dates = []
    fw22_dates = []


    # start = date.fromisoformat('1970-01-01')
    # for year in np.arange(start=2000, stop=2018):
    #     jun_start = date.fromisoformat(str(year) + '-06-01') - start
    #     aug_end = date.fromisoformat(str(year) + '-08-31') - start
    #     base_dates.append(np.arange(start=jun_start.days, stop=aug_end.days))
    # base_idx = np.argwhere(np.isin(dates, np.array(base_dates).flatten())).flatten()

    start = date.fromisoformat('1970-01-01')
    for year in np.arange(start=2018, stop=2019):
        jun_start = date.fromisoformat(str(year) + '-06-01') - start
        aug_end = date.fromisoformat(str(year) + '-08-31') - start
        fw18_dates.append(np.arange(start=jun_start.days, stop=aug_end.days))
    fw18_idx = np.argwhere(np.isin(dates, np.array(fw18_dates).flatten())).flatten()



    #base = inarray[base_idx]
    fw18 = inarray[fw18_idx]


    # band indices
    green = np.argwhere(bandnames == b'GREEN')[0][0]
    red = np.argwhere(bandnames == b'RED')[0][0]
    nir = np.argwhere(bandnames == b'NIR')[0][0]
    swir1 = np.argwhere(bandnames == b'SWIR1')[0][0]

    # calculate DSWI ((Band 8 (NIR) + Band 3 (Green)) / (Band 11 (SWIR1) + Band 4 (Red)))
    #base_dswi = np.sum(base[:, [green, nir]], axis=1) / np.sum(base[:, [red, swir1]], axis=1)
    fw18_dswi = np.sum(fw18[:, [green, nir]], axis=1) / np.sum(fw18[:, [red, swir1]], axis=1)
    fw19_dswi = np.sum(fw19[:, [green, nir]], axis=1) / np.sum(fw19[:, [red, swir1]], axis=1)
    fw20_dswi = np.sum(fw20[:, [green, nir]], axis=1) / np.sum(fw20[:, [red, swir1]], axis=1)
    fw21_dswi = np.sum(fw21[:, [green, nir]], axis=1) / np.sum(fw21[:, [red, swir1]], axis=1)
    fw22_dswi = np.sum(fw22[:, [green, nir]], axis=1) / np.sum(fw22[:, [red, swir1]], axis=1)


    # store results
    #base_array = np.round(np.nanmedian(base_dswi, axis=0) * 1000)
    #outarray[0] = np.round(np.nanmedian(base_dswi, axis=0) * 1000)
    outarray[0] = np.round(np.nanmedian(fw18_dswi, axis=0) * 1000)
    outarray[1] = np.round(np.nanmedian(fw19_dswi, axis=0) * 1000)
    outarray[2] = np.round(np.nanmedian(fw20_dswi, axis=0) * 1000)
    outarray[3] = np.round(np.nanmedian(fw21_dswi, axis=0) * 1000)
    outarray[4] = np.round(np.nanmedian(fw22_dswi, axis=0) * 1000)

    outarray[0][outarray[0]==0]=nodata
    outarray[1][outarray[1]==0]=nodata
    outarray[2][outarray[2]==0]=nodata
    outarray[3][outarray[3]==0]=nodata
    outarray[4][outarray[4]==0]=nodata
