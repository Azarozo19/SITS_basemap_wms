import unittest

from run_production import (
    DEFAULT_CONFIG,
    force_stage_command,
    load_config,
    render_stage_command,
)


class ProductionRunnerTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(DEFAULT_CONFIG)

    def test_force_stage_contains_reproducible_force_settings(self):
        command = force_stage_command(self.config, params_exist=True, metadata_validation=False)
        joined = " ".join(command)
        self.assertIn("--skip-prepare", command)
        self.assertIn("--skip-render", command)
        self.assertIn("--date-range 2025-01-01 2025-12-31", joined)
        self.assertIn("--chunk-size 3000 3000", joined)
        self.assertIn("--force-validation full", joined)

    def test_rgb_and_cir_have_independent_render_settings(self):
        rgb = " ".join(render_stage_command(self.config, "rgb", skip_clip=False))
        cir = " ".join(render_stage_command(self.config, "cir", skip_clip=True))

        self.assertIn("--gamma 2.0", rgb)
        self.assertIn("--rgb-gains 1.07 0.96 1.15", rgb)
        self.assertIn("--green-suppression 0.1", rgb)
        self.assertNotIn("--skip-clip", rgb)

        self.assertIn("--gamma 1.3", cir)
        self.assertIn("--rgb-gains 1.0 1.0 1.0", cir)
        self.assertIn("--green-suppression 0.0", cir)
        self.assertIn("--skip-clip", cir)


if __name__ == "__main__":
    unittest.main()
