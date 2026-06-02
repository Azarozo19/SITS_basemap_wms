import numpy as np
from datetime import date


def forcepy_init(dates, sensors, bandnames):
    """
    dates:     numpy.ndarray[nDates](int) days since epoch (1970-01-01)
    sensors:   numpy.ndarray[nDates](str)
    bandnames: numpy.ndarray[nBands](str)
    """

    return ['march_april', 'june_july_august', 'september_october']



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
    mar_apr_dates = []
    jun_jul_aug_dates = []
    sep_okt_dates = []

    start = date.fromisoformat('1970-01-01')
    for year in np.arange(start=2000, stop=2018):
        mar_start = date.fromisoformat(str(year) + '-03-01') - start
        apr_end = date.fromisoformat(str(year) + '-04-30') - start

        jun_start = date.fromisoformat(str(year) + '-06-01') - start
        aug_end = date.fromisoformat(str(year) + '-08-31') - start

        sep_start = date.fromisoformat(str(year) + '-09-01') - start
        okt_end = date.fromisoformat(str(year) + '-10-31') - start

        mar_apr_dates.append(np.arange(start=mar_start.days, stop=apr_end.days))
        jun_jul_aug_dates.append(np.arange(start=jun_start.days, stop=aug_end.days))
        sep_okt_dates.append(np.arange(start=sep_start.days, stop=okt_end.days))

    mar_apr_idx = np.argwhere(np.isin(dates, np.array(mar_apr_dates).flatten())).flatten()
    jun_jul_aug_idx = np.argwhere(np.isin(dates, np.array(jun_jul_aug_dates).flatten())).flatten()
    sep_okt_idx = np.argwhere(np.isin(dates, np.array(sep_okt_dates).flatten())).flatten()

    # get month-vitalitat_3cities_data
    mar_apr_data = inarray[mar_apr_idx]
    jun_jul_aug_data = inarray[jun_jul_aug_idx]
    sep_okt_data = inarray[sep_okt_idx]

    # band indices
    green = np.argwhere(bandnames == b'GREEN')[0][0]
    red = np.argwhere(bandnames == b'RED')[0][0]
    nir = np.argwhere(bandnames == b'NIR')[0][0]
    swir1 = np.argwhere(bandnames == b'SWIR1')[0][0]

    # calculate DSWI ((Band 8 (NIR) + Band 3 (Green)) / (Band 11 (SWIR1) + Band 4 (Red)))
    mar_apr_dswi = np.sum(mar_apr_data[:, [green, nir]], axis=1) / np.sum(mar_apr_data[:, [red, swir1]], axis=1)
    jun_jul_aug_dswi = np.sum(jun_jul_aug_data[:, [green, nir]], axis=1) / np.sum(jun_jul_aug_data[:, [red, swir1]], axis=1)
    sep_okt_dswi = np.sum(sep_okt_data[:, [green, nir]], axis=1) / np.sum(sep_okt_data[:, [red, swir1]], axis=1)

    # store results
    outarray[0] = np.round(np.nanmedian(mar_apr_dswi, axis=0) * 1000)
    outarray[1] = np.round(np.nanmedian(jun_jul_aug_dswi, axis=0) * 1000)
    outarray[2] = np.round(np.nanmedian(sep_okt_dswi, axis=0) * 1000)
