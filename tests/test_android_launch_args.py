import unittest

from simemu import android


class TestLaunchArgNormalization(unittest.TestCase):
    def test_normalizes_string_and_bool_extras(self) -> None:
        self.assertEqual(
            android._normalize_launch_args(
                ["--debug-route=journey/root", "--disable-mock-network", "--theme=dark"]
            ),
            [
                "--es", "debug_route", "journey/root",
                "--ez", "disable_mock_network", "true",
                "--es", "theme", "dark",
            ],
        )

    def test_preserves_known_am_start_options(self) -> None:
        self.assertEqual(
            android._normalize_launch_args(["--activity-clear-top", "--user", "0"]),
            ["--activity-clear-top", "--user", "0"],
        )
