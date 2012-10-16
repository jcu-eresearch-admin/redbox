from CSVAlertHandler import CSVAlertHandler
from XMLAlertHandler import XMLAlertHandler
import AlertException
import os
import traceback
import time
import shutil
from com.googlecode.fascinator import HarvestClient
from com.googlecode.fascinator.common import FascinatorHome
from com.googlecode.fascinator.api.harvester import HarvesterException
from java.io import File

class Alert:    
    debug = False
    
    def __init__(self, redboxVersion, config, baselineData):
        
        self.config = config
        self.redboxVersion = redboxVersion
        self.name = config['name']
        self.path = config['path']
        self.harvestConfig = config['harvestConfig']
        self.handlers = config['handlers']
        self.processLog = []
        
        if 'alertBaselineData' in config:
            self.baseline = dict(baselineData.items() + config['alertBaselineData'].items())
        else:
            self.baseline = baselineData
        
        #These directories are used to hold files during/after processing
        self.__DIR_PROCESSED = os.path.join(self.path, ".processed")
        self.__DIR_ALERT = os.path.join(self.__DIR_PROCESSED, time.strftime("%Y_%m_%d_%H_%M_%S"))
        self.__DIR_PROCESSING = os.path.join(self.__DIR_ALERT, "processing")
        self.__DIR_SUCCESS = os.path.join(self.__DIR_ALERT, "success")
        self.__DIR_FAILED = os.path.join(self.__DIR_ALERT, "failed")
        self.__DIR_ORIGINAL = os.path.join(self.__DIR_ALERT, "original")
        
    def processAlert(self):
        try:
            self.__checkDirs()
        except Exception, e:
            #We can't handle this alert as the dirs weren't available
            raise
        
        files = os.listdir(self.path)
        for file in files:
            if not os.path.isfile(self.pBase(file)):
                #Ignore sub-dirs
                continue
            
            self.processLog = []
            logFile = os.path.join(self.__DIR_ALERT, file + ".log")
            
            try:
                self.logInfo(file, "Processing file " + self.pBase(file))
                self.logInfo(file, self.config)
                (success, fail) = self.__handleAlert(file);
                self.logInfo(file, "File processing complete. Successful imports: %s; Failed imports %s"%(success,fail))        
            except Exception, e:
                self.logException(file, e)
            finally:
                #Move the original file to an archive folder
                shutil.move(self.pBase(file), self.__DIR_ORIGINAL) 
                self.saveAlertLog(logFile)                         
        return
    
    def __handleAlert(self, file):
        successCount = 0
        failedCount = 0
        handler = None
        
        ext = file.rpartition('.')[2]
        if not ext in self.handlers:
            self.logInfo(file, "Did not process file as extension is not configured")
            return (0,0)
                    
        if self.handlers[ext] == "CSVAlertHandler":
            config = self.config['CSVAlertHandlerParams']['configMap'][ext]
            handler = CSVAlertHandler(self.pBase(file), config)
            self.logInfo(file, "Using the CSVAlertHandler for file with extension %s" % ext)
        elif self.handlers[ext] == "XMLAlertHandler":
            config = self.config['XMLAlertHandlerParams']['configMap'][ext]
            handler = XMLAlertHandler(self.pBase(file), config)
            self.logInfo(file, "Using the XMLAlertHandler for file with extension %s" % ext)
        else:
            raise AlertException("Unknown file handler: '%s'" % self.handlers[ext])
            
        try:
            jsonList = handler.process()
        except:
            ## Processing failed
            raise 
        
        if jsonList is None:
            self.logInfo(file, "No records were returned.")
            return(0,0)
            
        ## Now all of the JSON Objects need to be ingested into the tool chain
        id = 0
        for json in jsonList:
            id += 1
            #use an incremental filename in case the data file contains more than 1 record
            meta_file = self.pTemp("%s.%s" % (file,id))
            self.logInfo(file, "Using metadata file: %s" % meta_file)
            try:
                oid = self.__ingestJson(file, meta_file, json)
                successCount += 1
            except HarvesterException, e:
                failedCount += 1
                self.logInfo(file, "Moving failed metadata file [%s] to %s." % (meta_file, self.__DIR_FAILED))
                shutil.move(meta_file, self.__DIR_FAILED)
                continue
            
            self.logInfo(file, "Moving successful metadata file [%s] to %s." % (meta_file, self.__DIR_SUCCESS))
            shutil.move(meta_file, self.__DIR_SUCCESS)

        return (successCount, failedCount)
    
    def __ingestJson(self, file, meta_file, json):
        '''Takes a json object and sends it through to the harvester
        
        meta_file -- the name to use for the resulting source file of the data
        json -- the json construct to be sent to the harvester
        '''
        harvester = None
        oid = None
        
        ## Cache the file out to disk... although it requires
        ## .tfpackage extension due to jsonVelocity transformer
        try:
            jsonFile = open(meta_file, "wb")
            jsonFile.write(json.toString(True).encode('utf-8'))  
        finally:
            jsonFile.close
            
        self.logInfo(file, "Submitting to harvest. Config file is %s and meta_file is %s" % (self.harvestConfig, meta_file))
        try:
            ## Now instantiate a HarvestClient just for this File.
            harvester = HarvestClient(File(self.harvestConfig), File(meta_file), "guest")
            harvester.start()
            ## And cleanup afterwards
            oid = harvester.getUploadOid() 
            
        except HarvesterException, e:     
            self.logException(file, e) 
            raise e
        finally:
            ## Cleanup
            if harvester is not None:
                harvester.shutdown()
                
        self.logInfo(file, "Successfully harvested alert item %s from processing file %s" % (oid,meta_file))
        return oid
    
    def __checkDirs(self):
        """Makes sure that the required directories exist: failed, processed, success
        """

        #All alert directories will have 1 process folder: .processed 
        self.__createDir(self.__DIR_PROCESSED)
        
        #Under that directory will be a subdir for each of the alerts run
        self.__createDir(self.__DIR_ALERT)
        
        #And finally we have the processing sub-subdirs
        self.__createDir(self.__DIR_PROCESSING)
        self.__createDir(self.__DIR_SUCCESS)
        self.__createDir(self.__DIR_FAILED)
        self.__createDir(self.__DIR_ORIGINAL)
        return

    def __createDir(self, dir):
        #print dir
        #self.logInfo("", "Checking directory: %s" % dir)
        try:
            os.mkdir(dir)
        except OSError:
            #Thrown when directory already exists so ignore
            pass
        
        if not os.path.exists(dir):
            raise AlertException("Required processing directory % does not exist. I even tried to create it for you." % dir)

    ## Short nameed Wrappers for convention based file paths
    def pBase(self, file):
        # A base path file
        return os.path.join(self.path, file)
    
    def pTemp(self, file):
        # Cache file for the HarvestClient... '.tfpackage' extension
        #  required inside system due to jsonVelocity transformer
        return os.path.join(self.__DIR_PROCESSING, file + ".tfpackage")
    
    def log(self, type, fileName, message):
        self.processLog.append( {"timestamp": time.ctime(),
                "type": type,
                "alert": self.name,
                "file": fileName,
                "message": message})

        if Alert.debug:
            print "%s\t%s\t%s\t%s" % (type, self.name, fileName, message)
        
    def logException (self, fileName, e):
        self.log("ERROR", fileName, "Message was: [%s] Exception was: [%s]" % (e.message, traceback.format_exc()))
    
    def logInfo(self, fileName, message):
        self.log("INFO", fileName, message)
        
    def saveAlertLog(self, file):
        f = None
        try:
            f = open(file, "wb")
            for item in self.processLog:
                if item["type"] == "ERROR":
                    f.write("[ERROR]\t[%s]\t[%s]\t[%s]\t%s\n" % (item["alert"], item["file"], item["timestamp"], item["message"]))
                else:
                    f.write("[INFO] [%s]\t[%s]\t[%s]\t%s\n" % (item["alert"], item["file"], item["timestamp"], item["message"]))
        finally:
            if not f is None:
                f.close()
        