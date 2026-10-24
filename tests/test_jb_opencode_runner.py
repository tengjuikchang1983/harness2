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

import os
import signal
import sys
import tempfile
import textwrap
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from harness2.benchmarks.jb_opencode_runner import Settings, _attempt


class OpenCodeAttemptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin_dir = self.root / "bin"
        self.workspace = self.root / "workspace"
        self.opencode_dir = self.root / "opencode"
        self.traj_file = self.root / "trajectory.jsonl"
        self.log_file = self.root / "runner.log"
        self.bin_dir.mkdir()
        (self.workspace / "task_folder").mkdir(parents=True)
        (self.workspace / "output").mkdir()
        (self.opencode_dir / "packages" / "opencode").mkdir(parents=True)
        self.cfg = Settings(
            task_ids=("task-1",),
            label="test",
            render_dir=self.root / "render",
            tasks_base=self.root / "tasks",
            opencode_dir=self.opencode_dir,
            model_id="model",
            agent_name="",
            variant="",
            agent_steps="",
            timeout_per_task="3600",
            max_retries=1,
            log_dir=self.root,
        )

    def _fake_bun(self, source: str) -> None:
        bun = self.bin_dir / "bun"
        bun.write_text(f"#!{sys.executable}\n{textwrap.dedent(source)}", encoding="utf-8")
        bun.chmod(0o755)

    def _run_attempt(self, cfg: Settings) -> int:
        with patch.dict(os.environ, {"PATH": str(self.bin_dir)}):
            return _attempt(
                cfg,
                self.workspace,
                self.traj_file,
                self.log_file,
                "task-1",
                1,
            )

    def _assert_process_gone(self, pid: int) -> None:
        try:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return
                time.sleep(0.01)
            self.fail(f"child process {pid} survived its parent's timeout")
        finally:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_attempt_runs_bun_without_timeout_executable(self):
        self._fake_bun(
            """\
            from pathlib import Path
            import sys

            workspace = Path(sys.argv[sys.argv.index("--dir") + 1])
            (workspace / "output" / "deliverable.txt").write_text("done", encoding="utf-8")
            print("plain stdout")
            print('{"type":"text","part":{"text":"done"}}')
            print('{"type":"step_finish"}')
            print("diagnostic", file=sys.stderr)
            """
        )

        code = self._run_attempt(self.cfg)

        self.assertEqual(code, 0)
        self.assertEqual(
            (self.workspace / "output" / "deliverable.txt").read_text(encoding="utf-8"),
            "done",
        )
        self.assertEqual(
            self.traj_file.read_text(encoding="utf-8"),
            '{"type":"text","part":{"text":"done"}}\n{"type":"step_finish"}\n',
        )
        self.assertEqual(self.log_file.read_text(encoding="utf-8"), "diagnostic\n")

    @unittest.skipUnless(os.name == "posix", "requires POSIX process groups")
    def test_attempt_returns_124_and_kills_child_on_timeout(self):
        pid_file = self.root / "child.pid"
        self._fake_bun(
            f"""\
            from pathlib import Path
            import subprocess
            import sys
            import time

            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            Path({str(pid_file)!r}).write_text(str(child.pid), encoding="utf-8")
            print('{{"type":"started"}}', flush=True)
            time.sleep(30)
            """
        )

        code = self._run_attempt(replace(self.cfg, timeout_per_task="1"))

        self.assertEqual(code, 124)
        self.assertEqual(self.traj_file.read_text(encoding="utf-8"), '{"type":"started"}\n')
        self._assert_process_gone(int(pid_file.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
