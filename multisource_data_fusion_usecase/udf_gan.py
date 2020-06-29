# -*- coding: utf-8 -*-
# Uncomment the import only for coding support
from openeo_udf.api.datacube import DataCube
from typing import Dict



def apply_hypercube(cube: DataCube, context: Dict) -> DataCube:

    import xarray
    import numpy
    import itertools
    from xarray.core.dataarray import DataArray
    from tensorflow.keras.layers import Input, concatenate,Conv2D, Dropout, LeakyReLU, Activation, Conv3D
    from tensorflow.keras.layers import BatchNormalization,Conv2DTranspose
    from tensorflow.keras.initializers import RandomNormal
    from tensorflow.keras import Model
    import tensorflow as tf


    # DEFAULTS #########################

    NDVI='ndvi'
    PVid='ndvi'
    B4id='TOC-B04_10M'
    B8id='TOC-B08_10M'
    VHid='VH'
    VVid='VV'
    prediction_model=""

    if context is not None:
        prediction_model=context.get('prediction_model',prediction_model)

    # HELPER FUNCTIONS #########################

    def computeWindowLists(bboxWindow, imageSize, windowsize, stride):
        '''
        bboxWindow: ((xmin,xmax),(ymin,ymax)) or None to use full image
        imageSize: (width,height)
        windowSize: size of blocks to split bboxWindow
        stride: overlaps width neighbours
        
        returns: 2d list of windows, where each window element is in the format ((xmin,xmax),(ymin,ymax))
        '''
        if bboxWindow is None:  bbox=[0,0,imageSize[0],imageSize[1]]
        else: bbox=[bboxWindow[0][0],bboxWindow[1][0],bboxWindow[0][1],bboxWindow[1][1]]
        
        # because sride amount of frame is not filled in the wind with windowsize -> bbox has to be enlarged
        bbox[0]= bbox[0]-stride if bbox[0]-stride>=0 else 0 
        bbox[1]= bbox[1]-stride if bbox[1]-stride>=0 else 0
        bbox[2]= bbox[2]+stride if bbox[2]+stride<=imageSize[0] else imageSize[0]
        bbox[3]= bbox[3]+stride if bbox[3]+stride<=imageSize[1] else imageSize[1]
         
        # We need to check if we're at the end of the master image
        # We have to make sure we have a full subtile
        # so we need to expand such tile and the resulting overlap
        # with previous subtile is not an issue
        windowlist=[]
        for xStart in range(bbox[0], bbox[2], windowsize - 2 * stride):
            
            windowlist.append([])
            
            if xStart + windowsize > bbox[2]:
                xStart = bbox[2] - windowsize
                xEnd = bbox[2]
            else:
                xEnd = xStart + windowsize
    
            for yStart in range(bbox[1], bbox[3], windowsize - 2 * stride):
                if yStart + windowsize > bbox[3]:
                    yStart = bbox[3] - windowsize
                    yEnd = bbox[3]
                else:
                    yEnd = yStart + windowsize
    
                windowlist[len(windowlist)-1].append(((xStart, xEnd), (yStart, yEnd)))
        
                if (yEnd==bbox[3]): break
            if (xEnd==bbox[2]): break
    
        return windowlist


    def build_generator(windowsize=32, tslength=13):
        kernelsize = 4
        stride = 2
        init = RandomNormal(mean=0.0, stddev=0.02)
    
        def conv2d(layer_input, filters, kernelsize, stride, init,
                   batchnormalization=True):
            c = Conv2D(filters, kernel_size=(kernelsize, kernelsize),
                       strides=stride, padding='same', activation=None,
                       kernel_initializer=init)(layer_input)
            if batchnormalization:
                c = BatchNormalization()(c)
            c = LeakyReLU(alpha=0.2)(c)
            return c
    
        def deconv2d(layer_input, s1_skip_input, s2_skip_input,
                     proba_skip_input, filters, kernelsize, stride,
                     init, dropout, batchnormalization=True):
            d = Conv2DTranspose(filters, kernel_size=(kernelsize, kernelsize),
                                strides=stride, padding='same',
                                activation=None,
                                kernel_initializer=init)(layer_input)
            if batchnormalization:
                d = BatchNormalization()(d)
            if dropout:
                d = Dropout(dropout)(d)
            d = concatenate([d, s1_skip_input, s2_skip_input,
                             proba_skip_input])
            d = Activation('relu')(d)
            return d
    
        # Inputs
        s1_input = Input(
            shape=(tslength, windowsize, windowsize, 2),
            name='s1_input')
        s2_input = Input(
            shape=(tslength, windowsize, windowsize, 1),
            name='s2_input')
        proba_input = Input(
            shape=(tslength, windowsize, windowsize, 1),
            name='proba_input')
    
        # ------------------
        # S1 encoder
        #
        # First ConvLSTM2D to handle the temporal dependencies
        # Then a tradiational Conv2D encoder
        # ------------------
        s1_conv3d_1 = LeakyReLU(alpha=0.2)(BatchNormalization()(
            Conv3D(filters=64,
                   kernel_size=(7, 3, 3),
                   strides=(5, 1, 1),
                   padding="same")(s1_input)))
        s1_conv3d_2 = LeakyReLU(alpha=0.2)(BatchNormalization()(
            Conv3D(filters=64,
                   kernel_size=(5, 3, 3),
                   strides=(5, 1, 1),
                   padding="same")(s1_conv3d_1)))
        s1_conv3d_3 = tf.keras.backend.squeeze(
            LeakyReLU(alpha=0.2)(BatchNormalization()(
                Conv3D(filters=64,
                       kernel_size=(7, 3, 3),
                       strides=(2, 1, 1),
                       padding="same")(s1_conv3d_2))), axis=1)
    
        s1_enc1 = conv2d(s1_conv3d_3, 64, kernelsize=kernelsize,
                         stride=stride, init=init, batchnormalization=False)
        s1_enc2 = conv2d(s1_enc1, 128, kernelsize=kernelsize,
                         stride=stride, init=init)
        s1_enc3 = conv2d(s1_enc2, 256, kernelsize=kernelsize,
                         stride=stride, init=init)
        s1_enc4 = conv2d(s1_enc3, 512, kernelsize=kernelsize,
                         stride=stride, init=init)
    
        # Bottleneck, no batch norm and relu instead of leaky
        s1_b = Conv2D(512, (4, 4), strides=(2, 2), padding='same',
                      kernel_initializer=init)(s1_enc4)
        s1_b = Activation('relu')(s1_b)
    
        # ------------------
        # S2 encoder
        #
        # First ConvLSTM2D to handle the temporal dependencies
        # Then a tradiational Conv2D encoder
        # ------------------
    
        s2_conv3d_1 = LeakyReLU(alpha=0.2)(BatchNormalization()(
            Conv3D(filters=64,
                   kernel_size=(7, 1, 1),
                   strides=(5, 1, 1),
                   padding="same")(s2_input)))
        s2_conv3d_2 = LeakyReLU(alpha=0.2)(BatchNormalization()(
            Conv3D(filters=64,
                   kernel_size=(5, 1, 1),
                   strides=(5, 1, 1),
                   padding="same")(s2_conv3d_1)))
        s2_conv3d_3 = tf.keras.backend.squeeze(
            LeakyReLU(alpha=0.2)(BatchNormalization()(
                Conv3D(filters=64,
                       kernel_size=(7, 1, 1),
                       strides=(2, 1, 1),
                       padding="same")(s2_conv3d_2))), axis=1)
    
        s2_enc1 = conv2d(s2_conv3d_3, 64, kernelsize=kernelsize,
                         stride=stride, init=init, batchnormalization=False)
        s2_enc2 = conv2d(s2_enc1, 128, kernelsize=kernelsize,
                         stride=stride, init=init)
        s2_enc3 = conv2d(s2_enc2, 256, kernelsize=kernelsize,
                         stride=stride, init=init)
        s2_enc4 = conv2d(s2_enc3, 512, kernelsize=kernelsize,
                         stride=stride, init=init)
    
        # Bottleneck, no batch norm and relu instead of leaky
        s2_b = Conv2D(512, (4, 4), strides=(2, 2), padding='same',
                      kernel_initializer=init)(s2_enc4)
        s2_b = Activation('relu')(s2_b)
    
        # ------------------
        # PROBA encoder
        #
        # First ConvLSTM2D to handle the temporal dependencies
        # Then a tradiational Conv2D encoder
        # ------------------
    
        proba_conv3d_1 = LeakyReLU(alpha=0.2)(BatchNormalization()(
            Conv3D(filters=64,
                   kernel_size=(7, 1, 1),
                   strides=(5, 1, 1),
                   padding="same")(proba_input)))
        proba_conv3d_2 = LeakyReLU(alpha=0.2)(BatchNormalization()(
            Conv3D(filters=64,
                   kernel_size=(5, 1, 1),
                   strides=(5, 1, 1),
                   padding="same")(proba_conv3d_1)))
        proba_conv3d_3 = tf.keras.backend.squeeze(
            LeakyReLU(alpha=0.2)(BatchNormalization()(
                Conv3D(filters=64,
                       kernel_size=(7, 1, 1),
                       strides=(2, 1, 1),
                       padding="same")(proba_conv3d_2))), axis=1)
    
        proba_enc1 = conv2d(proba_conv3d_3, 64, kernelsize=kernelsize,
                            stride=stride, init=init, batchnormalization=False)
        proba_enc2 = conv2d(proba_enc1, 128, kernelsize=kernelsize,
                            stride=stride, init=init)
        proba_enc3 = conv2d(proba_enc2, 256, kernelsize=kernelsize,
                            stride=stride, init=init)
        proba_enc4 = conv2d(proba_enc3, 512, kernelsize=kernelsize,
                            stride=stride, init=init)
    
        # Bottleneck, no batch norm and relu instead of leaky
        proba_b = Conv2D(512, (4, 4), strides=(2, 2), padding='same',
                         kernel_initializer=init)(proba_enc4)
        proba_b = Activation('relu')(proba_b)
    
        # --------------------------------------
        # Concatenation of the encoded features
        # --------------------------------------
    
        concatenated = concatenate([s1_b, s2_b, proba_b])
    
        # --------------------------------------
        # DECODER
        # --------------------------------------
    
        dec4 = deconv2d(concatenated, s1_enc4, s2_enc4, proba_enc4,
                        512, kernelsize=kernelsize,
                        stride=stride, init=init, dropout=0.5)
        dec3 = deconv2d(dec4, s1_enc3, s2_enc3, proba_enc3,
                        256, kernelsize=kernelsize,
                        stride=stride, init=init, dropout=0)
        dec2 = deconv2d(dec3, s1_enc2, s2_enc2, proba_enc2,
                        128, kernelsize=kernelsize,
                        stride=stride, init=init, dropout=0)
        dec1 = deconv2d(dec2, s1_enc1, s2_enc1, proba_enc1,
                        64, kernelsize=kernelsize,
                        stride=stride, init=init, dropout=0)
    
        # OUTPUT LAYER
        output = Activation('tanh')(Conv2DTranspose(
            1, (4, 4), strides=2, padding='same',
            kernel_initializer=init)(dec1))
    
        # Define the final generator model
        generator = Model(
            inputs=[s1_input, s2_input, proba_input],
            outputs=output,
            name='generator')
    
        return generator
    
    
    def minmaxscaler(data, source):
        ranges = {}
        ranges[NDVI] = [-0.08, 1]
        ranges[VVid] = [-20, -2]
        ranges[VHid] = [-33, -8]
        # Scale between -1 and 1
        datarescaled = 2*(data - ranges[source][0])/(ranges[source][1] - ranges[source][0]) - 1
        return datarescaled


    def minmaxunscaler(data, source):
        ranges = {}
        ranges[NDVI] = [-0.08, 1]
        ranges[VVid] = [-20, -2]
        ranges[VHid] = [-33, -8]
        # Unscale
        dataunscaled = 0.5*(data + 1) * (ranges[source][1] - ranges[source][0]) + ranges[source][0]
        return dataunscaled


    def process_window(window, acquisitiondate, inarr, model, windowsize=128, nodata=0):
    
# SKIPPING THIS BECAUSE RELYING ON PROPERLY SETTING FILTER TEMPORAL IN THE OPENEO PROCESS
# THIS MIGHT THROWS NOT ALL DIMENSIONS FOUND IN t: output_index DATE RANGE IS BIGGER THAN WHAT IS IN INARR
#         # select +-90days interval relative to acquisitiondate
#         output_index = pd.date_range(acquisitiondate - pd.to_timedelta('90D'),
#                                      acquisitiondate + pd.to_timedelta('90D'),
#                                      freq='5D')
#         inarr=inarr.ffill(dim='t').resample(t='1D').ffill().sel({'t': output_index}, method='ffill')
        inarr=inarr.ffill(dim='t').resample(t='1D').ffill()
        
        # grow it to 5 dimensions
        inarr=inarr.expand_dims(dim=['d0','d5'],axis=[0,5])
        
        # select bands
        PV=inarr.sel(bands=PVid)
        B4=inarr.sel(bands=B4id)
        B8=inarr.sel(bands=B8id)
        VH=inarr.sel(bands=VHid)
        VV=inarr.sel(bands=VVid)
     
        # Scale S1
        VV = minmaxscaler(VV, VVid)
        VH = minmaxscaler(VH, VHid)
    
        # Concatenate s1 data
        s1_backscatter = xarray.concat((VV, VH), dim='d5')
    
        # Calculate NDVI
        s2_ndvi = (B8-B4)/(B8+B4)
    
        # Scale NDVI
        s2_ndvi = minmaxscaler(s2_ndvi, NDVI)
        probav_ndvi = minmaxscaler(PV, NDVI)
    
        # Remove any nan values
        s2_ndvi=s2_ndvi.fillna(nodata)
        s1_backscatter=s1_backscatter.fillna(nodata)
        probav_ndvi=probav_ndvi.fillna(nodata)
    
        # Run neural network
        predictions = model.predict((s1_backscatter, s2_ndvi, probav_ndvi))
    
        # Unscale
        predictions = minmaxunscaler(predictions, NDVI)
    
        return predictions.reshape((windowsize, windowsize))

    # MAIN CODE #########################

    # extract xarray
    inarr=cube.get_array()
            
    # rescale
    inarr.loc[{'bands':PVid}]=0.004*inarr.sel(bands=PVid)-0.08
    inarr.loc[{'bands':B4id}]*=0.0001
    inarr.loc[{'bands':B4id}]*=0.0001
    inarr.loc[{'bands':VHid}]=10.*xarray.ufuncs.log10(inarr.sel(bands=VHid))
    inarr.loc[{'bands':VVid}]=10.*xarray.ufuncs.log10(inarr.sel(bands=VVid))
    
#     print('--- FIRST RENORM ------------')
#     for i in inarr.bands.values:
#         iarr=inarr.loc[{'bands':i}]
#         print(str(i)+": "+str(float(iarr.min()))+" "+str(float(iarr.max())))
    
#     print(VHid+": "+str(float(VH.min()))+" "+str(float(VH.max())))
#     print(VVid+": "+str(float(VV.min()))+" "+str(float(VV.max())))
#     print(B4id+": "+str(float(B4.min()))+" "+str(float(B4.max())))
#     print(B8id+": "+str(float(B8.min()))+" "+str(float(B8.max())))
#     print(PVid+": "+str(float(PV.min()))+" "+str(float(PV.max())))
    
    # compute windows
    xsize,ysize=inarr.x.shape[0],inarr.y.shape[0]
    windows=computeWindowLists(((0,xsize),(0,ysize)), (xsize,ysize), 128, 8)
    windowlist=list(itertools.chain(*windows))
    
    # selecting the date in the middle
    middate=inarr.t.values[0]+0.5*(inarr.t.values[-1]-inarr.t.values[0])

    # load the model
    model = build_generator(tslength=37)
    model.load_weights(prediction_model)

    # result buffer
    shape=[1,1,1,1]
    shape[inarr.dims.index('x')]=xsize
    shape[inarr.dims.index('y')]=ysize
    predictions=DataArray(numpy.full(shape,numpy.nan),dims=inarr.dims,coords={'bands':['predictions'],'t':[middate]})
    
    # run processing
    for iwin in windowlist:
        ires = process_window(iwin, middate, inarr.sel({'x':range(iwin[0][0],iwin[0][1]),'y':range(iwin[1][0],iwin[1][1])}), model, 128, 0.)
        predictions.loc[{'x':range(iwin[0][0],iwin[0][1]),'y':range(iwin[1][0],iwin[1][1])}]=ires
            
    # behave transparently
    return DataCube(predictions)
