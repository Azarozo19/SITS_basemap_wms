import os
import subprocess
import time
import shutil
import geopandas as gpd

def generate_input_feature_line(tif_path, num_layers):
    sequence = ' '.join(str(i) for i in range(1, num_layers + 1))
    return f"INPUT_FEATURE = {tif_path} {sequence}"


def replace_parameters(filename, replacements):
    with open(filename, 'r') as f:
        content = f.read()
        for key, value in replacements.items():
            content = content.replace(key, value)
    with open(filename, 'w') as f:
        f.write(content)

def extract_coordinates(file_path):
    with open(file_path, 'r') as f:
        lines = f.readlines()
    #Skip the first line
    lines = lines[1:]
    #Extract X and Y values
    x_values = [int(line.split('_')[0][1:]) for line in lines]
    y_values = [int(line.split('_')[1][1:]) for line in lines]
    #Extract the desired values
    x_str = f"{min(x_values)} {max(x_values)}"
    y_str = f"{min(y_values)} {max(y_values)}"

    return x_str, y_str

def check_and_reproject_shapefile(shapefile_path, target_epsg=3035):
    # Load the shapefile
    gdf = gpd.read_file(shapefile_path)
    # Check the current CRS of the shapefile
    if gdf.crs.to_epsg() != target_epsg:
        print("Reprojecting shapefile to EPSG: 3035")
        # Reproject the shapefile
        gdf = gdf.to_crs(epsg=target_epsg)
        # Define the new file path
        new_shapefile_path = shapefile_path.replace(".shp", "_3035.shp")
        # Save the reprojected shapefile
        gdf.to_file(new_shapefile_path, driver='ESRI Shapefile')
        print(f"Shapefile reprojected and saved to {new_shapefile_path}")
        return new_shapefile_path
    else:
        print("Shapefile is already in EPSG: 3035")
        return shapefile_path

def force_class(project_name, force_dir, local_dir, base_path, aois, hold):
    #defining parameters outsourced from main script

    #subprocess.run(['sudo', 'chmod', '-R', '777', f"{Path(temp_folder).parent}"])
    #subprocess.run(['sudo', 'chmod', '-R', '777', f"{Path(scripts_skel).parent}"])
    base_path_script = os.getcwd()
    startzeit = time.time()
    for aoi in aois:
        print(f"FORCE PROCESSING FOR {aoi}")

        basename = os.path.basename(aoi)
        aoi = check_and_reproject_shapefile(aoi)

        ### get force extend
        os.makedirs(f'{base_path}/process/temp/{project_name}/FORCE/{basename}', exist_ok=True)

        #subprocess.run(['sudo', 'chmod', '-R', '777', f"{temp_folder}/{project_name}/FORCE/{basename}"])

        shutil.copy(f"{base_path_script}/utils/skel/force_cube_sceleton/datacube-definition.prj",f"{base_path}/process/temp/{project_name}/FORCE/{basename}/datacube-definition.prj")

        cmd = f'sudo docker run -v {local_dir} -v {force_dir} -u "$(id -u):$(id -g)" davidfrantz/force:latest ' \
               f'force-tile-extent {aoi} -d {base_path_script}/utils/skel/force_cube_sceleton -a {base_path}/process/temp/{project_name}/FORCE/{basename}/tile_extent.txt'

        if hold == True:
            subprocess.run(['xterm','-hold','-e', cmd])
        else:
            subprocess.run(['xterm', '-e', cmd])

        #subprocess.run(['sudo','chmod','-R','777',f"{temp_folder}/{project_name}/FORCE/{basename}"])

        ### mask
        os.makedirs(f"{base_path}/process/temp/_mask/{project_name}/{basename}", exist_ok=True)

        #subprocess.run(['sudo', 'chmod', '-R', '777', f"{mask_folder}"])

        shutil.copy(f"{base_path_script}/utils/skel/force_cube_sceleton/datacube-definition.prj",f"{base_path}/process/temp/_mask/{project_name}/{basename}/datacube-definition.prj")
        cmd = f'sudo docker run -v {local_dir} -u "$(id -u):$(id -g)" davidfrantz/force:latest ' \
              f"force-cube -o {base_path}/process/temp/_mask/{project_name}/{basename} " \
              f"{aoi}"

        if hold == True:
            subprocess.run(['xterm','-hold','-e', cmd])
        else:
            subprocess.run(['xterm', '-e', cmd])
        #subprocess.run(['sudo','chmod','-R','777',f"{mask_folder}/{project_name}/{basename}"])

        ###mask mosaic
        cmd = f'sudo docker run -v {local_dir} -u "$(id -u):$(id -g)" davidfrantz/force:latest ' \
              f"force-mosaic {base_path}/process/temp/_mask/{project_name}/{basename}"

        if hold == True:
            subprocess.run(['xterm','-hold','-e', cmd])
        else:
            subprocess.run(['xterm', '-e', cmd])

        #subprocess.run(['sudo','chmod','-R','777',f"{temp_folder}/{project_name}/FORCE/{basename}"])

        ###force param

        os.makedirs(f"{base_path}/process/temp/{project_name}/FORCE/{basename}/provenance", exist_ok=True)
        os.makedirs(f"{base_path}/process/temp/{project_name}/FORCE/{basename}/tiles_tss", exist_ok=True)

        shutil.copy(f"{base_path_script}/utils/skel/force_cube_sceleton/datacube-definition.prj",f"{base_path}/process/temp/{project_name}/FORCE/{basename}/datacube-definition.prj")
        shutil.copy(f"{base_path_script}/utils/skel/force_cube_sceleton/datacube-definition.prj",f"{base_path}/process/temp/{project_name}/FORCE/{basename}/tiles_tss/datacube-definition.prj")

        tsa_param = f"{base_path}/process/temp/{project_name}/FORCE/{basename}/tsa.prm"
        param_cmd = (
            f'sudo docker run -v {local_dir} -v {force_dir} -u "$(id -u):$(id -g)" '
            f'davidfrantz/force:latest force-parameter {tsa_param} TSA'
        )
        subprocess.run(['bash', '-lc', param_cmd], check=True)

        X_TILE_RANGE, Y_TILE_RANGE = extract_coordinates(f"{base_path}/process/temp/{project_name}/FORCE/{basename}/tile_extent.txt")
        # Define replacements
        replacements = {
            # INPUT/OUTPUT DIRECTORIES
            f'DIR_LOWER = NULL':f'DIR_LOWER = {force_dir.split(":")[0]}/FORCE/C1/L2/ard',
            f'DIR_HIGHER = NULL':f'DIR_HIGHER = {base_path}/process/temp/{project_name}/FORCE/{basename}/tiles_tss',
            f'DIR_PROVENANCE = NULL':f'DIR_PROVENANCE = {base_path}/process/temp/{project_name}/FORCE/{basename}/provenance',
            # MASKING
            f'DIR_MASK = NULL':f'DIR_MASK = {base_path}/process/temp/_mask/{project_name}/{basename}',
            f'BASE_MASK = NULL':f'BASE_MASK = {os.path.basename(aoi).replace(".shp",".tif")}',
            # PROCESSING EXTENT AND RESOLUTION
            f'X_TILE_RANGE = 0 0': f'X_TILE_RANGE = {X_TILE_RANGE}',
            f'Y_TILE_RANGE = 0 0': f'Y_TILE_RANGE = {Y_TILE_RANGE}',
            f"STREAMING = TRUE" : f"STREAMING = FALSE",
            f"OUTPUT_SUBFOLDERS = FALSE": f"OUTPUT_SUBFOLDERS = TRUE",
            f"CHUNK_SIZE = 0 0": f"CHUNK_SIZE = 1000 1000",
            f"ABOVE_NOISE = 0": f"ABOVE_NOISE = 3",
            f"BELOW_NOISE = 0": f"BELOW_NOISE = 1",
            f"OUTPUT_TSS = FALSE": f"OUTPUT_TSS = TRUE",
        }
        # Replace parameters in the file
        replace_parameters(f"{base_path}/process/temp/{project_name}/FORCE/{basename}/tsa.prm", replacements)


    endzeit = time.time()
    print("FORCE-Processing beendet nach "+str((endzeit-startzeit)/60)+" Minuten")


def force_class_udf(
    project_name,
    force_dir,
    local_dir,
    base_path,
    aois,
    hold,
    udf_source="utils/skel/udf_rgb_raw_block.py",
    python_type="CHUNK",
):
    # defining parameters outsourced from main script

    # subprocess.run(['sudo', 'chmod', '-R', '777', f"{Path(temp_folder).parent}"])
    # subprocess.run(['sudo', 'chmod', '-R', '777', f"{Path(scripts_skel).parent}"])
    base_path_script = os.getcwd()
    if os.path.isabs(udf_source):
        udf_source_path = udf_source
    else:
        udf_source_path = os.path.join(base_path_script, udf_source)

    udf_target_name = os.path.basename(udf_source_path)
    startzeit = time.time()
    for aoi in aois:
        print(f"FORCE PROCESSING FOR {aoi}")

        basename = os.path.basename(aoi)
        print(f"Checking AOI path: {aoi}")
        if not os.path.exists(aoi):
            print(f"Error: AOI path does not exist -> {aoi}")
        aoi = check_and_reproject_shapefile(aoi)
        print(f"Reprojected AOI path: {aoi}")



        ### get force extend
        os.makedirs(f'{base_path}/process/temp/{project_name}/FORCE/{basename}', exist_ok=True)

        # subprocess.run(['sudo', 'chmod', '-R', '777', f"{temp_folder}/{project_name}/FORCE/{basename}"])

        shutil.copy(f"{base_path_script}/utils/skel/force_cube_sceleton/datacube-definition.prj",
                    f"{base_path}/process/temp/{project_name}/FORCE/{basename}/datacube-definition.prj")

        print(f"Checking AOI path: {aoi} -> Exists: {os.path.exists(aoi)}")

        cmd = f'sudo docker run -v {local_dir} -v {force_dir} -u "$(id -u):$(id -g)" davidfrantz/force:latest ' \
              f'force-tile-extent  {aoi} -d {base_path_script}/utils/skel/force_cube_sceleton -a {base_path}/process/temp/{project_name}/FORCE/{basename}/tile_extent.txt'

        if hold == True:
            subprocess.run(['xterm', '-hold', '-e', cmd])
        else:
            subprocess.run(['xterm', '-e', cmd])

        # subprocess.run(['sudo','chmod','-R','777',f"{temp_folder}/{project_name}/FORCE/{basename}"])

        ### mask
        os.makedirs(f"{base_path}/process/temp/_mask/{project_name}/{basename}", exist_ok=True)

        # subprocess.run(['sudo', 'chmod', '-R', '777', f"{mask_folder}"])

        shutil.copy(f"{base_path_script}/utils/skel/force_cube_sceleton/datacube-definition.prj",
                    f"{base_path}/process/temp/_mask/{project_name}/{basename}/datacube-definition.prj")
        cmd = f'sudo docker run -v {local_dir} -u "$(id -u):$(id -g)" davidfrantz/force:latest ' \
              f"force-cube -o {base_path}/process/temp/_mask/{project_name}/{basename} " \
              f"{aoi}"

        if hold == True:
            subprocess.run(['xterm', '-hold', '-e', cmd])
        else:
            subprocess.run(['xterm', '-e', cmd])
        # subprocess.run(['sudo','chmod','-R','777',f"{mask_folder}/{project_name}/{basename}"])

        ###mask mosaic
        cmd = f'sudo docker run -v {local_dir} -u "$(id -u):$(id -g)" davidfrantz/force:latest ' \
              f"force-mosaic {base_path}/process/temp/_mask/{project_name}/{basename}"

        if hold == True:
            subprocess.run(['xterm', '-hold', '-e', cmd])
        else:
            subprocess.run(['xterm', '-e', cmd])

        # subprocess.run(['sudo','chmod','-R','777',f"{temp_folder}/{project_name}/FORCE/{basename}"])

        ###force param

        os.makedirs(f"{base_path}/process/temp/{project_name}/FORCE/{basename}/provenance", exist_ok=True)
        os.makedirs(f"{base_path}/process/temp/{project_name}/FORCE/{basename}/tiles_tss", exist_ok=True)

        shutil.copy(f"{base_path_script}/utils/skel/force_cube_sceleton/datacube-definition.prj",
                    f"{base_path}/process/temp/{project_name}/FORCE/{basename}/datacube-definition.prj")
        shutil.copy(f"{base_path_script}/utils/skel/force_cube_sceleton/datacube-definition.prj",
                    f"{base_path}/process/temp/{project_name}/FORCE/{basename}/tiles_tss/datacube-definition.prj")
        shutil.copy(udf_source_path,
                    f"{base_path}/process/temp/{project_name}/FORCE/{basename}/{udf_target_name}")

        tsa_param = f"{base_path}/process/temp/{project_name}/FORCE/{basename}/tsa_UDF.prm"
        param_cmd = (
            f'sudo docker run -v {local_dir} -v {force_dir} -u "$(id -u):$(id -g)" '
            f'davidfrantz/force:latest force-parameter {tsa_param} UDF'
        )
        subprocess.run(['bash', '-lc', param_cmd], check=True)

        X_TILE_RANGE, Y_TILE_RANGE = extract_coordinates(f"{base_path}/process/temp/{project_name}/FORCE/{basename}/tile_extent.txt")

        # Define replacements
        replacements = {
            # INPUT/OUTPUT DIRECTORIES
            f'DIR_LOWER = NULL': f'DIR_LOWER = {force_dir.split(":")[0]}/FORCE/C1/L2/ard',
            f'DIR_HIGHER = NULL': f'DIR_HIGHER = {base_path}/process/temp/{project_name}/FORCE/{basename}/tiles_tss',
            f'DIR_PROVENANCE = NULL': f'DIR_PROVENANCE = {base_path}/process/temp/{project_name}/FORCE/{basename}/provenance',
            # MASKING
            f'DIR_MASK = NULL': f'DIR_MASK = {base_path}/process/temp/_mask/{project_name}/{basename}',
            f'BASE_MASK = NULL': f'BASE_MASK = {os.path.basename(aoi).replace(".shp", ".tif")}',
            # PROCESSING EXTENT AND RESOLUTION
            f'X_TILE_RANGE = 0 0': f'X_TILE_RANGE = {X_TILE_RANGE}',
            f'Y_TILE_RANGE = 0 0': f'Y_TILE_RANGE = {Y_TILE_RANGE}',
            f"STREAMING = TRUE": f"STREAMING = FALSE",
            f"OUTPUT_SUBFOLDERS = FALSE": f"OUTPUT_SUBFOLDERS = TRUE",
            f"CHUNK_SIZE = 0 0": f"CHUNK_SIZE = 100 100",
            f"ABOVE_NOISE = 0": f"ABOVE_NOISE = 3",
            f"BELOW_NOISE = 0": f"BELOW_NOISE = 1",
            f"FILE_PYTHON = NULL": f"FILE_PYTHON = {base_path}/process/temp/{project_name}/FORCE/{basename}/{udf_target_name}",
            f"PYTHON_TYPE = PIXEL": f"PYTHON_TYPE = {python_type}",
            f"OUTPUT_PYP = FALSE": f"OUTPUT_PYP = TRUE",
        }
        # Replace parameters in the file
        replace_parameters(f"{base_path}/process/temp/{project_name}/FORCE/{basename}/tsa_UDF.prm", replacements)

    endzeit = time.time()
    print("FORCE-Processing beendet nach " + str((endzeit - startzeit) / 60) + " Minuten")
