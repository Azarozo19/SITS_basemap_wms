import json
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from utils.force_resume import (
    build_force_fingerprint,
    configure_force_resume_batch,
    create_resume_parameters,
    inspect_force_outputs,
    mark_force_batch_running,
    prepare_force_resume,
    read_tile_allowlist,
    validate_force_raster,
)


RAW_SUFFIX = "_HL_UDF_SEN2L_PYP.tif"


def _write_raster(path: Path, resolution=10):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=8,
        height=8,
        count=2,
        dtype="int16",
        crs="EPSG:3035",
        transform=from_origin(4_000_000, 3_000_000, resolution, resolution),
        tiled=True,
        blockxsize=16,
        blockysize=16,
    ) as dst:
        dst.write(np.ones((2, 8, 8), dtype=np.int16))


def _write_job_files(job_root: Path, tile_ids: list[str]):
    allowlist = job_root / "tile_extent.txt"
    allowlist.write_text(f"{len(tile_ids)}\n" + "".join(f"{tile}\n" for tile in tile_ids))
    params = job_root / "tsa_UDF.prm"
    params.write_text(
        "X_TILE_RANGE = 1 2\n"
        "Y_TILE_RANGE = 3 3\n"
        f"FILE_TILE = {allowlist}\n"
        "RESOLUTION = 10\n"
    )
    return params


class ForceResumeTests(unittest.TestCase):
    def test_resume_allowlist_preserves_container_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            job_root = Path(temp_dir)
            source_params = job_root / "tsa_UDF.prm"
            source_params.write_text(
                "FILE_TILE = /opb_mount/process/job/tile_extent.txt\n"
                "X_TILE_RANGE = 0 0\n"
                "Y_TILE_RANGE = 0 0\n"
            )
            resume_params = job_root / "tsa_UDF.resume.prm"
            resume_allowlist = job_root / "tile_extent.resume.txt"
            resume_allowlist.write_text("1\nX0002_Y0003\n")

            create_resume_parameters(
                source_params,
                resume_params,
                resume_allowlist,
                ["X0002_Y0003"],
            )

            self.assertIn(
                "FILE_TILE = /opb_mount/process/job/tile_extent.resume.txt",
                resume_params.read_text(),
            )

    def test_fingerprint_resolves_container_udf_beside_parameters(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            job_root = Path(temp_dir) / "job"
            job_root.mkdir()
            params = job_root / "tsa_UDF.prm"
            params.write_text("FILE_PYTHON = /container/job/example_udf.py\n")
            (job_root / "example_udf.py").write_text("print('test')\n")

            _, components = build_force_fingerprint(
                params,
                ["X0001_Y0001"],
                "force:test",
                RAW_SUFFIX,
            )

            self.assertIn("udf_sha256", components)

    def test_read_tile_allowlist_validates_header(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tiles.txt"
            path.write_text("2\nX0001_Y0003\n")
            with self.assertRaisesRegex(ValueError, "count mismatch"):
                read_tile_allowlist(path)

    def test_full_raster_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            valid_path = Path(temp_dir) / "valid.tif"
            _write_raster(valid_path)
            self.assertEqual(
                validate_force_raster(valid_path, validation_mode="full", expected_resolution=10),
                (True, None),
            )

            invalid_path = Path(temp_dir) / "invalid.tif"
            invalid_path.write_bytes(b"not a geotiff")
            valid, reason = validate_force_raster(invalid_path, validation_mode="full")
            self.assertFalse(valid)
            self.assertTrue(reason)

    def test_inspection_classifies_complete_missing_and_corrupt_tiles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_root = Path(temp_dir) / "tiles_tss"
            valid_path = raw_root / "X0001_Y0003" / "PYP" / f"result{RAW_SUFFIX}"
            _write_raster(valid_path)
            corrupt_path = raw_root / "X0002_Y0003" / "PYP" / f"result{RAW_SUFFIX}"
            corrupt_path.parent.mkdir(parents=True)
            corrupt_path.write_bytes(b"broken")

            scan = inspect_force_outputs(
                raw_root,
                ["X0001_Y0003", "X0002_Y0003", "X0003_Y0003"],
                RAW_SUFFIX,
                validation_mode="full",
                expected_resolution=10,
            )

            self.assertEqual(scan["complete"], ["X0001_Y0003"])
            self.assertEqual(scan["missing"], ["X0003_Y0003"])
            self.assertIn("X0002_Y0003", scan["corrupt"])

    def test_prepare_resume_writes_only_unfinished_tiles_and_quarantines_corrupt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            job_root = Path(temp_dir) / "job"
            job_root.mkdir()
            params = _write_job_files(job_root, ["X0001_Y0003", "X0002_Y0003"])
            raw_root = job_root / "tiles_tss"
            valid_path = raw_root / "X0001_Y0003" / "PYP" / f"result{RAW_SUFFIX}"
            _write_raster(valid_path)
            corrupt_path = raw_root / "X0002_Y0003" / "PYP" / f"result{RAW_SUFFIX}"
            corrupt_path.parent.mkdir(parents=True)
            corrupt_path.write_bytes(b"broken")

            plan = prepare_force_resume(
                job_root=job_root,
                params_path=params,
                raw_tiles_root=raw_root,
                force_image="force:test",
                raw_suffix=RAW_SUFFIX,
                validation_mode="full",
            )

            self.assertEqual(plan["complete"], ["X0001_Y0003"])
            self.assertEqual(plan["remaining"], ["X0002_Y0003"])
            self.assertEqual(read_tile_allowlist(job_root / "tile_extent.resume.txt"), ["X0002_Y0003"])
            self.assertFalse(corrupt_path.exists())
            quarantined = list((job_root / "resume_quarantine").rglob(f"*{RAW_SUFFIX}"))
            self.assertEqual(len(quarantined), 1)
            resume_params = Path(plan["params_path"]).read_text()
            self.assertIn(f"FILE_TILE = {job_root / 'tile_extent.resume.txt'}", resume_params)
            self.assertIn("X_TILE_RANGE = 2 2", resume_params)

            state = json.loads((job_root / "force_resume_state.json").read_text())
            self.assertEqual(state["status"], "pending")
            self.assertEqual(state["remaining_tiles"], ["X0002_Y0003"])

    def test_changed_fingerprint_refuses_to_mix_existing_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            job_root = Path(temp_dir) / "job"
            job_root.mkdir()
            params = _write_job_files(job_root, ["X0001_Y0003"])
            raw_root = job_root / "tiles_tss"
            _write_raster(raw_root / "X0001_Y0003" / "PYP" / f"result{RAW_SUFFIX}")
            prepare_force_resume(
                job_root=job_root,
                params_path=params,
                raw_tiles_root=raw_root,
                force_image="force:test",
                raw_suffix=RAW_SUFFIX,
                validation_mode="metadata",
            )
            _write_raster(raw_root / "X0001_Y0003" / "PYP" / f"result{RAW_SUFFIX}")

            params.write_text(params.read_text().replace("RESOLUTION = 10", "RESOLUTION = 20"))
            with self.assertRaisesRegex(RuntimeError, "fingerprint changed"):
                prepare_force_resume(
                    job_root=job_root,
                    params_path=params,
                    raw_tiles_root=raw_root,
                    force_image="force:test",
                    raw_suffix=RAW_SUFFIX,
                    validation_mode="metadata",
                )

    def test_interrupted_active_batch_is_quarantined_and_retried(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            job_root = Path(temp_dir) / "job"
            job_root.mkdir()
            params = _write_job_files(job_root, ["X0001_Y0003", "X0002_Y0003"])
            raw_root = job_root / "tiles_tss"
            plan = prepare_force_resume(
                job_root=job_root,
                params_path=params,
                raw_tiles_root=raw_root,
                force_image="force:test",
                raw_suffix=RAW_SUFFIX,
                validation_mode="full",
            )
            configure_force_resume_batch(plan, ["X0001_Y0003"])
            mark_force_batch_running(plan, ["X0001_Y0003"])

            active_output = raw_root / "X0001_Y0003" / "PYP" / f"result{RAW_SUFFIX}"
            _write_raster(active_output)

            recovered_plan = prepare_force_resume(
                job_root=job_root,
                params_path=params,
                raw_tiles_root=raw_root,
                force_image="force:test",
                raw_suffix=RAW_SUFFIX,
                validation_mode="full",
            )

            self.assertEqual(
                recovered_plan["remaining"],
                ["X0001_Y0003", "X0002_Y0003"],
            )
            self.assertFalse(active_output.exists())
            self.assertEqual(
                len(list((job_root / "resume_quarantine").rglob(f"*{RAW_SUFFIX}"))),
                1,
            )

    def test_first_resume_conservatively_retries_newest_existing_tile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            job_root = Path(temp_dir) / "job"
            job_root.mkdir()
            params = _write_job_files(job_root, ["X0001_Y0003", "X0002_Y0003"])
            raw_root = job_root / "tiles_tss"
            older = raw_root / "X0001_Y0003" / "PYP" / f"result{RAW_SUFFIX}"
            newer = raw_root / "X0002_Y0003" / "PYP" / f"result{RAW_SUFFIX}"
            _write_raster(older)
            _write_raster(newer)
            older.touch()
            newer.touch()
            # Force deterministic ordering even on coarse-mtime filesystems.
            older_mtime = older.stat().st_mtime_ns
            newer_mtime = older_mtime + 1_000_000_000
            os.utime(newer, ns=(newer_mtime, newer_mtime))

            plan = prepare_force_resume(
                job_root=job_root,
                params_path=params,
                raw_tiles_root=raw_root,
                force_image="force:test",
                raw_suffix=RAW_SUFFIX,
                validation_mode="full",
            )

            self.assertEqual(plan["complete"], ["X0001_Y0003"])
            self.assertEqual(plan["remaining"], ["X0002_Y0003"])
            self.assertTrue(older.exists())
            self.assertFalse(newer.exists())


if __name__ == "__main__":
    unittest.main()
