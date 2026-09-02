import unittest
import warnings

try:
    import numpy as np

    from utils.skel import udf_rgb_p25_least_cloudy_block as optimized
except ModuleNotFoundError:
    np = None
    optimized = None


def _reference(inarray, bandnames, nodata):
    work = inarray.astype(np.float32)
    invalid = work == nodata
    if np.all(invalid):
        return None
    work[invalid] = np.nan
    work[work == 0] = np.nan

    indices = [np.argwhere(bandnames == name)[0][0] for name in (b"RED", b"GREEN", b"BLUE", b"BROADNIR")]
    rgbn = work[:, indices, :, :]
    valid_scenes = np.all(np.isfinite(rgbn), axis=1)
    valid_fraction = valid_scenes.reshape(valid_scenes.shape[0], -1).mean(axis=1)
    ranked = np.argsort(-valid_fraction, kind="stable")
    ranked = ranked[valid_fraction[ranked] > 0]

    output = np.full((5, inarray.shape[2], inarray.shape[3]), nodata, dtype=np.int16)
    if ranked.size == 0:
        return output
    keep_count = max(1, int(np.ceil(ranked.size * 0.5)))
    selected = rgbn[ranked[:keep_count]]
    selected_valid = np.all(np.isfinite(selected), axis=1)
    counts = np.sum(selected_valid, axis=0)
    valid_pixels = counts > 0
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="All-NaN slice encountered")
        for band in range(4):
            percentile = np.nanpercentile(selected[:, band], 25, axis=0)
            output[band][valid_pixels] = np.rint(percentile[valid_pixels])
    output[4][valid_pixels] = counts[valid_pixels]
    return output


@unittest.skipIf(np is None, "NumPy is only available inside the FORCE runtime")
class OptimizedUdfTests(unittest.TestCase):
    def setUp(self):
        self.bandnames = np.array(
            [b"BLUE", b"GREEN", b"RED", b"BROADNIR", b"SWIR1", b"SWIR2"]
        )
        optimized.forcepy_init(None, None, self.bandnames)

    def test_matches_original_algorithm(self):
        rng = np.random.default_rng(42)
        values = rng.integers(0, 10001, size=(17, 6, 12, 9), dtype=np.int16)
        values[rng.random(values.shape) < 0.25] = -9999
        expected = _reference(values, self.bandnames, -9999)
        actual = np.empty((5, 12, 9), dtype=np.int16)

        optimized.forcepy_chunk(values, actual, None, None, self.bandnames, -9999, 8)

        np.testing.assert_array_equal(actual, expected)

    def test_all_nodata_leaves_force_initialized_output_unchanged(self):
        values = np.full((4, 6, 3, 2), -9999, dtype=np.int16)
        actual = np.full((5, 3, 2), -9999, dtype=np.int16)
        optimized.forcepy_chunk(values, actual, None, None, self.bandnames, -9999, 8)
        np.testing.assert_array_equal(actual, -9999)

    def test_missing_required_band_is_reported(self):
        with self.assertRaisesRegex(ValueError, "BROADNIR"):
            optimized.forcepy_init(None, None, np.array([b"RED", b"GREEN", b"BLUE"]))

    def test_partial_partition_matches_numpy_linear_percentile(self):
        rng = np.random.default_rng(7)
        values = rng.normal(size=(21, 4, 5, 3)).astype(np.float32)
        values[rng.random(values.shape) < 0.35] = np.nan
        expected = np.nanpercentile(values, 25, axis=0)

        actual = optimized._nanpercentile_linear_inplace(values.copy(), 25)

        np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-6, equal_nan=True)


if __name__ == "__main__":
    unittest.main()
