"""Desktop file-opening safety regressions."""

from __future__ import annotations

import shutil
import os
import unittest
import uuid
from pathlib import Path
from unittest import mock

from aiji.pet import actions


ROOT = Path(__file__).resolve().parent.parent


class TestOpenTarget(unittest.TestCase):
    def test_executable_file_is_not_launched(self) -> None:
        directory = ROOT / ".tmp" / f"pet-open-{uuid.uuid4().hex[:8]}"
        directory.mkdir(mode=0o777, parents=True, exist_ok=True)
        executable = directory / "unsafe.exe"
        executable.write_bytes(b"MZ")
        try:
            with mock.patch.object(actions.os, "startfile", create=True) as startfile:
                ok, message = actions.open_target(str(executable))
            self.assertFalse(ok)
            self.assertIn("可执行", message)
            startfile.assert_not_called()
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_auth_header_uses_configured_api_key(self) -> None:
        with mock.patch.dict(os.environ, {"AIJI_API_KEY": "secret"}, clear=False):
            self.assertEqual(
                actions.auth_headers({"Content-Type": "application/json"}),
                {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer secret",
                },
            )
