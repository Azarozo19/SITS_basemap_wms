"""Safe tile-level resume support for FORCE higher-level jobs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import rasterio

from utils.force_class_utils import extract_coordinates, set_force_parameters


STATE_SCHEMA_VERSION = 1
TILE_ID_PATTERN = re.compile(r"^X-?\d+_Y-?\d+$")


def parse_force_parameters(params_path: Path) -> dict[str, str]:
    """Read active (non-comment) FORCE parameters and reject duplicates."""
    parameters: dict[str, str] = {}
    for line in Path(params_path).read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = (part.strip() for part in stripped.split("=", 1))
        if key in parameters:
            raise ValueError(f"Duplicate FORCE parameter '{key}' in {params_path}")
        parameters[key] = value
    return parameters


def read_tile_allowlist(tile_extent_path: Path) -> list[str]:
    """Read FORCE's count-prefixed tile allow-list."""
    path = Path(tile_extent_path)
    lines = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"FORCE tile allow-list is empty: {path}")
    try:
        declared_count = int(lines[0])
    except ValueError as exc:
        raise ValueError(f"Invalid tile count in FORCE allow-list: {path}") from exc

    tile_ids = lines[1:]
    if declared_count != len(tile_ids):
        raise ValueError(
            f"FORCE tile allow-list count mismatch in {path}: "
            f"header says {declared_count}, found {len(tile_ids)}"
        )
    invalid = [tile_id for tile_id in tile_ids if not TILE_ID_PATTERN.fullmatch(tile_id)]
    if invalid:
        raise ValueError(f"Invalid FORCE tile identifier(s) in {path}: {invalid}")
    if len(tile_ids) != len(set(tile_ids)):
        raise ValueError(f"Duplicate tile identifiers in FORCE allow-list: {path}")
    return tile_ids


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_force_fingerprint(
    params_path: Path,
    tile_ids: list[str],
    force_image: str,
    raw_suffix: str,
) -> tuple[str, dict]:
    """Fingerprint all inputs that can materially change FORCE tile outputs."""
    params_path = Path(params_path)
    parameters = parse_force_parameters(params_path)
    components: dict[str, object] = {
        "force_image": force_image,
        "raw_suffix": raw_suffix,
        "parameters_sha256": _sha256_file(params_path),
        "tile_ids": tile_ids,
    }

    udf_path = parameters.get("FILE_PYTHON")
    if udf_path and udf_path != "NULL":
        udf_file = Path(udf_path)
        if not udf_file.exists():
            copied_udf = params_path.parent / udf_file.name
            if copied_udf.exists():
                udf_file = copied_udf
            else:
                raise FileNotFoundError(f"Configured FORCE UDF does not exist: {udf_file}")
        components["udf_sha256"] = _sha256_file(udf_file)

    cube_definition = params_path.parent / "datacube-definition.prj"
    if cube_definition.exists():
        components["datacube_definition_sha256"] = _sha256_file(cube_definition)

    encoded = json.dumps(components, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest(), components


def validate_force_raster(
    raster_path: Path,
    *,
    validation_mode: str = "full",
    expected_resolution: float | None = None,
) -> tuple[bool, str | None]:
    """Validate metadata and, in full mode, decode every raster block."""
    if validation_mode not in {"metadata", "full"}:
        raise ValueError(f"Unsupported FORCE validation mode: {validation_mode}")
    path = Path(raster_path)
    try:
        with rasterio.open(path) as src:
            if src.driver != "GTiff":
                return False, f"unexpected driver {src.driver!r}"
            if src.width <= 0 or src.height <= 0 or src.count <= 0:
                return False, "raster has no pixels or bands"
            if src.crs is None:
                return False, "raster has no CRS"
            if expected_resolution is not None:
                x_resolution, y_resolution = (abs(value) for value in src.res)
                tolerance = max(abs(expected_resolution) * 1e-6, 1e-9)
                if (
                    abs(x_resolution - expected_resolution) > tolerance
                    or abs(y_resolution - expected_resolution) > tolerance
                ):
                    return (
                        False,
                        f"resolution is {src.res}, expected {expected_resolution}",
                    )
            if validation_mode == "full":
                for band_index in src.indexes:
                    for _, window in src.block_windows(band_index):
                        src.read(band_index, window=window)
    except Exception as exc:  # Rasterio/GDAL exposes several exception subclasses.
        return False, f"{type(exc).__name__}: {exc}"
    return True, None


def inspect_force_outputs(
    raw_tiles_root: Path,
    tile_ids: list[str],
    raw_suffix: str,
    *,
    validation_mode: str = "full",
    expected_resolution: float | None = None,
) -> dict:
    """Classify required FORCE tiles from the files currently on disk."""
    raw_root = Path(raw_tiles_root)
    complete: list[str] = []
    missing: list[str] = []
    corrupt: dict[str, list[dict[str, str]]] = {}
    outputs: dict[str, list[str]] = {}

    for tile_id in tile_ids:
        matches = sorted(
            path
            for path in (raw_root / tile_id).rglob(f"*{raw_suffix}")
            if path.is_file()
        )
        outputs[tile_id] = [str(path) for path in matches]
        if not matches:
            missing.append(tile_id)
            continue

        failures: list[dict[str, str]] = []
        for path in matches:
            valid, reason = validate_force_raster(
                path,
                validation_mode=validation_mode,
                expected_resolution=expected_resolution,
            )
            if not valid:
                failures.append({"path": str(path), "reason": reason or "unknown validation error"})
        if failures:
            corrupt[tile_id] = failures
        else:
            complete.append(tile_id)

    return {
        "complete": complete,
        "missing": missing,
        "corrupt": corrupt,
        "outputs": outputs,
    }


def quarantine_corrupt_tiles(job_root: Path, scan: dict) -> Path | None:
    """Move all matching outputs for corrupt tiles aside so FORCE gets clean targets."""
    if not scan["corrupt"]:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    quarantine_root = Path(job_root) / "resume_quarantine" / stamp
    counter = 1
    while quarantine_root.exists():
        quarantine_root = quarantine_root.with_name(f"{stamp}_{counter}")
        counter += 1

    raw_root = Path(job_root) / "tiles_tss"
    for tile_id in scan["corrupt"]:
        for source_text in scan["outputs"].get(tile_id, []):
            source = Path(source_text)
            relative = source.relative_to(raw_root)
            target = quarantine_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
    return quarantine_root


def write_tile_allowlist(path: Path, tile_ids: list[str]) -> None:
    path = Path(path)
    path.write_text(f"{len(tile_ids)}\n" + "".join(f"{tile_id}\n" for tile_id in tile_ids))


def create_resume_parameters(
    source_params_path: Path,
    resume_params_path: Path,
    resume_allowlist_path: Path,
    tile_ids: list[str],
) -> None:
    if not tile_ids:
        raise ValueError("Cannot create FORCE resume parameters for an empty tile list")
    shutil.copy2(source_params_path, resume_params_path)
    x_tile_range, y_tile_range = extract_coordinates(resume_allowlist_path)
    source_parameters = parse_force_parameters(source_params_path)
    configured_allowlist = source_parameters.get("FILE_TILE")
    if configured_allowlist and configured_allowlist != "NULL":
        resume_allowlist_for_force = str(
            Path(configured_allowlist).with_name(Path(resume_allowlist_path).name)
        )
    else:
        resume_allowlist_for_force = str(resume_allowlist_path)

    set_force_parameters(
        resume_params_path,
        {
            "FILE_TILE": resume_allowlist_for_force,
            "X_TILE_RANGE": x_tile_range,
            "Y_TILE_RANGE": y_tile_range,
        },
    )


def load_resume_state(state_path: Path) -> dict | None:
    path = Path(state_path)
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read FORCE resume state {path}: {exc}") from exc
    if state.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError(f"Unsupported FORCE resume state schema in {path}")
    return state


def write_resume_state(state_path: Path, state: dict) -> None:
    """Atomically replace state so an interruption cannot leave partial JSON."""
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w") as output:
        output.write(json.dumps(state, indent=2, sort_keys=True) + "\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def resolve_tile_allowlist(params_path: Path) -> tuple[Path, list[str]]:
    parameters = parse_force_parameters(params_path)
    configured = parameters.get("FILE_TILE")
    if not configured or configured == "NULL":
        raise RuntimeError(
            "Automatic FORCE resume requires FILE_TILE. Re-run preparation without "
            "--no-tile-allowlist, or use --no-resume-force for the legacy full run."
        )
    allowlist_path = Path(configured)
    if not allowlist_path.exists():
        fallback = Path(params_path).parent / "tile_extent.txt"
        if fallback.exists():
            allowlist_path = fallback
        else:
            raise FileNotFoundError(f"Configured FORCE tile allow-list does not exist: {configured}")
    return allowlist_path, read_tile_allowlist(allowlist_path)


def prepare_force_resume(
    *,
    job_root: Path,
    params_path: Path,
    raw_tiles_root: Path,
    force_image: str,
    raw_suffix: str,
    validation_mode: str,
) -> dict:
    """Validate disk state and build a FORCE parameter file for unfinished tiles."""
    job_root = Path(job_root)
    params_path = Path(params_path)
    allowlist_path, tile_ids = resolve_tile_allowlist(params_path)
    fingerprint, fingerprint_components = build_force_fingerprint(
        params_path, tile_ids, force_image, raw_suffix
    )
    state_path = job_root / "force_resume_state.json"
    previous_state = load_resume_state(state_path)
    any_existing_tifs = any(Path(raw_tiles_root).rglob("*.tif"))
    if (
        previous_state
        and previous_state.get("fingerprint") != fingerprint
        and any_existing_tifs
    ):
        raise RuntimeError(
            f"FORCE resume fingerprint changed for {job_root.name}, but tile outputs already exist. "
            "Use a new --project-name or archive the existing job before running changed "
            "parameters, UDF code, image, or raw suffix."
        )

    interrupted_tiles = []
    if previous_state and previous_state.get("fingerprint") == fingerprint:
        if previous_state.get("status") == "running":
            interrupted_tiles = previous_state.get("active_tiles", [])
    interrupted_outputs: dict[str, list[str]] = {}
    for tile_id in interrupted_tiles:
        interrupted_outputs[tile_id] = [
            str(path)
            for path in sorted(
                path
                for path in (Path(raw_tiles_root) / tile_id).rglob(f"*{raw_suffix}")
                if path.is_file()
            )
        ]
    interrupted_scan = {
        "corrupt": {
            tile_id: [{"path": path, "reason": "FORCE was interrupted while this tile was active"}]
            for tile_id, paths in interrupted_outputs.items()
            for path in paths[:1]
        },
        "outputs": interrupted_outputs,
    }
    interrupted_quarantine = quarantine_corrupt_tiles(job_root, interrupted_scan)

    parameters = parse_force_parameters(params_path)
    expected_resolution = float(parameters["RESOLUTION"]) if "RESOLUTION" in parameters else None
    scan = inspect_force_outputs(
        raw_tiles_root,
        tile_ids,
        raw_suffix,
        validation_mode=validation_mode,
        expected_resolution=expected_resolution,
    )
    quarantine_path = quarantine_corrupt_tiles(job_root, scan)
    adoption_quarantine = None
    adoption_retried_tile = None
    if previous_state is None and any_existing_tifs and not scan["corrupt"] and scan["complete"]:
        newest_tile = max(
            scan["complete"],
            key=lambda tile_id: max(
                Path(path).stat().st_mtime_ns for path in scan["outputs"][tile_id]
            ),
        )
        adoption_scan = {
            "corrupt": {
                newest_tile: [
                    {
                        "path": scan["outputs"][newest_tile][0],
                        "reason": "newest output from a pre-checkpoint run is conservatively retried",
                    }
                ]
            },
            "outputs": {newest_tile: scan["outputs"][newest_tile]},
        }
        adoption_quarantine = quarantine_corrupt_tiles(job_root, adoption_scan)
        scan["complete"].remove(newest_tile)
        scan["missing"].append(newest_tile)
        adoption_retried_tile = newest_tile
    quarantine_paths = [
        str(path)
        for path in (interrupted_quarantine, quarantine_path, adoption_quarantine)
        if path is not None
    ]
    remaining = scan["missing"] + list(scan["corrupt"])
    resume_allowlist_path = job_root / "tile_extent.resume.txt"
    resume_params_path = params_path.with_name(f"{params_path.stem}.resume{params_path.suffix}")
    if remaining:
        write_tile_allowlist(resume_allowlist_path, remaining)
        create_resume_parameters(
            params_path,
            resume_params_path,
            resume_allowlist_path,
            remaining,
        )

    state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "fingerprint": fingerprint,
        "fingerprint_components": fingerprint_components,
        "source_params_path": str(params_path),
        "source_allowlist_path": str(allowlist_path),
        "raw_suffix": raw_suffix,
        "validation_mode": validation_mode,
        "status": "complete" if not remaining else "pending",
        "required_tile_count": len(tile_ids),
        "complete_tiles": scan["complete"],
        "remaining_tiles": remaining,
        "active_tiles": [],
        "corrupt_tiles": scan["corrupt"],
        "quarantine_paths": quarantine_paths,
        "adopted_existing_outputs": previous_state is None and any_existing_tifs,
        "adoption_retried_tile": adoption_retried_tile,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_resume_state(state_path, state)
    return {
        "params_path": resume_params_path if remaining else None,
        "state_path": state_path,
        "fingerprint": fingerprint,
        "fingerprint_components": fingerprint_components,
        "tile_ids": tile_ids,
        "remaining": remaining,
        "complete": scan["complete"],
        "expected_resolution": expected_resolution,
        "quarantine_paths": quarantine_paths,
        "resume_allowlist_path": resume_allowlist_path,
        "resume_params_path": resume_params_path,
        "source_params_path": params_path,
    }


def configure_force_resume_batch(plan: dict, tile_ids: list[str]) -> Path:
    """Rewrite the generated allow-list/parameters for one checkpoint batch."""
    if not tile_ids:
        raise ValueError("Cannot configure an empty FORCE resume batch")
    unexpected = sorted(set(tile_ids) - set(plan["remaining"]))
    if unexpected:
        raise ValueError(f"FORCE resume batch contains unexpected tile(s): {unexpected}")
    write_tile_allowlist(plan["resume_allowlist_path"], tile_ids)
    create_resume_parameters(
        plan["source_params_path"],
        plan["resume_params_path"],
        plan["resume_allowlist_path"],
        tile_ids,
    )
    return plan["resume_params_path"]


def mark_force_batch_running(plan: dict, tile_ids: list[str]) -> None:
    state = load_resume_state(plan["state_path"])
    if state is None or state.get("fingerprint") != plan["fingerprint"]:
        raise RuntimeError("FORCE resume state changed before batch execution")
    state.update(
        status="running",
        active_tiles=list(tile_ids),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    write_resume_state(plan["state_path"], state)


def mark_force_batch_finished(plan: dict, tile_ids: list[str]) -> None:
    state = load_resume_state(plan["state_path"])
    if state is None or state.get("fingerprint") != plan["fingerprint"]:
        raise RuntimeError("FORCE resume state changed during batch execution")
    completed = list(dict.fromkeys(state.get("complete_tiles", []) + list(tile_ids)))
    remaining = [tile_id for tile_id in state.get("remaining_tiles", []) if tile_id not in tile_ids]
    state.update(
        status="complete" if not remaining else "pending",
        active_tiles=[],
        complete_tiles=completed,
        remaining_tiles=remaining,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    write_resume_state(plan["state_path"], state)


def finalize_force_resume(
    *,
    plan: dict,
    raw_tiles_root: Path,
    raw_suffix: str,
    validation_mode: str,
) -> dict:
    """Validate every expected output after FORCE exits and persist final state."""
    scan = inspect_force_outputs(
        raw_tiles_root,
        plan["tile_ids"],
        raw_suffix,
        validation_mode=validation_mode,
        expected_resolution=plan["expected_resolution"],
    )
    remaining = scan["missing"] + list(scan["corrupt"])
    state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "fingerprint": plan["fingerprint"],
        "fingerprint_components": plan["fingerprint_components"],
        "raw_suffix": raw_suffix,
        "validation_mode": validation_mode,
        "status": "complete" if not remaining else "incomplete",
        "required_tile_count": len(plan["tile_ids"]),
        "complete_tiles": scan["complete"],
        "remaining_tiles": remaining,
        "active_tiles": [],
        "corrupt_tiles": scan["corrupt"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_resume_state(plan["state_path"], state)
    return state
