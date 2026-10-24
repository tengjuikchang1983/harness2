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

    def test_real_adapt_roots_render_on_dashboard_and_namespace_routes(self):
        fixtures = (
            ("legacy-ns", self.runs / "adapt", "legacy-task"),
            ("jb-oc-agg1", self.runs / "jb" / "adapt", "technical_writers__task1"),
            ("wb-office-ns", self.runs / "wb" / "office" / "adapt", "office-task"),
            (
                "deep-ns",
                self.runs / "dependencies" / "vendor" / "harvested" / "adapt",
                "deep-task",
            ),
        )
        for namespace, adapt_root, task in fixtures:
            task_dir = adapt_root / namespace / task
            task_dir.mkdir(parents=True)
            (task_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "task_id": task,
                        "domain": "technical_writers",
                        "k": 1,
                        "seed": None,
                        "status": "ok",
                        "base": [],
                        "modes": {},
                        "selections": {},
                        "errors": [],
                    }
                )
            )

        dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn('href="/ns/legacy-ns"', dashboard.text)
        self.assertIn('href="/ns/jb-oc-agg1"', dashboard.text)
        self.assertIn('href="/ns/wb-office-ns"', dashboard.text)
        self.assertNotIn('href="/ns/deep-ns"', dashboard.text)

        namespace = self.client.get("/ns/jb-oc-agg1")
        self.assertEqual(namespace.status_code, 200)
        self.assertIn("technical_writers__task1", namespace.text)

    def test_nested_library_renders_lessons_with_run_and_mode_identity(self):
        for run, mode, lesson in (
            ("k1-oc", "agg", "Open with the concrete result."),
            ("k2-oc", "seq", "Keep each revision tied to evidence."),
        ):
            domain = (
                self.runs
                / "jb"
                / "library"
                / "jb"
                / run
                / mode
                / "technical_writers"
            )
            entry = domain / "harnesses" / f"task-{run}"
            entry.mkdir(parents=True)
            (domain / "INDEX.md").write_text("# Library - technical_writers\n")
            (entry / "EXPERIENCE.md").write_text(
                f"# technical_writers/{run}\n\n"
                f"## Transferable lesson\n- **lesson**: {lesson}\n"
            )

        response = self.client.get("/library")
        self.assertEqual(response.status_code, 200)
        self.assertIn("technical_writers", response.text)
        self.assertIn("Open with the concrete result.", response.text)
        self.assertIn("Keep each revision tied to evidence.", response.text)
        self.assertIn("k1-oc/agg", response.text)
        self.assertIn("k2-oc/seq", response.text)

    def test_missing_paths_are_clean(self):
        self.assertEqual(self.client.get("/ns/nope").status_code, 404)
        self.assertEqual(self.client.get("/library").status_code, 200)


if __name__ == "__main__":
    unittest.main()
