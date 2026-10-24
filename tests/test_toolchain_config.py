"""Checks for the ESP32-P4 revision-v3 build prerequisites."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ToolchainConfigTest(unittest.TestCase):
    def test_idf_pin_supports_revision_three_and_matches_baseline(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        match = re.search(r"^ARG ESP_IDF_REF=(v\d+\.\d+\.\d+)$", dockerfile, re.MULTILINE)
        self.assertIsNotNone(match)
        version = match.group(1)
        self.assertGreaterEqual(tuple(map(int, version[1:].split("."))), (5, 5, 3))
        baseline = (ROOT / "deps/esp-idf/mods/BASELINE").read_text()
        self.assertIn("IDF_VERSION=" + version + "\n", baseline)

    def test_image_installs_both_board_toolchains(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("./install.sh esp32s3,esp32p4", dockerfile)

    def test_waveshare_43_explicitly_targets_revision_three(self):
        config = (ROOT / "deps/micropython/mods/new_files/ports/esp32/boards" /
                  "WAVESHARE_ESP32_P4_WIFI6_TOUCH_LCD_43/sdkconfig.board").read_text()
        settings = re.findall(r"^CONFIG_ESP32P4_SELECTS_REV_LESS_V3=(.+)$",
                              config, re.MULTILINE)
        self.assertEqual(settings, ["n"])


if __name__ == "__main__":
    unittest.main()
