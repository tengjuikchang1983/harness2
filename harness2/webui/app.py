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

"""Web console over the harness2 runs tree.

harness2-webui [--host 127.0.0.1] [--port 8788]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from . import data

TEMPLATES = Path(__file__).resolve().parent / "templates"
REPO_ROOT = Path(__file__).resolve().parents[2]

STREAM_POLL_SECONDS = 0.2


@dataclass
class Action:
    id: str
    kind: str
    argv: list[str]
    status: str  # "running" -> "done" | "failed"
    started_at: str
    ended_at: str | None = None
    exit_code: int | None = None
    log_path: Path | None = None


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ActionRunner:
    """Runs one subprocess at a time; logs persist under <runs>/_webui/."""

    def __init__(self, runs_dir: Path, cwd: Path):
        self.log_root = runs_dir / "_webui"
        self.cwd = cwd
        self._lock = threading.Lock()
        self._actions: dict[str, Action] = {}
        self._procs: dict[str, subprocess.Popen] = {}
        self._running_id: str | None = None

    def start(self, kind: str, argv: list[str]) -> Action | None:
        """Launch the action, or return None when one is already running."""
        with self._lock:
            if self._running_id is not None:
                return None
            self.log_root.mkdir(parents=True, exist_ok=True)
            action = Action(
                id=uuid.uuid4().hex[:8],
                kind=kind,
                argv=argv,
                status="running",
                started_at=_utcnow(),
                log_path=self.log_root / f"{kind}-{uuid.uuid4().hex[:8]}.log",
            )
            log_file = action.log_path.open("w", encoding="utf-8")
            try:
                proc = subprocess.Popen(
                    argv,
                    cwd=self.cwd,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                )
            finally:
                log_file.close()
            self._actions[action.id] = action
            self._procs[action.id] = proc
            self._running_id = action.id
            threading.Thread(target=self._reap, args=(action, proc), daemon=True).start()
            return action

    def _reap(self, action: Action, proc: subprocess.Popen) -> None:
        exit_code = proc.wait()
        with self._lock:
            action.exit_code = exit_code
            action.ended_at = _utcnow()
            action.status = "done" if exit_code == 0 else "failed"
            self._running_id = None

    def get(self, action_id: str) -> Action | None:
        return self._actions.get(action_id)

    def running(self) -> Action | None:
        if self._running_id is None:
            return None
        return self._actions.get(self._running_id)

    def latest(self) -> Action | None:
        if not self._actions:
            return None
        return max(self._actions.values(), key=lambda action: action.started_at)


def _event_stream(action: Action) -> Iterator[str]:
    """SSE replay of the action log followed by a live tail until it exits."""
    offset = 0
    while True:
        chunk = ""
        if action.log_path is not None and action.log_path.is_file():
            with action.log_path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(offset)
                chunk = handle.read()
                offset = handle.tell()
        for line in chunk.splitlines():
            yield f"data: {line}\n\n"
        if action.status != "running":
            detail = f" exit={action.exit_code}" if action.exit_code is not None else ""
            yield f"data: [action {action.status}{detail}]\n\n"
            yield "event: end\ndata: {}\n\n"
            return
        time.sleep(STREAM_POLL_SECONDS)


def _demo_argv(runs_dir: Path) -> list[str]:
    script = Path(sys.executable).with_name("harness2-demo")
    argv = (
        [str(script)]
        if script.is_file()
        else [
            sys.executable,
            "-c",
            "from harness2.cli.demo import main; main()",
        ]
    )
    return [*argv, "--output", str(runs_dir / "demo"), "--k", "2"]


def default_runs_dir() -> Path:
    try:
        from .. import config

        return config.RUNS
    except Exception:
        return Path(os.environ.get("HARNESS2_RUNS_DIR", "runs")).expanduser().resolve()


def create_app(runs_dir: Path | None = None) -> FastAPI:
    runs_dir = Path(runs_dir) if runs_dir is not None else default_runs_dir()
    templates = Jinja2Templates(directory=TEMPLATES)
    app = FastAPI(title="harness2 console")
    app.state.runs_dir = runs_dir
    app.state.runner = ActionRunner(runs_dir, cwd=REPO_ROOT)

    def base(request: Request, **extra):
        latest = app.state.runner.latest()
        context = {
            "request": request,
            "runs_dir": runs_dir,
            "namespaces": data.load_run_index(runs_dir),
            "latest_action": latest,
            "latest_log": data.tail_lines(latest.log_path if latest else None, 12),
        }
        context.update(extra)
        return context

    def find_namespace(name: str) -> data.NamespaceEntry:
        for entry in data.load_run_index(runs_dir):
            if entry.name == name:
                return entry
        raise HTTPException(status_code=404, detail=f"no namespace {name!r}")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        entries = data.load_run_index(runs_dir)
        judged = [
            row
            for entry in entries
            if entry.source == "adapt"
            for row in data.judged_rows(entry.path)
        ]
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context=base(
                request,
                active="dashboard",
                entries=entries,
                total_tasks=sum(entry.n_tasks for entry in entries),
                total_done=sum(entry.done for entry in entries),
                total_failed=sum(entry.failed for entry in entries),
                overall_score=data.mean_score(judged),
            ),
        )

    @app.get("/ns/{name}", response_class=HTMLResponse)
    def namespace(request: Request, name: str):
        entry = find_namespace(name)
        return templates.TemplateResponse(
            request=request,
            name="namespace.html",
            context=base(
                request,
                active="namespace",
                current_ns=name,
                entry=entry,
                rows=data.namespace_rows(entry.path, entry.source),
            ),
        )

    @app.get("/ns/{name}/task/{task_id}/harness", response_class=HTMLResponse)
    def harness(request: Request, name: str, task_id: str):
        entry = find_namespace(name)
        page = data.load_harness_page(entry.path, entry.source, task_id)
        if page is None:
            raise HTTPException(status_code=404, detail=f"no task {task_id!r} in {name!r}")
        return templates.TemplateResponse(
            request=request,
            name="harness.html",
            context=base(request, active="namespace", current_ns=name, entry=entry, page=page),
        )

    @app.get("/library", response_class=HTMLResponse)
    def library(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="library.html",
            context=base(request, active="library", domains=data.load_library(runs_dir)),
        )

    @app.post("/actions/demo", response_class=HTMLResponse)
    def start_demo(request: Request):
        action = app.state.runner.start("demo", _demo_argv(runs_dir))
        if action is None:
            return templates.TemplateResponse(
                request=request,
                name="action_busy.html",
                context=base(request, running=app.state.runner.running()),
                status_code=409,
            )
        return RedirectResponse(f"/actions/{action.id}", status_code=303)

    @app.get("/actions/{action_id}", response_class=HTMLResponse)
    def action_page(request: Request, action_id: str):
        action = app.state.runner.get(action_id)
        if action is None:
            raise HTTPException(status_code=404, detail=f"no action {action_id!r}")
        return templates.TemplateResponse(
            request=request,
            name="action.html",
            context=base(
                request,
                action=action,
                existing_log="\n".join(data.tail_lines(action.log_path, 10_000)),
            ),
        )

    @app.get("/actions/{action_id}/stream")
    def action_stream(action_id: str):
        action = app.state.runner.get(action_id)
        if action is None:
            raise HTTPException(status_code=404, detail=f"no action {action_id!r}")
        return StreamingResponse(
            _event_stream(action),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    args = parser.parse_args(argv)
    uvicorn.run(create_app(), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
