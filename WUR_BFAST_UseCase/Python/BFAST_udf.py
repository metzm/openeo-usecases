from bfast import BFASTMonitor
from bfast.utils import crop_data_dates
import xarray as xr
import pandas as pd
import numpy as np
from openeo_udf.api.datacube import DataCube

def apply_datacube(udf_cube: DataCube,context:dict) -> DataCube:
    """
    Apply the BFASTmonitor method to detect a break at the end of time-series of the datacube.
    This UDF reduce the time dimension of the input datacube. 
    :param udf_cube: the openEO virtual DataCube object 
    :return DataCube(breaks_xr):
    """
    from datetime import datetime
    # convert the openEO datacube into the xarray DataArray structure
    my_xarray: xr.DataArray = udf_cube.get_array()
    #select single band, removes band dimension
    my_xarray = my_xarray.sel(bands='VV')
    #
    start_hist = datetime(2017, 5, 1)
    start_monitor = datetime(2019, 1, 1)
    end_monitor = datetime(2019, 12, 29)
    # get the dates from the data cube:
    dates = [pd.Timestamp(date).to_pydatetime() for date in my_xarray.coords['t'].values]
    # pre-processing - crop the input data cube according to the history and monitor periods:
    data, dates = crop_data_dates(my_xarray.values, dates, start_hist, end_monitor)
    # !!! Note !!! that data has the shape 91, and not 92 for our dataset. The reason is the definition in
    # the bfast utils.py script where the start_hist is set < than dates, and not <= than dates.
    # -------------------------------------
    # specify the BFASTmonitor parameters:
    model = BFASTMonitor(
        start_monitor,
        freq=31,
        k=3,
        verbose=1,
        hfrac=0.25,
        trend=True,
        level=0.05,
        backend='python'
    )
    # run the monitoring:
    # model.fit(data, dates, nan_value=udf_data.nodatavals[0])
    model.fit(data, dates)
    # get the detected breaks as an xarray Data Array:
    breaks_xr = xr.DataArray(model.breaks,
                             coords=[my_xarray.coords['x'].values, my_xarray.coords['y'].values],
                             dims=['x', 'y'])
    # return the breaks as openEO DataCube:
    return DataCube(breaks_xr)



