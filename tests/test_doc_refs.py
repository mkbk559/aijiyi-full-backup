"""文档不许骗人：现行文档里提到的文件/路由必须真的存在。

历史文档（决策日志、标了"设定已废弃/历史文档"的）不在此列——它们讲的是当时。
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "archcheck" / "doc_ref_check.py"


class DocRefTest(unittest.TestCase):
    def test_current_docs_point_at_real_files_and_routes(self):
        proc = subprocess.run([sys.executable, str(TOOL)],
                              capture_output=True, text=True, encoding="utf-8")
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(proc.returncode, 0,
                         json.dumps(payload, ensure_ascii=False))
        self.assertGreaterEqual(payload["checked"], 40, "别把检查范围缩没了")

    def test_checker_catches_a_fake_path(self):
        sys.path.insert(0, str(ROOT / "tools" / "archcheck"))
        import doc_ref_check

        self.assertIn("aiji/throat/server.py", doc_ref_check.FILE_REF.findall(
            "见 `aiji/throat/server.py`"))
        self.assertFalse((ROOT / "aiji" / "definitely_missing.py").exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
