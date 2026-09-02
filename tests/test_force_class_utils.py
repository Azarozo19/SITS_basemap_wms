import tempfile
import unittest
from pathlib import Path

from utils.force_class_utils import (
    _docker_command,
    _required_local_mounts,
    _validate_processing_options,
    containerize_path,
    extract_coordinates,
    set_force_parameters,
)


class ForceParameterTests(unittest.TestCase):
    def test_set_force_parameters_updates_by_tag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            params = Path(temp_dir) / "job.prm"
            params.write_text(
                "# example\n"
                "CHUNK_SIZE = 0 0\n"
                "FILE_TILE = NULL\n"
                "STREAMING = TRUE\n"
            )

            set_force_parameters(
                params,
                {
                    "CHUNK_SIZE": "1000 1000",
                    "FILE_TILE": "/data/tile_extent.txt",
                    "STREAMING": "FALSE",
                },
            )

            self.assertEqual(
                params.read_text(),
                "# example\n"
                "CHUNK_SIZE = 1000 1000\n"
                "FILE_TILE = /data/tile_extent.txt\n"
                "STREAMING = FALSE\n",
            )

    def test_set_force_parameters_rejects_missing_tag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            params = Path(temp_dir) / "job.prm"
            params.write_text("CHUNK_SIZE = 0 0\n")
            with self.assertRaisesRegex(ValueError, "FILE_TILE"):
                set_force_parameters(params, {"FILE_TILE": "/data/tiles.txt"})

    def test_set_force_parameters_rejects_duplicate_tag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            params = Path(temp_dir) / "job.prm"
            params.write_text("CHUNK_SIZE = 0 0\nCHUNK_SIZE = 0 0\n")
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                set_force_parameters(params, {"CHUNK_SIZE": "1000 1000"})

    def test_extract_coordinates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            extent = Path(temp_dir) / "tile_extent.txt"
            extent.write_text("3\nX0002_Y0004\nX0003_Y0002\nX0001_Y0003\n")
            self.assertEqual(extract_coordinates(extent), ("1 3", "2 4"))


class ForceExecutionTests(unittest.TestCase):
    def test_containerize_path_honors_mount_destination_and_mode(self):
        self.assertEqual(
            containerize_path(
                "/drive_mount/process/job.prm",
                ["/drive_mount:/opb_mount", "/tmp:/scratch:ro"],
            ),
            "/opb_mount/process/job.prm",
        )

    def test_default_chunk_is_valid_for_30km_tile(self):
        _validate_processing_options((1000, 1000), 10)

    def test_invalid_chunk_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not divisible"):
            _validate_processing_options((700, 1000), 10)

    def test_docker_command_uses_pinned_image_and_argument_list(self):
        command = _docker_command(
            "davidfrantz/force:3.9.02",
            "/data:/data",
            "/force:/force",
            ["force-higher-level", "/data/job.prm"],
            use_sudo=False,
        )
        self.assertEqual(command[0:3], ["docker", "run", "--rm"])
        self.assertIn("davidfrantz/force:3.9.02", command)
        self.assertEqual(command[-2:], ["force-higher-level", "/data/job.prm"])

    def test_required_paths_outside_primary_mount_are_added_read_only(self):
        mounts = _required_local_mounts(
            "/rvt_mount:/rvt_mount",
            ["/opb_mount/SITS_basemap_wms", "/opb_mount/general_data/germany"],
        )
        self.assertEqual(mounts[0], "/rvt_mount:/rvt_mount")
        self.assertIn(
            "/opb_mount/SITS_basemap_wms:/opb_mount/SITS_basemap_wms:ro",
            mounts,
        )
        self.assertIn(
            "/opb_mount/general_data/germany:/opb_mount/general_data/germany:ro",
            mounts,
        )


if __name__ == "__main__":
    unittest.main()
