import os, sys, pathlib, time, re, glob, math, copy
import warnings
def warning_on_one_line(message, category, filename, lineno, file=None, line=None):
    return '%s:%s: %s: %s\n' % (filename, lineno, category.__name__, message)
warnings.formatwarning = warning_on_one_line
warnings.filterwarnings("ignore", category=DeprecationWarning)
import numpy as np
import pandas as pd
import cv2
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.ticker import FuncFormatter 
import pickle
from tqdm import tqdm
from sklearn.model_selection import train_test_split
from sklearn.manifold import TSNE
import tensorflow as tf
#tensorboard --logdir=... --host localhost --port 6006
#browse: http://localhost:6006/
from tensorflow.keras.activations import *
import tensorflow.keras.backend as K
#https://github.com/keras-team/keras/issues/3945
#https://stackoverflow.com/questions/64118331
K.image_data_format();
#https://towardsdatascience.com/bayesian-hyper-parameter-optimization-neural-networks-
#  tensorflow-facies-prediction-example-f9c48d21f795
from skopt import gp_minimize
from skopt.callbacks import CheckpointSaver
from skopt import load as load_gp_minimize
from skopt.space import Integer, Real, Categorical

from _GlobalFuncs import *
OPTITER, OPTACCU, OPTASTD = 0, 0, 0
################################################################################################
DATA_LOC     = "./catDogData/full"
TESTDATA_LOC = "./catDogData/zTest/"
FIG_LOC      = "./catDogFig/"
IMAGE_SIZE = (128, 128)         #(224, 224) for ResNet
RAND_SEED  = 1
SAVE_GM_MINIMIZE_CHECKPOINT = True
SAVE_BOOTSTRAP_CHECKPOINT   = True
SAVE_EPOCH_CHECKPOINT       = True
def main():
    verbosity = 3

    modelName = "catDogConv2D.model"
    trainOn   = True                #False to test the currently saved model
    printPredFigN = 10
    #dataset 
    testRatio       = 0.1
    dropRatio       = 0.0           #ratio of data to simulate unlabeled Y's
    validationRatio = 0.1           #ratio of data for validation
    batchSize       = 32            #32 is good according to the ref

    #trainings
    trainAutoencoderOn = False
    autoEpochN         = 30
   
    optModelSearchOn  = True
    optimizationCoreN = -1      #-1 to use all CPU cores
    optimizationCallN = 50      #note: increase to a difference >= 10 when reloading
    learningEpochN    = 8       #note: equilibrium needed if # of MC dropout layer varies
    bootstrappingN    = 5
    
    retrainOptModelOn = True
    learningEpochNOpt = 30
    bootstrappingNOpt = 1

    #model setup
    learningRate = Real(   low=1E-6, high=1E-1, prior="log-uniform",     name="learningRate")
    convLayerN   = Integer(low=1,    high=3,                             name="convLayerN")
    convFilterN  = Categorical(categories=[32, 64, 128],                 name="convFilterN")
    denseLayerN  = Integer(low=1,    high=5,                             name="denseLayerN")
    denseNeuronN = Integer(low=10,   high=500,                           name="denseNeuronN")
    actFunc  = Categorical(categories=["relu", "elu", "selu"],           name="actFunc")
    initFunc = Categorical(categories=["he_normal", "he_uniform"],       name="initFunc")

    dims, par0 = [], []
    if "SimpleDense" in modelName:
        optModelSearchOn = False
        bootstrappingNOpt = 1
        denseNeuronN2 = Integer(low=10,   high=500,                      name="denseNeuronN2")
        dims = [learningRate, denseNeuronN, denseNeuronN2]
        par0 = [1E-2,         300,          100]
    elif "Dense" in modelName:
        bootstrappingN    = 1
        bootstrappingNOpt = 1
        actFunc = Categorical(categories=["relu", "sigmoid"],            name="actFunc")
        dims = [learningRate, denseLayerN, denseNeuronN, actFunc]
        par0 = [1E-3,         3,           128,          "relu"]
    elif "Standard" in modelName:
        dims = [learningRate, denseLayerN, denseNeuronN, actFunc, initFunc]
        par0 = [1E-3,         3,           128,           "elu",   "he_normal"]
    elif "Conv2D" in modelName:
        denseLayerN  = Integer(low=1,    high=5,                         name="denseLayerN")
        dims=[learningRate,convLayerN,convFilterN,denseLayerN,denseNeuronN,actFunc,initFunc]
        par0=[1E-3,        2,         64,         3,          128,        "elu",   "he_normal"]
    elif "RNN" in modelName:
        dims = [learningRate]
        par0 = [1E-3]
    elif "ResNet50" in modelName:
        optModelSearchOn = False 
        dims = [learningRate]
        par0 = [1E-3]
    else:
        raise AssertionError("main(): no model has been selected")
    if verbosity >= 1: print("\n####################################################RUN STARTS")
#dataset########################################################################################
    if verbosity >= 1: print("Preparing data:")
    inputImageSize = IMAGE_SIZE
    nameY = []
    prepDataLocs = []
    for dataLoc in [DATA_LOC, TESTDATA_LOC]:
        prepDataLocs.append(dataLoc + "")
        while prepDataLocs[-1][-1] == "/": prepDataLocs[-1] = prepDataLocs[-1][:-1]
        prepDataLocs[-1] = prepDataLocs[-1] + "Prep/"
        if verbosity >= 1: print("  saving data under:", prepDataLocs[-1])
        if len(nameY) == 0: 
            for _, directories, _ in os.walk(dataLoc):
                for directory in directories:
                    nameY.append(directory)
        for yIter, label in enumerate(tqdm(nameY)):
            origPath = dataLoc          + "/" + label + "/"
            prepPath = prepDataLocs[-1] + "/" + label + "/"
            pathlib.Path(prepPath).mkdir(parents=True, exist_ok=True)
            for imgName in tqdm(os.listdir(origPath)):
                outImgName = prepPath + imgName.split("/")[-1]
                if os.path.isfile(outImgName) == False:
                    errorOccured, origImgFile, resizedImgFile = False, None, None
                    try:
                        #stackoverflow.com/questions/9131992
                        #github.com/ImageMagick/ImageMagick/discussions/2754
                        origImgFile = cv2.imread(origPath+"/"+imgName)
                    except Exception as e:
                        warnings.warn(str(e), Warning)
                        errorOccured = True
                    if (errorOccured == False) and (origImgFile is not None): 
                        '''
                        ###original
                        resizedImgFile = zeroPadCenterResize(origImgFile, inputImageSize)
                        #resizedImgFile = cv2.cvtColor(resizedImgFile, cv2.COLOR_RGB2GRAY)
                        cv2.imwrite(outImgName, resizedImgFile) 
                        '''
                        ###ignore small images
                        if (origImgFile.shape[0] > inputImageSize[0]) and\
                           (origImgFile.shape[1] > inputImageSize[1]): 
                            resizedImgFile = zeroPadCenterResize(origImgFile, inputImageSize)
                            cv2.imwrite(outImgName, resizedImgFile) 
                        '''
                        ###different scales of each image
                        scaleArr = [1.0, 0.75, 0.5, 0.25]
                        for scaleIdx, resizeScale in enumerate(scaleArr):
                            rescaledSize = (np.array(inputImageSize)*resizeScale).astype(int) 
                            resizedImgFile = zeroPadCenterResize(origImgFile,    rescaledSize)
                            resizedImgFile = zeroPadCenterResize(resizedImgFile, inputImageSize)
                            inFileName  = outImgName.split("/")[-1]
                            outFileName = inFileName.replace(".", ("_"*scaleIdx)+".")
                            outputName = outImgName.replace(inFileName, outFileName)
                            cv2.imwrite(outputName, resizedImgFile)
                        '''
    ############################################################################################
    '''
    #simulate unlabeled Y's
    np.random.seed(RAND_SEED)
    inputY = []
    for y in inputYFull:
        if np.random.uniform() < dropRatio: inputY.append(float("NaN"))
        else:                               inputY.append(y)
    '''
    #saving input values
    trainingInputDict = {}
    trainingInputDict["RAND_SEED"]      = RAND_SEED
    trainingInputDict["nameY"]          = nameY
    trainingInputDict["inputImageSize"] = inputImageSize
    trainingInputDict["modelName"]     = modelName
    trainingInputDict["trainOn"]       = trainOn
    trainingInputDict["printPredFigN"] = printPredFigN
    trainingInputDict["testRatio"]       = testRatio
    trainingInputDict["dropRatio"]       = dropRatio
    trainingInputDict["validationRatio"] = validationRatio
    trainingInputDict["batchSize"]       = batchSize
    trainingInputDict["trainAutoencoderOn"] = trainAutoencoderOn
    trainingInputDict["autoEpochN"]         = autoEpochN
    trainingInputDict["optModelSearchOn"]  = optModelSearchOn
    trainingInputDict["optimizationCoreN"] = optimizationCoreN
    trainingInputDict["optimizationCallN"] = optimizationCallN
    trainingInputDict["learningEpochN"]    = learningEpochN
    trainingInputDict["bootstrappingN"]    = bootstrappingN
    trainingInputDict["retrainOptModelOn"]  = retrainOptModelOn 
    trainingInputDict["learningEpochNOpt"]  = learningEpochNOpt
    trainingInputDict["bootstrappingNOpt"]  = bootstrappingNOpt

    pathlib.Path(modelName).mkdir(parents=True, exist_ok=True)
    filenameInput = modelName + "/trainingInput.pickle"
    if os.path.isfile(filenameInput) == False:
        with open(filenameInput, "wb") as handle:
            pickle.dump(trainingInputDict, handle, protocol=pickle.HIGHEST_PROTOCOL)
        if verbosity >= 1: print("Saving input training parameters:\n    ", filenameInput)
    if verbosity >= 1:
        print("Loading dataset parameters:")
        print("  trainOn      :", trainOn)
        print("  printPredFigN:", printPredFigN)
        print("  testRatio      :", testRatio)
        print("  dropRatio      :", dropRatio)
        print("  validationRatio:", validationRatio)
        print("  batchSize      :", batchSize)
        print("Loading training parameters:")
        print("  trainAutoencoderOn:", trainAutoencoderOn)
        print("  autoEpochN        :", autoEpochN)
        print("  optModelSearchOn :", optModelSearchOn)
        print("  optimizationCallN:", optimizationCallN)
        print("  learningEpochN   :", learningEpochN )
        print("  bootstrappingN   : ", bootstrappingN)
        print("  retrainOptModelOn :", retrainOptModelOn)
        print("  learningEpochNOpt :", learningEpochNOpt)
        print("  bootstrappingNOpt :", bootstrappingNOpt)
#autoencoder####################################################################################
    if trainOn == False:
        trainAutoencoderOn = False
        optModelSearchOn   = False
        retrainOptModelOn  = False    
    pretrainedLayers = []
    '''
    if trainAutoencoderOn == True:
        if verbosity >= 1:
            print("####################################################AUTOENCODER PRETRAINING")
        encodedXuntrained = None
        trainX, validX, trainY, validY = train_test_split(inputXNorm, inputY, shuffle=False,\
                                                          test_size=validationRatio)
        #train encoder, decoder, autoencoder
        encoder = buildEncoderConv(inputShape, regularization="dropout")
        encoderOutputShape = list(encoder.layers[-1].output_shape)
        encoderOutputShape = [s for s in encoderOutputShape if s is not None]
        decoder = buildDecoderConv(inputShape=encoderOutputShape)

        autoEncoder = buildAutoEncoder(encoder, decoder)
        encodedXuntrained = encoder.predict(validX)
        tensorboardAutoDir = modelName + "/tensorboardAutoDir/"
        tensorboardAutoDir += str(int(time.time())) + "Conv"
        tensorboardAuto = tf.keras.callbacks.TensorBoard(tensorboardAutoDir)
        history = autoEncoder.fit(trainX, trainX, validation_data=(validX, validX),\
                                  epochs=autoEpochN, callbacks=[tensorboardAuto])
        encoder    .save(modelName+"/zEncoder.model")
        decoder    .save(modelName+"/zDecoder.model")
        autoEncoder.save(modelName+"/zAutoEncoder.model")
        os.rename(tensorboardAutoDir, tensorboardAutoDir.replace("tensorboardAutoDir/",\
                                                                 "tensorboardAutoDir/Fin"))
        #compressed figures   
        encoder     = tf.keras.models.load_model(modelName+"/zEncoder.model",    compile=False)
        decoder     = tf.keras.models.load_model(modelName+"/zDecoder.model",    compile=False)
        autoEncoder = tf.keras.models.load_model(modelName+"/zAutoEncoder.model",compile=False)
        if printFigN > 0:
            print("Saving the following figures:")
            encodedX = encoder.predict(validX)
            cmprsX   = autoEncoder.predict(validX)
            validX = validX.reshape(*validX.shape[:-1])
            cmprsX = cmprsX.reshape(*cmprsX.shape[:-1])
            printTSNE(encodedXuntrained, validY, nameY, "TSNEuntrained", verbosity=verbosity)
            printTSNE(encodedX,          validY, nameY, "TSNE",          verbosity=verbosity)
            for idx, valX in enumerate(validX[:printFigN]):
                fig = plt.figure(figsize=(12, 6))
                gs = gridspec.GridSpec(1, 2)
                ax = []
                for i in range (gs.nrows*gs.ncols): ax.append(fig.add_subplot(gs[i]))
                ax[0].imshow(valX, cmap=plt.cm.binary)
                ax[0].set_title("Original Normalized", fontsize=24)
                ax[1].imshow(cmprsX[idx], cmap=plt.cm.binary)
                ax[1].set_title("Autoencoder Compressed", fontsize=24) 
                filenameFig = FIG_LOC + "compressed"+str(idx)+".png"
                plt.savefig(filenameFig, dpi=100)
                plt.close()
                print("   ", filenameFig)
        #encoder for autoencoder pretraining
        encoder = tf.keras.models.load_model(modelName+"/zEncoder.model", compile=False)
        for i, layer in enumerate(encoder.layers):
            if isinstance(layer, tf.keras.layers.Conv2D):
                #layer.trainable = False
                pretrainedLayers.append(layer)
    '''
#####searching for optimal model################################################################
    if optModelSearchOn == True:
        if verbosity >= 1:
            print("################################################SEARCHING FOR OPTIMAL MODEL")
        fitFunc = fitFuncLambda(modelName, dims, prepDataLocs[0], inputImageSize,\
                                validationRatio, batchSize, learningEpochN, bootstrappingN,\
                                pretrainedLayers=pretrainedLayers, verbosity=verbosity)
        callbacks = []
        minCheckpointPath = modelName + "/checkpoint_gp_minimize.pkl"
        if SAVE_GM_MINIMIZE_CHECKPOINT == True:
            minCheckpointSaver = CheckpointSaver(minCheckpointPath, compress=9,\
                                                 store_objective=False)
            callbacks.append(minCheckpointSaver)
        optParDict, eval0 = {}, None
        #restore gp_minimize: remember to delete the .pkl file when changing model
        global OPTITER, OPTACCU, OPTASTD
        try:
            restoredOpt = load_gp_minimize(minCheckpointPath)
            par0, eval0 = restoredOpt.x_iters, restoredOpt.func_vals
            if verbosity >= 1:
                print("Reading the gp_minimize checkpoint file:\n    ", minCheckpointPath)
            OPTITER = len(eval0)
            optimizationCallN -= (OPTITER + 1)
            parDicts = {}
            with open(modelName + "/pars.pickle", "rb") as handle:
                parDicts = pickle.load(handle)
            optParDict = parDicts["opt"]
            OPTACCU = optParDict["val_accuracy"]
            OPTASTD = optParDict["val_accu_std"]
            if verbosity >= 2:
                model = tf.keras.models.load_model(modelName)
                print(model.summary())
                print("Parameters:\n   ", optParDict)
            if verbosity >= 1:
                print("Current optimal validation accuracy:")
                print("   ", OPTACCU, "+/-", (OPTASTD if (OPTASTD > 0) else "NA"))
        except FileNotFoundError:
            if (SAVE_GM_MINIMIZE_CHECKPOINT == True) and (verbosity >= 1):
                print("Saving checkpoint enabled for gp_minimize:\n    ", minCheckpointPath)
        except:
            raise
        #main optimization
        if optimizationCallN > 0:
            result = gp_minimize(func=fitFunc, dimensions=dims, x0=par0,y0=eval0,acq_func="EI",\
                                 n_jobs=optimizationCoreN, n_calls=optimizationCallN,\
                                 callback=callbacks)
#retrain optimal model##########################################################################
    if retrainOptModelOn == True:
        if verbosity >= 1:
            print("######################################################RETRAIN OPTIMAL MODEL")
        optParDict, parOpt = {}, []
        try:
            parDicts = {}
            with open(modelName + "/pars.pickle", "rb") as handle:
                parDicts = pickle.load(handle)
                optParDict = parDicts["opt"]
            for dim in dims: parOpt.append(optParDict[dim.name])
        except FileNotFoundError:
            parOpt = par0.copy()
            print("The parameter file from model optimization is not found:")
            print("   ", modelName + "/pars.pickle")
            print("Using par0 as the optimized parameters")
        except:
            raise
        fitFuncOpt = fitFuncLambda(modelName, dims, prepDataLocs[0], inputImageSize,\
                                   validationRatio, batchSize, learningEpochNOpt,\
                                   bootstrappingNOpt, pretrainedLayers=pretrainedLayers,\
                                   verbosity=verbosity)
        optAccuracy = fitFuncOpt(parOpt)
#prediction#####################################################################################
    if verbosity >= 1:
        print("###############################################################MODEL PREDICTION")
    #loading trained data
    histDF, optParDict = {}, None
    try:
        model = tf.keras.models.load_model(modelName)
        histDFs, parDicts = {}, {}
        with open(modelName + "/history.pickle", "rb") as handle:
            histDFs = pickle.load(handle) 
        with open(modelName + "/pars.pickle", "rb") as handle:
            parDicts = pickle.load(handle)
        histDF     = histDFs["opt"]
        optParDict = parDicts["opt"]
        if verbosity >= 2: print(model.summary())
    except OSError or FileNotFoundError:
        print("No trained model is found:\n    ", modelName)
        sys.exit(0)
    except:
        raise
    #loading test data
    #plotting
    histDF.plot(figsize=(8, 5))
    plt.title("Learning Performance History")
    plt.grid("True")
    plt.gca().set_ylim(0.0, 1.0)
    filenameFig = FIG_LOC + "_optModel_learningHistory.png"
    plt.savefig(filenameFig)
    plt.close()
    if verbosity >= 1: print("Saving training result/prediction figures:\n    ", filenameFig)
    #prediction figures
    dataTest = tf.keras.utils.image_dataset_from_directory(\
        prepDataLocs[1], image_size=inputImageSize, shuffle=False)
    if nameY != dataTest.class_names:
        raise AssertionError("main(): make sure the labels in the train/test directories match")
    testPaths = dataTest.file_paths
    testIdx = 0
    if verbosity >= 1: print("  test loss, test acc:", model.evaluate(dataTest))
    if printPredFigN != 0:
        testXs, testYs = next(iter(dataTest))
        testYs = testYs.numpy()
        predYweights = model.predict(testXs)
        predYs = [np.argmax(predYweight) for predYweight in predYweights] 
        for testY, predY in zip(testYs, predYs):
            testXorig = cv2.imread(testPaths[testIdx].replace("Pred", ""))
            testXorig = cv2.cvtColor(testXorig, cv2.COLOR_BGR2RGB)
            plt.imshow(testXorig, cmap=plt.cm.Spectral)
            plt.title("Prediction: "+nameY[predY], fontsize=24)
            filenameFig = FIG_LOC + "predicted" + str(testIdx) + ".png"
            plt.savefig(filenameFig, dpi=100)
            plt.close()
            testIdx += 1
            if verbosity >= 1: print(" ", testIdx,nameY[predY],nameY[testY],"\n   ",filenameFig)
            if testIdx > printPredFigN: break

























################################################################################################
################################################################################################
################################################################################################
#model##########################################################################################
def buildModel(modelName, pars, dims, targetN, inputShape, pretrainedLayers=[]):
    if "SimpleDense" in modelName:
        return modelDenseSimple(pars, dims, targetN, inputShape, pretrainedLayers)
    elif "Dense" in modelName:
        return modelDense(pars, dims, targetN, inputShape, pretrainedLayers)
    elif "Standard" in modelName:
        return modelStandard(pars, dims, targetN, inputShape, pretrainedLayers)
    elif "Conv2D" in modelName:
        return modelConv2D(pars, dims, targetN, inputShape, pretrainedLayers)
    elif "RNN" in modelName:
        return modelRNN(pars, dims, targetN, inputShape, pretrainedLayers)
    elif "ResNet50" in modelName:
        return modelResNet50(pars, dims, targetN, inputShape, pretrainedLayers)
    else:
        print("No model found:\n    ", modelName)
        sys.exit(0)
def modelDenseSimple(pars, dims, targetN, inputShape, pretrainedLayers=[]):
    #dims: learningRate, denseNeuronN, denseNeuronN2
    parDict = {}
    for par, dim in zip(pars, dims): parDict[dim.name] = par
    
    model = tf.keras.models.Sequential()
    model.add(tf.keras.layers.Rescaling(1.0/127, offset=-1))
    for layer in pretrainedLayers: model.add(cloneLayer(layer))
    if pretrainedLayers == []: model.add(tf.keras.layers.Flatten(input_shape=inputShape))

    model.add(tf.keras.layers.Dense(parDict["denseNeuronN"],  activation="relu"))
    model.add(tf.keras.layers.Dense(parDict["denseNeuronN2"], activation="relu"))
    model.add(tf.keras.layers.Dense(targetN,                  activation="softmax"))
    model.compile(optimizer=tf.keras.optimizers.SGD(learning_rate=parDict["learningRate"]),\
                  loss=tf.keras.losses.sparse_categorical_crossentropy,metrics=["accuracy"])
    return model
def modelDense(pars, dims, targetN, inputShape, pretrainedLayers=[]):
    #dims: learningRate, denseLayerN, denseNeuronN, actFunc
    parDict = {}
    for par, dim in zip(pars, dims): parDict[dim.name] = par

    model = tf.keras.models.Sequential()
    model.add(tf.keras.layers.Rescaling(1.0/127, offset=-1))
    for layer in pretrainedLayers: model.add(cloneLayer(layer))
    if pretrainedLayers == []: model.add(tf.keras.layers.Flatten(input_shape=inputShape))

    for i in range(parDict["denseLayerN"]):
        model.add(tf.keras.layers.Dense(parDict["denseNeuronN"], activation=parDict["actFunc"]))
    model.add(tf.keras.layers.Dense(targetN, activation="softmax"))
    model.compile(optimizer=tf.keras.optimizers.SGD(learning_rate=parDict["learningRate"]),\
                  loss=tf.keras.losses.sparse_categorical_crossentropy, metrics=["accuracy"])
    return model
def modelStandard(pars, dims, targetN, inputShape, pretrainedLayers=[]):
    #dims: learningRate, denseLayerN, denseNeuronN, actFunc, initFunc
    #############Adjustables#############
    dropoutRate   = 0.2
    momentumRatio = 0.9
    #####################################
    parDict = {}
    for par, dim in zip(pars, dims): parDict[dim.name] = par

    model = tf.keras.models.Sequential()
    model.add(tf.keras.layers.Rescaling(1.0/127, offset=-1))
    for layer in pretrainedLayers: model.add(cloneLayer(layer))
    if pretrainedLayers == []: model.add(tf.keras.layers.Flatten(input_shape=inputShape))

    for i in range(parDict["denseLayerN"]):
        model.add(tf.keras.layers.Dense(parDict["denseNeuronN"],activation=parDict["actFunc"],\
                                        kernel_initializer=parDict["initFunc"]))
        model.add(dropoutMC(rate=dropoutRate))
        model.add(tf.keras.layers.BatchNormalization())
        model.add(tf.keras.layers.Dense(parDict["denseNeuronN"],activation=parDict["actFunc"],\
                                        kernel_initializer=parDict["initFunc"]))
    model.add(tf.keras.layers.Dense(targetN, activation="softmax"))
    optimizer = tf.keras.optimizers.SGD(learning_rate=parDict["learningRate"],\
                                        momentum=momentumRatio, nesterov=True)
    model.compile(optimizer=optimizer,\
                  loss=tf.keras.losses.sparse_categorical_crossentropy, metrics=["accuracy"])
    return model
#https://stats.stackexchange.com/questions/240305
#https://machinelearningmastery.com/
#  image-augmentation-with-keras-preprocessing-layers-and-tf-image/
def modelConv2D(pars, dims, targetN, inputShape, pretrainedLayers=[]):
    #dims: learningRate, convLayerN, convFilterN, denseLayerN, denseNeuronN, actFunc, initFunc
    #############Adjustables#############
    convLayerNinit   = 64
    convFilterNinit  = (8, 8)
    convDropoutRate  = 0.1
    denseDropoutRate = 0.5
    momentumRatio    = 0.9
    #####################################
    parDict = {}
    for par, dim in zip(pars, dims): parDict[dim.name] = par

    model = tf.keras.models.Sequential()
    model.add(flipLayer("horizontal"))
    #model.add(rotationLayer(0.2))           #makes no sense for directional photos 
    model.add(tf.keras.layers.Rescaling(1.0/127, offset=-1))
    for layer in pretrainedLayers: model.add(cloneLayer(layer))
    if pretrainedLayers == []:
        model.add(tf.keras.layers.Conv2D(convLayerNinit, convFilterNinit, 
                                         activation=parDict["actFunc"],\
                                         padding="SAME", input_shape=inputShape))
    for i in range(parDict["convLayerN"]):
        if pow(2, i+1) < min(inputShape[1], inputShape[2]): 
            model.add(tf.keras.layers.MaxPool2D(pool_size=(2, 2)))
        filterN = max(8, parDict["convFilterN"]/pow(2, parDict["convLayerN"]-1-i))
        model.add(tf.keras.layers.Conv2D(filterN, (3, 3), activation=parDict["actFunc"],\
                                         padding="SAME"))
        model.add(tf.keras.layers.Dropout(rate=convDropoutRate))
    model.add(tf.keras.layers.Flatten())
    for i in range(parDict["denseLayerN"]):
        neutronN = max(8, parDict["denseNeuronN"]/pow(2, i))
        model.add(tf.keras.layers.Dropout(rate=denseDropoutRate))
        model.add(tf.keras.layers.Dense(neutronN, activation=parDict["actFunc"],\
                                        kernel_initializer=parDict["initFunc"]))
    model.add(tf.keras.layers.Dense(targetN, activation="softmax"))
    optimizer = tf.keras.optimizers.SGD(learning_rate=parDict["learningRate"],\
                                        momentum=momentumRatio, nesterov=True)
    model.compile(optimizer=optimizer,\
                  loss=tf.keras.losses.sparse_categorical_crossentropy, metrics=["accuracy"])
    return model
#https://machinelearningmastery.com/how-to-implement-major-architecture-innovations-for-
#convolutional-neural-networks/
def modelRNN(pars, dims, targetN, inputShape, pretrainedLayers=[]):
    #dims: learningRate
    #############Adjustables#############
    convLayerNinit  = 64
    convFilterNinit = (8, 8)
    dropoutRate     = 0.5
    momentumRatio   = 0.9
    deepLayers      = [64]*3 + [128]*4 + [256]*6 + [512]*3
    #####################################
    parDict = {}
    for par, dim in zip(pars, dims): parDict[dim.name] = par

    inputZ = tf.keras.layers.Input(inputShape[1:])
    inputZ = flipLayer("horizontal")(inputZ)
    inputZ = tf.keras.layers.Rescaling(1.0/127, offset=-1)(inputZ)
    Z = inputZ + 0
    for layer in pretrainedLayers: Z = layer(Z)
    if pretrainedLayers == []:
        Z = tf.keras.layers.Conv2D(convLayerNinit, convFilterNinit, strides=2,\
                                   activation="relu", padding="SAME")(Z)
    Z = tf.keras.layers.BatchNormalization()(Z)
    Z = tf.keras.layers.Activation("relu")(Z)
    Z = tf.keras.layers.MaxPool2D(pool_size=(3, 3), strides=2, padding="SAME")(Z)
    
    filterNpre = 64
    for filterN in deepLayers:
        strideN = (1 if filterN == filterNpre else 2);
        Z = residualBlock(Z, filterN, strideN=strideN)
        filterNpre = filterN*1
    
    Z = tf.keras.layers.GlobalAvgPool2D()(Z)
    Z = tf.keras.layers.Flatten()(Z)
    Z = tf.keras.layers.Dropout(rate=dropoutRate)(Z)
    Z = tf.keras.layers.Dense(64, activation="relu", kernel_initializer="he_normal")(Z)
    Z = tf.keras.layers.Dense(targetN, activation="softmax")(Z)
    model = tf.keras.models.Model(inputs=inputZ, outputs=Z)
    optimizer = tf.keras.optimizers.SGD(learning_rate=parDict["learningRate"],\
                                        momentum=momentumRatio, nesterov=True)
    model.compile(optimizer=optimizer,\
                  loss=tf.keras.losses.sparse_categorical_crossentropy, metrics=["accuracy"])
    return model
def residualBlock(inputZ, filterN, strideN=1):
    mainZ = inputZ + 0
    mainZ = tf.keras.layers.Conv2D(filterN, [3, 3], strides=strideN, padding="SAME",\
                                   use_bias=False)(mainZ)
    mainZ = tf.keras.layers.BatchNormalization()  (mainZ)
    mainZ = tf.keras.layers.Activation("relu")    (mainZ)
    mainZ = tf.keras.layers.Conv2D(filterN, [3, 3], strides=1, padding="SAME",\
                                   use_bias=False)(mainZ)
    mainZ = tf.keras.layers.BatchNormalization()  (mainZ)

    skipZ = inputZ + 0
    if strideN > 1:             #patch size of [1, 1] is the key
        skipZ = tf.keras.layers.Conv2D(filterN, [1, 1], strides=strideN, padding="SAME",\
                                       use_bias=False)(skipZ)
        skipZ = tf.keras.layers.BatchNormalization()  (skipZ)
    
    mergedZ = tf.keras.layers.add([mainZ, skipZ])
    mergedZ = tf.keras.layers.Activation("relu")(mergedZ)
    return mergedZ
#https://stackoverflow.com/questions/49492255
#also include method on how to inject layers in existing model
def modelResNet50(pars, dims, targetN, inputShape, pretrainedLayers=[]):
    #dims: learningRate
    #############Adjustables#############
    dropoutRate     = 0.5
    momentumRatio   = 0.9
    #####################################
    from tensorflow.keras.applications.resnet50 import ResNet50
    
    parDict = {}
    for par, dim in zip(pars, dims): parDict[dim.name] = par
    #-------------------------------------------------------------------------
    #NOTE: ResNet50 expects inputShape=(None, 224, 224, 3)
    model = ResNet50(weights="imagenet")

    print(model.summary())
    #-------------------------------------------------------------------------
    optimizer = tf.keras.optimizers.SGD(learning_rate=parDict["learningRate"],\
                                        momentum=momentumRatio, nesterov=True)
    model.compile(optimizer=optimizer,\
                  loss=tf.keras.losses.sparse_categorical_crossentropy, metrics=["accuracy"])
    return model
class dropoutMC(tf.keras.layers.Dropout):
    def call(self, inputs):
        return super().call(inputs, training=True) #to be turned off during .evaluation()
class flipLayer(tf.keras.layers.RandomFlip):
    def call(self, inputs):
        return super().call(inputs, training=True) #to be turned off during .evaluation()
class rotationLayer(tf.keras.layers.RandomRotation):
    def call(self, inputs):
        return super().call(inputs, training=True) #to be turned off during .evaluation() 
#autoencoder####################################################################################
def buildAutoEncoder(encoder, decoder):
    model = tf.keras.models.Sequential([encoder, decoder])
    model.compile(optimizer=tf.keras.optimizers.SGD(learning_rate=1.0),\
                  loss="binary_crossentropy", metrics=[roundedAccuracy])
    return model
def buildEncoderConv(inputShape, regularization=False):
    #############Adjustables#############
    convFilterNinit = (8, 8)
    #####################################
    model = tf.keras.models.Sequential()
    model.add(tf.keras.layers.Conv2D(32, convFilterNinit, activation="selu", padding="SAME",\
                                     input_shape=inputShape))
    model.add(tf.keras.layers.MaxPool2D(pool_size=(2, 2)))
    if ("gaus" in regularization) or ("Gaus" in regularization):
        model.add(tf.keras.layers.GaussianNoise(0.1))
    elif ("drop" in regularization) or ("Drop" in regularization):
        model.add(tf.keras.layers.Dropout(0.5))
    if ("l1" in regularization) or ("L1" in regularization):
        model.add(tf.keras.layers.Conv2D(64, (3, 3), activation="selu", padding="SAME",\
                                         activity_regularizer=tf.keras.regularizers.l1(10e-5)))
    elif ("l2" in regularization) or ("L2" in regularization):
        model.add(tf.keras.layers.Conv2D(64, (3, 3), activation="selu", padding="SAME",\
                                         activity_regularizer=tf.keras.regularizers.l2(10e-2)))
    else:
        model.add(tf.keras.layers.Conv2D(64, (3, 3), activation="selu", padding="SAME"));
    model.add(tf.keras.layers.MaxPool2D(pool_size=(2, 2)))
    return model
def buildDecoderConv(inputShape):
    model = tf.keras.models.Sequential()
    model.add(tf.keras.layers.Conv2DTranspose(32, (3, 3), strides=2, activation="selu",\
                                              padding="SAME", input_shape=inputShape))
    model.add(tf.keras.layers.Conv2DTranspose(1,  (3, 3), strides=2, activation="sigmoid",\
                                              padding="SAME"))
    return model
def printTSNE(encodedXInput, knownYInput, nameY, figName, verbosity=1):
    if encodedXInput is None: return
    encodedX, knownY = dropNaNY(encodedXInput, knownYInput)
    tsne = TSNE()
    if len(encodedX.shape) > 2:
        shapeX1D = 1
        for n in encodedX.shape[1:]: shapeX1D *= n
        encodedX = encodedX.reshape(encodedX.shape[0], shapeX1D)
    encodedX2D = tsne.fit_transform(encodedX) #note: different outcome everytime

    fig = plt.figure(figsize=(8, 6))
    gs = gridspec.GridSpec(1, 1)
    ax = []
    for i in range (gs.nrows*gs.ncols): ax.append(fig.add_subplot(gs[i]))

    maxY = np.max(knownY)
    minY = np.min(knownY)
    cmap = plt.get_cmap("jet", maxY-minY+1)
    labelFormat = FuncFormatter(lambda x, pos: nameY[int(x)]) #pos required by FuncFormatter
    plot = ax[0].scatter(encodedX2D[:,0], encodedX2D[:,1], c=knownY, s=10, cmap=cmap,\
                         vmax=(maxY+0.5), vmin=(minY-0.5))
    fig.colorbar(plot, ax=ax[0], format=labelFormat, ticks=np.arange(minY, maxY+1))
    ax[0].set_title("Encoder t-SNE Visualization")
    
    filenameFig = FIG_LOC + "-" + figName + ".png"
    gs.tight_layout(fig)
    plt.savefig(filenameFig, dpi=100)
    plt.close()
    if verbosity >= 1: print("   ", filenameFig)
#model fitter###################################################################################
def fitFuncGen(modelName, pars, dims, prepDataLoc, imageSize, valiR, batchSize,\
               epochN, bootstrappingN, pretrainedLayers=[], verbosity=1):
    #############Adjustables#############
    minLearningRate       = pow(10, -6)
    scheduleExpDecayConst = 10
    #####################################
    global OPTITER, OPTACCU, OPTASTD
    parStr  = ""
    parDict = {}
    for par, dim in zip(pars, dims):
        parStr += str(par) + "-"
        parDict[dim.name] = par
    parStr = parStr[:-1]
    if verbosity >= 1:
        print("###################################################START MODEL FITTING", OPTITER)
        print("Parameters:", parStr)
    callbacks = []
    epochCheckpointPath = modelName + "/checkpoint_epoch.{epoch:02d}-{val_loss:.4f}.h5"
    if SAVE_EPOCH_CHECKPOINT == True:
        epochCheckpointSaver = tf.keras.callbacks.ModelCheckpoint(filepath=epochCheckpointPath,\
                                                                  save_weights_only=True,\
                                                                  verbose=verbosity)
        callbacks.append(epochCheckpointSaver)
    tensorboardModelDir = modelName + "/tensorboardModelDir/"
    tensorboardModelDir += str(int(time.time())) + "--" + parStr
    tensorboard = tf.keras.callbacks.TensorBoard(log_dir=tensorboardModelDir,\
                                                 histogram_freq=0, write_graph=True,\
                                                 write_grads=False, write_images=False)
    callbacks.append(tensorboard)

    val_accuracies = []
    bootCheckpointPath = modelName + "/checkpoint_boot.pickle"
    if SAVE_BOOTSTRAP_CHECKPOINT == True:
        if os.path.isfile(bootCheckpointPath) == True:
            if verbosity >= 1: 
                print("Reading the bootstrap checkpoint file:\n    ", bootCheckpointPath)
            with open(bootCheckpointPath, "rb") as handle:
                val_accuracies = pickle.load(handle)
        elif verbosity >= 1:
            print("Saving checkpoint enabled for bootstrap:\n    ", bootCheckpointPath) 
    for bootSeed in range(bootstrappingN):
        if verbosity >= 1: print("\n############################BOOTSTRAPPING:", bootSeed)
        if bootSeed < len(val_accuracies): continue
        #tensorflow.org/tutorials/load_data/images
        dataTrain, dataVali = tf.keras.utils.image_dataset_from_directory(\
            prepDataLoc, image_size=imageSize, validation_split=valiR, subset="both",\
            batch_size=batchSize, seed=bootSeed, shuffle=True)
        lenY = len(dataTrain.class_names)
        AUTOTUNE = tf.data.AUTOTUNE
        dataTrain = dataTrain.cache().prefetch(buffer_size=AUTOTUNE)    
        dataVali  = dataVali .cache().prefetch(buffer_size=AUTOTUNE)
        input_shape = None
        for input_shape_in_data, _ in dataTrain:
            input_shape = [None, *input_shape_in_data.shape[1:]]
            break
        tf.random.set_seed(bootSeed)    #for dropout Monte Carlo layers
        model = buildModel(modelName, pars, dims, lenY, input_shape,\
                           pretrainedLayers=pretrainedLayers)
        model.build(input_shape)
        if verbosity >= 3: print(model.summary())
        checkpoint_epoch_files = glob.glob(modelName + "/checkpoint_epoch*")
        if SAVE_EPOCH_CHECKPOINT == True:
            checkpoint_epoch_files.sort(key=lambda Lfunc: int(re.sub("\D", "", Lfunc)))
            if len(checkpoint_epoch_files) != 0:
                if verbosity >= 1: 
                    print("Reading the epoch checkpoint file:\n    ",checkpoint_epoch_files[-1])
                try:
                    model.load_weights(checkpoint_epoch_files[-1])
                except:
                    print("     ...reading failed, restarting from epoch 0")
                for checkpoint_epoch_file in glob.glob(modelName + "/checkpoint_epoch*"): 
                    os.remove(checkpoint_epoch_file)
            elif verbosity >= 1:
               print("Saving checkpoint enabled for epoch:\n    ", epochCheckpointPath) 
        callbacks_final = [*callbacks]
        if "learningRate" in parDict.keys():
            schedulerFunc = schedulerLambda(parDict["learningRate"], minLearningRate,\
                                            scheduleExpDecayConst,\
                                            epochShift=len(checkpoint_epoch_files))
            scheduler = tf.keras.callbacks.LearningRateScheduler(schedulerFunc)
            callbacks_final.append(scheduler)
        ########################################################################################
        history = model.fit(dataTrain, validation_data=dataVali,\
                            epochs=(epochN-len(checkpoint_epoch_files)),\
                            callbacks=callbacks_final)
        ########################################################################################
        if SAVE_EPOCH_CHECKPOINT == True:
            for checkpoint_epoch_file in glob.glob(modelName + "/checkpoint_epoch*"): 
                os.remove(checkpoint_epoch_file)
        val_accuracies.append(history.history["val_accuracy"][-1])
        if verbosity >= 1: print("val_accuracy =", val_accuracies[bootSeed])
        if SAVE_BOOTSTRAP_CHECKPOINT == True:
            with open(bootCheckpointPath, "wb") as handle:
                pickle.dump(val_accuracies, handle, protocol=pickle.HIGHEST_PROTOCOL)
        if (OPTASTD > 0) and (val_accuracies[bootSeed] < (OPTACCU - 6*OPTASTD)):
            if verbosity >= 1: 
                print("WARNING: 6-sigma smaller than the current optimal validation accuracy:")
                print("   ", OPTACCU, "-", "6*" +str(OPTASTD))
                print("Terminating the bootstrapping...\n")
            break
    if os.path.isfile(bootCheckpointPath) == True: os.remove(bootCheckpointPath)
    val_accuracy = np.mean(val_accuracies)
    val_accu_std = 0.0
    if len(val_accuracies) >= 2: val_accu_std = np.std(val_accuracies, ddof=1)
    if verbosity >= 1:
        print("--------------------------------------------------------------RESULT:")
        print("Parameters:                ", parStr)
        print("Ending Learning Rate      =", K.eval(model.optimizer.learning_rate))
        print("Model Validation Accuracy =", val_accuracy, "+/-", val_accu_std)

    parDict["bootstrappingN"] = bootstrappingN
    parDict["val_accuracy"]   = val_accuracy
    parDict["val_accu_std"]   = val_accu_std
    parDict["isOpt"]          = False
    parDicts, histDFs = {}, {}
    try:
        with open(modelName + "/pars.pickle", "rb") as handle:
            parDicts = pickle.load(handle)
        with open(modelName + "/history.pickle", "rb") as handle:
            histDFs = pickle.load(handle)
    except FileNotFoundError:
        warnings.warn("fitFuncGen(): creating new pars.pickle and history.pickle", Warning) 
    except:
        raise
    histDF = pd.DataFrame(history.history)
    histDFs[str(OPTITER)] = histDF
    parDicts[str(OPTITER)] = parDict 
    if val_accuracy > OPTACCU:
        OPTACCU = 1.0*val_accuracy
        OPTASTD = max(1.0*val_accu_std, 0.0)
        model.save(modelName)
        histDFs["opt"] = histDF
        parDict["isOpt"] = True        
        parDicts["opt"] = parDict 
        if verbosity >= 1: print("Optimal So Far!")
    with open(modelName + "/history.pickle", "wb") as handle:
        pickle.dump(histDFs, handle, protocol=pickle.HIGHEST_PROTOCOL)
    with open(modelName + "/pars.pickle", "wb") as handle:
        pickle.dump(parDicts, handle, protocol=pickle.HIGHEST_PROTOCOL)
    if verbosity >= 1:
        print("##########################################################END MODEL FITTING\n\n")
    del model
    os.rename(tensorboardModelDir, \
              tensorboardModelDir.replace("tensorboardModelDir/", "tensorboardModelDir/Fin"))
    OPTITER += 1
    return -val_accuracy
def fitFuncLambda(modelName, dims, prepDataLoc, imageSize, valiR, batchSize,\
                  epochN, bootstrappingN, pretrainedLayers=[], verbosity=1):
    return lambda pars: fitFuncGen(modelName, pars, dims, prepDataLoc, imageSize, valiR,\
                                   batchSize, epochN, bootstrappingN, pretrainedLayers=[],\
                                   verbosity=verbosity)

################################################################################################
if __name__ == "__main__": main()





