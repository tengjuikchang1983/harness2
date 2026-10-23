# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Web console smoke tests against a throwaway runs dir."""

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from harness2.webui.app import create_app


class WebuiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.runs = Path(temporary.name) / "runs"
        self.runs.mkdir()
        self.client = TestClient(create_app(self.runs))

    def test_healthz(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_dashboard_renders_with_empty_runs_dir(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Dashboard", response.text)
        self.assertIn("No runs yet", response.text)

    def test_demo_json_projects_into_namespace(self):
        demo_dir = self.runs / "demo"
        demo_dir.mkdir()
        (demo_dir / "demo.json").write_text(
            json.dumps(
                {
                    "domain": "accounting",
                    "n_tasks": 2,
                    "done": 2,
                    "failed": 0,
                    "tasks": [
                        {"task": "ledger-1", "status": "ok"},
                        {"task": "ledger-2", "status": "ok"},
                    ],
                }
            )
        )
        dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn('href="/ns/demo"', dashboard.text)

        namespace = self.client.get("/ns/demo")
        self.assertEqual(namespace.status_code, 200)
        self.assertIn("ledger-1", namespace.text)
        self.assertIn("ledger-2", namespace.text)
        self.assertIn("Tasks", namespace.text)

    def test_missing_paths_are_clean(self):
        self.assertEqual(self.client.get("/ns/nope").status_code, 404)
        self.assertEqual(self.client.get("/library").status_code, 200)


if __name__ == "__main__":
    unittest.main()
