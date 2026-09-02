import os
import shutil
import subprocess
import time
from pathlib import Path


DEFAULT_FORCE_IMAGE = "davidfrantz/force:3.9.02"
DEFAULT_CHUNK_SIZE = (1000, 1000)
DEFAULT_RESOLUTION = 10
DEFAULT_SENSORS = ("SEN2A", "SEN2B", "SEN2C")
DEFAULT_TARGET_SENSOR = "SEN2L"


def generate_input_feature_line(tif_path, num_layers):
    sequence = " ".join(str(i) for i in range(1, num_layers + 1))
    return f"INPUT_FEATURE = {tif_path} {sequence}"


def set_force_parameters(filename, parameters):
    """Update FORCE tag/value parameters and fail on missing or duplicate tags."""
    path = Path(filename)
    lines = path.read_text().splitlines(keepends=True)
    remaining = set(parameters)
    seen = set()
    updated = []

    for line in lines:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            updated.append(line)
            continue

        tag = stripped.split("=", 1)[0].strip()
        if tag not in parameters:
            updated.append(line)
            continue
        if tag in seen:
            raise ValueError(f"Duplicate FORCE parameter '{tag}' in {path}")

        indentation = line[: len(line) - len(stripped)]
        newline = "\n" if line.endswith("\n") else ""
        updated.append(f"{indentation}{tag} = {parameters[tag]}{newline}")
        seen.add(tag)
        remaining.discard(tag)

    if remaining:
        missing = ", ".join(sorted(remaining))
        raise ValueError(f"Missing FORCE parameter(s) in {path}: {missing}")

    path.write_text("".join(updated))


def replace_parameters(filename, replacements):
    """Backward-compatible exact replacement with validation."""
    path = Path(filename)
    content = path.read_text()
    missing = [key for key in replacements if key not in content]
    if missing:
        raise ValueError(f"Replacement text not found in {path}: {missing}")
    for key, value in replacements.items():
        content = content.replace(key, value)
    path.write_text(content)


def extract_coordinates(file_path):
    lines = Path(file_path).read_text().splitlines()[1:]
    tile_lines = [line.strip() for line in lines if line.strip()]
    if not tile_lines:
        raise ValueError(f"FORCE tile extent contains no tiles: {file_path}")

    try:
        x_values = [int(line.split("_")[0][1:]) for line in tile_lines]
        y_values = [int(line.split("_")[1][1:]) for line in tile_lines]
    except (IndexError, ValueError) as exc:
        raise ValueError(f"Invalid FORCE tile extent format: {file_path}") from exc

    return f"{min(x_values)} {max(x_values)}", f"{min(y_values)} {max(y_values)}"


def check_and_reproject_shapefile(shapefile_path, target_epsg=3035):
    import geopandas as gpd

    gdf = gpd.read_file(shapefile_path)
    if gdf.crs is None:
        raise ValueError(f"AOI has no CRS: {shapefile_path}")
    if gdf.crs.to_epsg() == target_epsg:
        print(f"Shapefile is already in EPSG:{target_epsg}")
        return shapefile_path

    print(f"Reprojecting shapefile to EPSG:{target_epsg}")
    gdf = gdf.to_crs(epsg=target_epsg)
    source = Path(shapefile_path)
    output = source.with_name(f"{source.stem}_{target_epsg}.shp")
    gdf.to_file(output, driver="ESRI Shapefile")
    print(f"Shapefile reprojected and saved to {output}")
    return str(output)


def _split_mount_spec(mount_spec):
    parts = str(mount_spec).split(":")
    host_root = Path(parts[0]).resolve()
    container_root = Path(parts[1] if len(parts) >= 2 else parts[0])
    return host_root, container_root


def _container_mount_path(mount_spec):
    return str(_split_mount_spec(mount_spec)[1])


def _mount_host_root(mount_spec):
    return _split_mount_spec(mount_spec)[0]


def containerize_path(path, mount_specs):
    """Translate a host path to its path inside the most-specific Docker mount."""
    specs = [mount_specs] if isinstance(mount_specs, str) else list(mount_specs)
    resolved = Path(path).resolve()
    matches = []
    for mount_spec in specs:
        host_root, container_root = _split_mount_spec(mount_spec)
        try:
            relative = resolved.relative_to(host_root)
        except ValueError:
            continue
        matches.append((len(host_root.parts), container_root / relative))
    if not matches:
        raise ValueError(f"Host path is not covered by a Docker mount: {resolved}")
    return str(max(matches, key=lambda item: item[0])[1])


def _path_is_mounted(path, mount_specs):
    resolved = Path(path).resolve()
    for mount_spec in mount_specs:
        try:
            resolved.relative_to(_mount_host_root(mount_spec))
            return True
        except ValueError:
            continue
    return False


def _required_local_mounts(local_dir, required_paths):
    mount_specs = [local_dir] if isinstance(local_dir, str) else list(local_dir)
    for required_path in required_paths:
        resolved = Path(required_path).resolve()
        if not _path_is_mounted(resolved, mount_specs):
            mount_specs.append(f"{resolved}:{resolved}:ro")
    return mount_specs


def _docker_command(image, local_dir, force_dir, force_args, use_sudo):
    cmd = []
    if use_sudo:
        cmd.append("sudo")
    cmd.extend(["docker", "run", "--rm"])
    local_mounts = [local_dir] if isinstance(local_dir, str) else local_dir
    for mount_spec in local_mounts:
        cmd.extend(["-v", mount_spec])
    if force_dir:
        cmd.extend(["-v", force_dir])
    cmd.extend(["-u", f"{os.getuid()}:{os.getgid()}", image])
    cmd.extend(str(arg) for arg in force_args)
    return cmd


def _run_docker_force(
    image,
    local_dir,
    force_dir,
    force_args,
    hold=False,
    use_sudo=True,
):
    cmd = _docker_command(image, local_dir, force_dir, force_args, use_sudo)
    print("Running command:")
    print(" ".join(cmd))
    if hold:
        subprocess.run(["xterm", "-hold", "-e", *cmd], check=True)
    else:
        subprocess.run(cmd, check=True)


def _validate_processing_options(chunk_size, resolution, tile_size=30000):
    if len(chunk_size) != 2 or any(value <= 0 for value in chunk_size):
        raise ValueError("chunk_size must contain two positive values")
    if resolution <= 0:
        raise ValueError("resolution must be positive")
    for axis, value in zip(("X", "Y"), chunk_size):
        if value % resolution:
            raise ValueError(f"CHUNK_SIZE {axis}={value} is not divisible by RESOLUTION={resolution}")
        if tile_size % value:
            raise ValueError(f"TILE_SIZE={tile_size} is not divisible by CHUNK_SIZE {axis}={value}")


def _prepare_force_job(
    project_name,
    force_dir,
    local_dir,
    base_path,
    aoi,
    hold,
    module,
    param_name,
    force_image,
    chunk_size,
    resolution,
    sensors,
    target_sensor,
    date_range,
    above_noise,
    below_noise,
    nthread_read,
    nthread_compute,
    nthread_write,
    use_tile_allowlist,
    use_sudo,
    udf_source_path=None,
    python_type="CHUNK",
):
    _validate_processing_options(chunk_size, resolution)
    base_path_script = Path.cwd()
    skeleton_dir = base_path_script / "utils" / "skel" / "force_cube_sceleton"
    datacube_definition = skeleton_dir / "datacube-definition.prj"

    original_basename = Path(aoi).name
    print(f"FORCE PREPARATION FOR {aoi}")
    if not Path(aoi).exists():
        raise FileNotFoundError(f"AOI path does not exist: {aoi}")
    prepared_aoi = check_and_reproject_shapefile(aoi)
    docker_mounts = _required_local_mounts(
        local_dir,
        (base_path_script, Path(prepared_aoi).parent),
    )

    job_root = Path(base_path) / "process" / "temp" / project_name / "FORCE" / original_basename
    mask_root = Path(base_path) / "process" / "temp" / "_mask" / project_name / original_basename
    provenance_root = job_root / "provenance"
    tiles_root = job_root / "tiles_tss"
    for directory in (job_root, mask_root, provenance_root, tiles_root):
        directory.mkdir(parents=True, exist_ok=True)

    container_path = lambda path: containerize_path(path, docker_mounts)

    shutil.copy(datacube_definition, job_root / "datacube-definition.prj")
    shutil.copy(datacube_definition, mask_root / "datacube-definition.prj")
    shutil.copy(datacube_definition, tiles_root / "datacube-definition.prj")

    tile_extent_path = job_root / "tile_extent.txt"
    _run_docker_force(
        force_image,
        docker_mounts,
        force_dir,
        [
            "force-tile-extent",
            container_path(prepared_aoi),
            "-d",
            container_path(skeleton_dir),
            "-a",
            container_path(tile_extent_path),
        ],
        hold=hold,
        use_sudo=use_sudo,
    )
    _run_docker_force(
        force_image,
        docker_mounts,
        None,
        ["force-cube", "-o", container_path(mask_root), container_path(prepared_aoi)],
        hold=hold,
        use_sudo=use_sudo,
    )
    _run_docker_force(
        force_image,
        docker_mounts,
        None,
        ["force-mosaic", container_path(mask_root)],
        hold=hold,
        use_sudo=use_sudo,
    )

    udf_target_path = None
    if udf_source_path is not None:
        udf_target_path = job_root / Path(udf_source_path).name
        shutil.copy(udf_source_path, udf_target_path)

    params_path = job_root / param_name
    _run_docker_force(
        force_image,
        docker_mounts,
        force_dir,
        ["force-parameter", container_path(params_path), module],
        use_sudo=use_sudo,
    )

    x_tile_range, y_tile_range = extract_coordinates(tile_extent_path)
    parameters = {
        "DIR_LOWER": str(Path(_container_mount_path(force_dir)) / "C1" / "L2" / "ard"),
        "DIR_HIGHER": container_path(tiles_root),
        "DIR_PROVENANCE": container_path(provenance_root),
        "DIR_MASK": container_path(mask_root),
        "BASE_MASK": Path(prepared_aoi).with_suffix(".tif").name,
        "X_TILE_RANGE": x_tile_range,
        "Y_TILE_RANGE": y_tile_range,
        "FILE_TILE": container_path(tile_extent_path) if use_tile_allowlist else "NULL",
        "NTHREAD_READ": str(nthread_read),
        "NTHREAD_COMPUTE": str(nthread_compute),
        "NTHREAD_WRITE": str(nthread_write),
        "STREAMING": "FALSE",
        "OUTPUT_SUBFOLDERS": "TRUE",
        "CHUNK_SIZE": f"{chunk_size[0]} {chunk_size[1]}",
        "RESOLUTION": str(resolution),
        "SENSORS": " ".join(sensors),
        "TARGET_SENSOR": target_sensor,
        "ABOVE_NOISE": str(above_noise),
        "BELOW_NOISE": str(below_noise),
    }
    if date_range is not None:
        parameters["DATE_RANGE"] = " ".join(date_range)

    if module == "UDF":
        parameters.update(
            {
                "FILE_PYTHON": container_path(udf_target_path),
                "PYTHON_TYPE": python_type,
                "OUTPUT_PYP": "TRUE",
            }
        )
    else:
        parameters["OUTPUT_TSS"] = "TRUE"

    set_force_parameters(params_path, parameters)
    print(f"Prepared validated FORCE parameters: {params_path}")
    return params_path


def force_class(
    project_name,
    force_dir,
    local_dir,
    base_path,
    aois,
    hold,
    *,
    force_image=DEFAULT_FORCE_IMAGE,
    chunk_size=DEFAULT_CHUNK_SIZE,
    resolution=DEFAULT_RESOLUTION,
    sensors=DEFAULT_SENSORS,
    target_sensor=DEFAULT_TARGET_SENSOR,
    date_range=None,
    above_noise=0,
    below_noise=0,
    nthread_read=8,
    nthread_compute=22,
    nthread_write=4,
    use_tile_allowlist=True,
    use_sudo=True,
):
    start_time = time.time()
    for aoi in aois:
        _prepare_force_job(
            project_name,
            force_dir,
            local_dir,
            base_path,
            aoi,
            hold,
            "TSA",
            "tsa.prm",
            force_image,
            chunk_size,
            resolution,
            sensors,
            target_sensor,
            date_range,
            above_noise,
            below_noise,
            nthread_read,
            nthread_compute,
            nthread_write,
            use_tile_allowlist,
            use_sudo,
        )
    print(f"FORCE preparation finished after {(time.time() - start_time) / 60:.2f} minutes")


def force_class_udf(
    project_name,
    force_dir,
    local_dir,
    base_path,
    aois,
    hold,
    udf_source="utils/skel/udf_rgb_raw_block.py",
    python_type="CHUNK",
    *,
    force_image=DEFAULT_FORCE_IMAGE,
    chunk_size=DEFAULT_CHUNK_SIZE,
    resolution=DEFAULT_RESOLUTION,
    sensors=DEFAULT_SENSORS,
    target_sensor=DEFAULT_TARGET_SENSOR,
    date_range=None,
    above_noise=0,
    below_noise=0,
    nthread_read=8,
    nthread_compute=22,
    nthread_write=4,
    use_tile_allowlist=True,
    use_sudo=True,
):
    udf_source_path = Path(udf_source)
    if not udf_source_path.is_absolute():
        udf_source_path = Path.cwd() / udf_source_path
    if not udf_source_path.exists():
        raise FileNotFoundError(f"UDF source does not exist: {udf_source_path}")

    start_time = time.time()
    for aoi in aois:
        _prepare_force_job(
            project_name,
            force_dir,
            local_dir,
            base_path,
            aoi,
            hold,
            "UDF",
            "tsa_UDF.prm",
            force_image,
            chunk_size,
            resolution,
            sensors,
            target_sensor,
            date_range,
            above_noise,
            below_noise,
            nthread_read,
            nthread_compute,
            nthread_write,
            use_tile_allowlist,
            use_sudo,
            udf_source_path=udf_source_path,
            python_type=python_type,
        )
    print(f"FORCE preparation finished after {(time.time() - start_time) / 60:.2f} minutes")
