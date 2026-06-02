import numpy as np
from datetime import date
import time

def forcepy_init(dates, sensors, bandnames):
    """
    dates:     numpy.ndarray[nDates](int) days since epoch (1970-01-01)
    sensors:   numpy.ndarray[nDates](str)
    bandnames: numpy.ndarray[nBands](str)
    """

    out_bands = ['dswi_2018','dswi_2019','dswi_2020','dswi_2021','dswi_2022','dswi_2023']
    #out_bands = ['fw_baseline']
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
    # get month-indices
    #base_dates = []
    fw18_dates = []
    fw19_dates = []
    fw20_dates = []
    fw21_dates = []
    fw22_dates = []
    fw23_dates = []


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

    start = date.fromisoformat('1970-01-01')
    for year in np.arange(start=2019, stop=2020):
        jun_start = date.fromisoformat(str(year) + '-06-01') - start
        aug_end = date.fromisoformat(str(year) + '-08-31') - start
        fw19_dates.append(np.arange(start=jun_start.days, stop=aug_end.days))
    fw19_idx = np.argwhere(np.isin(dates, np.array(fw19_dates).flatten())).flatten()

    start = date.fromisoformat('1970-01-01')
    for year in np.arange(start=2020, stop=2021):
        jun_start = date.fromisoformat(str(year) + '-06-01') - start
        aug_end = date.fromisoformat(str(year) + '-08-31') - start
        fw20_dates.append(np.arange(start=jun_start.days, stop=aug_end.days))
    fw20_idx = np.argwhere(np.isin(dates, np.array(fw20_dates).flatten())).flatten()

    start = date.fromisoformat('1970-01-01')
    for year in np.arange(start=2021, stop=2022):
        jun_start = date.fromisoformat(str(year) + '-06-01') - start
        aug_end = date.fromisoformat(str(year) + '-08-31') - start
        fw21_dates.append(np.arange(start=jun_start.days, stop=aug_end.days))
    fw21_idx = np.argwhere(np.isin(dates, np.array(fw21_dates).flatten())).flatten()

    start = date.fromisoformat('1970-01-01')
    for year in np.arange(start=2022, stop=2023):
        jun_start = date.fromisoformat(str(year) + '-06-01') - start
        aug_end = date.fromisoformat(str(year) + '-08-31') - start
        fw22_dates.append(np.arange(start=jun_start.days, stop=aug_end.days))
    fw22_idx = np.argwhere(np.isin(dates, np.array(fw22_dates).flatten())).flatten()

    start = date.fromisoformat('1970-01-01')
    for year in np.arange(start=2023, stop=2024):
        jun_start = date.fromisoformat(str(year) + '-06-01') - start
        aug_end = date.fromisoformat(str(year) + '-08-31') - start
        fw23_dates.append(np.arange(start=jun_start.days, stop=aug_end.days))
    fw23_idx = np.argwhere(np.isin(dates, np.array(fw23_dates).flatten())).flatten()

    #base = inarray[base_idx]
    fw18 = inarray[fw18_idx]
    fw19 = inarray[fw19_idx]
    fw20 = inarray[fw20_idx]
    fw21 = inarray[fw21_idx]
    fw22 = inarray[fw22_idx]
    fw23 = inarray[fw23_idx]

    # band indices
    green = np.argwhere(bandnames == b'GREEN')[0][0]
    red = np.argwhere(bandnames == b'RED')[0][0]
    nir = np.argwhere(bandnames == b'BROADNIR')[0][0]
    swir1 = np.argwhere(bandnames == b'SWIR1')[0][0]

    # calculate DSWI ((Band 8 (NIR) + Band 3 (Green)) / (Band 11 (SWIR1) + Band 4 (Red)))
    #base_dswi = np.sum(base[:, [green, nir]], axis=1) / np.sum(base[:, [red, swir1]], axis=1)
    fw18_dswi = np.sum(fw18[:, [green, nir]], axis=1) / np.sum(fw18[:, [red, swir1]], axis=1)
    fw19_dswi = np.sum(fw19[:, [green, nir]], axis=1) / np.sum(fw19[:, [red, swir1]], axis=1)
    fw20_dswi = np.sum(fw20[:, [green, nir]], axis=1) / np.sum(fw20[:, [red, swir1]], axis=1)
    fw21_dswi = np.sum(fw21[:, [green, nir]], axis=1) / np.sum(fw21[:, [red, swir1]], axis=1)
    fw22_dswi = np.sum(fw22[:, [green, nir]], axis=1) / np.sum(fw22[:, [red, swir1]], axis=1)
    fw23_dswi = np.sum(fw23[:, [green, nir]], axis=1) / np.sum(fw23[:, [red, swir1]], axis=1)


    # store results
    #base_array = np.round(np.nanmedian(base_dswi, axis=0) * 1000)
    #outarray[0] = np.round(np.nanmedian(base_dswi, axis=0) * 1000)
    outarray[0] = np.round(np.nanmedian(fw18_dswi, axis=0) * 1000)
    outarray[1] = np.round(np.nanmedian(fw19_dswi, axis=0) * 1000)
    outarray[2] = np.round(np.nanmedian(fw20_dswi, axis=0) * 1000)
    outarray[3] = np.round(np.nanmedian(fw21_dswi, axis=0) * 1000)
    outarray[4] = np.round(np.nanmedian(fw22_dswi, axis=0) * 1000)
    outarray[5] = np.round(np.nanmedian(fw23_dswi, axis=0) * 1000)

    outarray[0][outarray[0] == 0] = nodata
    outarray[1][outarray[1] == 0] = nodata
    outarray[2][outarray[2] == 0] = nodata
    outarray[3][outarray[3] == 0] = nodata
    outarray[4][outarray[4] == 0] = nodata
    outarray[5][outarray[5] == 0] = nodata
