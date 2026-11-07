import json, unittest
from pathlib import Path
from aiji.brain import names


class NamesTest(unittest.TestCase):
    def test_self_names_present(self):
        line = names.names_line()
        self.assertIn("玛卡巴卡", line)

    def test_others_not_marked_as_self(self):
        line = names.names_line()
        self.assertIn("不是你", line)
        self.assertIn("大肥鱼", line)

    def test_missing_file_falls_back(self):
        line = names.names_line(path=".tmp/definitely-not-here.json")
        self.assertIn("玛卡巴卡", line)


if __name__ == "__main__":
    unittest.main()
