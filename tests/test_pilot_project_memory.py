"""One focused test for project-neutral, temporary broker pilot."""
import unittest

from scripts.pilot_project_memory import run_pilot


class ProjectPilotTests(unittest.TestCase):
    def test_synapsys_mobile_pilot(self):
        result = run_pilot("synapsys-os-mobile", "synapsys")
        self.assertEqual("PASS", result["result"])
        self.assertFalse(result["writes_real_projects"])
        self.assertFalse(result["mcp_tested"])
        self.assertEqual(6, len(result["checks"]))

    def test_generic_project_name_and_conflict(self):
        result = run_pilot("gestor-domestico", "proyecto-diferente")
        self.assertEqual("PASS", result["result"])
        with self.assertRaises(ValueError):
            run_pilot("mismo", "mismo")


if __name__ == "__main__":
    unittest.main()
