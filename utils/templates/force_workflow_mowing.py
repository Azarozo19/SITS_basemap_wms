###### execute force dynamically ##########
### requirements: sudo apt-get install xterm
############################################
import subprocess
import time
import glob
from utils.utils import create_folder_structure, execute_cmd
from utils.force_class_utils import force_class, force_class_udf

#########################
### define parameters ###
#########################

base_path = '/mowing_detection'  # Base Path where ./process/ folder structure should be created
project_name = "brandemburg"                                                   # Project Name defined for vitalitat_3cities_data storage
force_dir = "/force:/force"                                             # Mount Point for Force-Datacube
local_dir = "/rvt_mount:/rvt_mount"                                     # Mount Point for local Drive
hold = True                                                           # if True, cmd must be closed manually - recommended for debugging FORCE

aois = glob.glob("/mowing_detection/process/data/BB_25833_3035.shp")      # Define multiple or single AOI-Shapefile

###################################################################
### check / create folder structure and create process skeleton ###
###################################################################

#create_folder_structure(base_path)
#force_class(project_name, force_dir, local_dir, base_path, aois, hold)
force_class_udf(project_name, force_dir, local_dir, base_path, aois, hold)


##############################
### execute parameter file ###
##############################
params_path = "/mowing_detection/process/temp/brandemburg/FORCE/BB_25833_3035.shp/tsa.prm"
execute_cmd(params_path, hold, local_dir, force_dir)
