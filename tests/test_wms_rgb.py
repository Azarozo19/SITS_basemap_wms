import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    from utils.wms_rgb import atomic_output_path, adjust_rgb_tone, export_wms_rgb
except ModuleNotFoundError:
    np = None
    rasterio = None
    adjust_rgb_tone = None
    atomic_output_path = None
    export_wms_rgb = None


@unittest.skipIf(rasterio is None, "Rasterio test dependencies are not installed")
class WmsExportTests(unittest.TestCase):
    def test_atomic_output_preserves_previous_file_on_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            final_path = Path(temp_dir) / "result.txt"
            final_path.write_text("old")

            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                with atomic_output_path(final_path) as temporary_path:
                    temporary_path.write_text("partial")
                    raise RuntimeError("interrupted")

            self.assertEqual(final_path.read_text(), "old")
            self.assertEqual(list(final_path.parent.glob("*.building*")), [])

    def test_tone_adjustment_brightens_and_reduces_saturation(self):
        valid = np.array([[True, True], [True, False]])
        rgb = [
            np.array([[64, 100], [200, 0]], dtype=np.uint8),
            np.array([[32, 100], [100, 0]], dtype=np.uint8),
            np.array([[16, 100], [50, 0]], dtype=np.uint8),
        ]

        adjusted = adjust_rgb_tone(rgb, valid, gamma=2.0, saturation=0.0)

        self.assertGreater(adjusted[0][0, 0], rgb[0][0, 0])
        np.testing.assert_array_equal(adjusted[0][valid], adjusted[1][valid])
        np.testing.assert_array_equal(adjusted[1][valid], adjusted[2][valid])
        self.assertEqual(adjusted[0][1, 1], 0)

    def test_neutral_tone_adjustment_preserves_values(self):
        valid = np.ones((1, 2), dtype=bool)
        rgb = [np.array([[10, 20]], dtype=np.uint8) for _ in range(3)]
        adjusted = adjust_rgb_tone(rgb, valid)
        for actual, expected in zip(adjusted, rgb):
            np.testing.assert_array_equal(actual, expected)

    def test_channel_gains_adjust_rgb_balance(self):
        valid = np.array([[True, False]])
        rgb = [
            np.array([[100, 10]], dtype=np.uint8),
            np.array([[100, 10]], dtype=np.uint8),
            np.array([[100, 10]], dtype=np.uint8),
        ]
        adjusted = adjust_rgb_tone(
            rgb,
            valid,
            channel_gains=(1.1, 0.9, 1.2),
        )

        self.assertEqual([band[0, 0] for band in adjusted], [110, 90, 120])
        self.assertEqual([band[0, 1] for band in adjusted], [0, 0, 0])

    def test_neutral_protection_preserves_gray_pixels(self):
        valid = np.ones((1, 2), dtype=bool)
        rgb = [
            np.array([[100, 150]], dtype=np.uint8),
            np.array([[100, 120]], dtype=np.uint8),
            np.array([[100, 90]], dtype=np.uint8),
        ]
        adjusted = adjust_rgb_tone(
            rgb,
            valid,
            channel_gains=(1.1, 0.9, 1.2),
            neutral_protection=0.4,
        )

        self.assertEqual([band[0, 0] for band in adjusted], [100, 100, 100])
        self.assertNotEqual([band[0, 1] for band in adjusted], [150, 120, 90])

    def test_green_suppression_only_changes_green_dominant_pixels(self):
        valid = np.ones((1, 3), dtype=bool)
        rgb = [
            np.array([[80, 120, 100]], dtype=np.uint8),
            np.array([[140, 100, 100]], dtype=np.uint8),
            np.array([[70, 80, 100]], dtype=np.uint8),
        ]
        adjusted = adjust_rgb_tone(rgb, valid, green_suppression=0.1)

        self.assertEqual([band[0, 0] for band in adjusted], [80, 126, 70])
        self.assertEqual([band[0, 1] for band in adjusted], [120, 100, 80])
        self.assertEqual([band[0, 2] for band in adjusted], [100, 100, 100])

    def test_tone_adjustment_rejects_invalid_controls(self):
        valid = np.ones((1, 1), dtype=bool)
        rgb = [np.ones((1, 1), dtype=np.uint8) for _ in range(3)]
        with self.assertRaisesRegex(ValueError, "gamma"):
            adjust_rgb_tone(rgb, valid, gamma=0)
        with self.assertRaisesRegex(ValueError, "saturation"):
            adjust_rgb_tone(rgb, valid, saturation=-0.1)
        with self.assertRaisesRegex(ValueError, "channel_gains"):
            adjust_rgb_tone(rgb, valid, channel_gains=(1.0, 1.0))
        with self.assertRaisesRegex(ValueError, "channel_gains"):
            adjust_rgb_tone(rgb, valid, channel_gains=(1.0, 0.0, 1.0))
        with self.assertRaisesRegex(ValueError, "neutral_protection"):
            adjust_rgb_tone(rgb, valid, neutral_protection=-0.1)
        with self.assertRaisesRegex(ValueError, "green_suppression"):
            adjust_rgb_tone(rgb, valid, green_suppression=1.0)
        with self.assertRaisesRegex(ValueError, "green_dominance_threshold"):
            adjust_rgb_tone(rgb, valid, green_dominance_threshold=0.0)

    def test_export_preserves_mask_and_valid_scene_products(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.tif"
            output_path = Path(temp_dir) / "output.tif"
            profile = {
                "driver": "GTiff",
                "width": 4,
                "height": 3,
                "count": 5,
                "dtype": "int16",
                "nodata": -9999,
                "crs": "EPSG:3035",
                "transform": from_origin(0, 30, 10, 10),
            }
            values = np.array(
                [
                    [[100, 200, 300, -9999], [400, 500, 600, 700], [800, 900, 1000, 100]],
                    [[100, 200, 300, -9999], [400, 500, 600, 700], [800, 900, 1000, 100]],
                    [[100, 200, 300, -9999], [400, 500, 600, 700], [800, 900, 1000, 100]],
                    [[100, 200, 300, -9999], [400, 500, 600, 700], [800, 900, 1000, 100]],
                    [[3, 3, 2, -9999], [5, 1, 4, 3], [2, 6, 7, 3]],
                ],
                dtype=np.int16,
            )
            with rasterio.open(input_path, "w", **profile) as dst:
                dst.write(values)

            export_wms_rgb(
                input_path,
                output_tif=output_path,
                stretch=(0, 1000),
                min_valid_scenes=3,
                compression_method="NONE",
                blocksize=16,
            )

            with rasterio.open(output_path) as result:
                self.assertEqual(result.count, 5)
                self.assertEqual(result.dtypes, ("uint8",) * 5)
                alpha = result.read(4)
                self.assertEqual(alpha[0, 0], 255)
                self.assertEqual(alpha[0, 2], 0)
                self.assertEqual(alpha[0, 3], 0)
                self.assertEqual(result.read(5)[1, 0], 5)


if __name__ == "__main__":
    unittest.main()
