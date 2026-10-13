import unittest
from unittest.mock import patch

from dev_portal.cli import main


class FirstRunRoutingTests(unittest.TestCase):
    def test_bare_double_click_routes_to_native_sign_in_not_owner_config(self):
        with patch("dev_portal.desktop.run_desktop", return_value=0) as launch:
            self.assertEqual(main([]), 0)
        launch.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
