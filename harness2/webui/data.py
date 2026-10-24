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

"""Read-only projections of the runs tree for the web console.

Real sweep output exists only after the first model-backed run, in legacy
runs/adapt and runs/library trees or scoped benchmark/subset roots. demo.json
is written at the very end of a demo, so every reader here tolerates missing
directories and unreadable files.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

DONE_STATUSES = ("ok", "partial")
FAILED_STATUSES = ("failed", "base-failed", "error", "exception")

MAX_FILE_CHARS = 32_000


@dataclass
class TaskRow:
    task: str
    status: str
    modes: dict[str, dict]
    selections: dict[str, dict]
    score: float | None


@dataclass
class NamespaceEntry:
    name: str
    path: Path
    n_tasks: int
    done: int
    failed: int
    mean_score: float | None
    source: str  # "demo" | "adapt"


@dataclass(frozen=True)
class ComponentSpec:
    id: str
    name: str
    patterns: tuple[str, ...]
    display: str
    group: str
    when: str


# (group, subtitle, css color key) — the single source for the c1-c8 table.
GROUPS: tuple[tuple[str, str, str], ...] = (
    ("Context construction", "what enters the context, and when", "blue"),
    ("Middleware", "hooks every tool call, outside the model's discretion", "purple"),
    ("Tool interface", "what the model can call, and how", "green"),
    ("Orchestration", "when work is delegated to a new agent, and how", "pink"),
)

COMPONENTS: tuple[ComponentSpec, ...] = (
    ComponentSpec(
        "c1",
        "System prompt",
        ("systemprompt.md",),
        "systemprompt.md",
        "Context construction",
        "every call",
    ),
    ComponentSpec(
        "c2",
        "Guardrails",
        ("guardrails.md",),
        "guardrails.md",
        "Context construction",
        "every call",
    ),
    ComponentSpec(
        "c3",
        "Memory",
        ("LongTermMEMORY.md", "memory*", "memory/**"),
        "LongTermMEMORY.md / memory files",
        "Context construction",
        "every call",
    ),
    ComponentSpec(
        "c6",
        "Skills",
        ("skills/*/SKILL.md",),
        "skills/<name>/SKILL.md",
        "Context construction",
        "on invoke",
    ),
    ComponentSpec(
        "c4",
        "Tool guidance",
        ("tool_guidance.md", "tool_descriptions/tool_guidance.md"),
        "tool_guidance.md",
        "Tool interface",
        "every call",
    ),
    ComponentSpec(
        "c7",
        "Scripts",
        ("scripts/*.py",),
        "scripts/<name>.py",
        "Tool interface",
        "via bash",
    ),
    ComponentSpec(
        "c8",
        "Plugin",
        ("plugin/*.ts",),
        "plugin/<name>.ts",
        "Middleware",
        "every tool call",
    ),
    ComponentSpec(
        "c5",
        "Subagents",
        ("sub_agents/*/AGENT.md",),
        "sub_agents/<n>/AGENT.md",
        "Orchestration",
        "task tool",
    ),
)


@dataclass
class ComponentView:
    spec: ComponentSpec
    files: list[tuple[str, str]]  # (path relative to the tree, content)
    present: bool


@dataclass
class HarnessPage:
    task: str
    tree_dir: Path | None
    label: str | None  # selection label the tree was resolved from
    groups: list[tuple[str, str, str, list[ComponentView]]]


@dataclass
class LibraryDomain:
    name: str
    path: Path
    git_versioned: bool
    git_log: str | None
    experience: str | None


def _read_json(path: Path) -> dict | list | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def tail_lines(path: Path | None, n: int) -> list[str]:
    if path is None or not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-n:]


def judged_rows(ns_dir: Path) -> list[dict]:
    raw = _read_json(ns_dir / "judged.json")
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, dict)]


def _frac(row: dict) -> float | None:
    frac = row.get("frac")
    if isinstance(frac, (int, float)):
        return float(frac)
    passed, criteria = row.get("n_passed"), row.get("n_criteria")
    if isinstance(passed, int) and isinstance(criteria, int) and criteria > 0:
        return passed / criteria
    return None


def mean_score(rows: list[dict]) -> float | None:
    fracs = [frac for frac in (_frac(row) for row in rows) if frac is not None]
    return sum(fracs) / len(fracs) if fracs else None


def _task_scores(rows: list[dict]) -> dict[str, float]:
    by_task: dict[str, list[float]] = {}
    for row in rows:
        frac = _frac(row)
        task = str(row.get("task_id") or "")
        if frac is not None and task:
            by_task.setdefault(task, []).append(frac)
    return {task: sum(fracs) / len(fracs) for task, fracs in by_task.items()}


def task_dirs(ns_dir: Path) -> dict[str, Path]:
    """task id -> task dir. Sweep task dirs are <ns>/<task>; the demo nests them
    under runs/demo/runs/<scheduler namespace>/<task>."""
    found: dict[str, Path] = {}
    for pattern in ("*/summary.json", "*/*/*/summary.json"):
        for summary in sorted(ns_dir.glob(pattern)):
            found.setdefault(summary.parent.name, summary.parent)
    return found


def _demo_rows(ns_dir: Path) -> tuple[list[TaskRow], int, int]:
    raw = _read_json(ns_dir / "demo.json")
    if not isinstance(raw, dict):
        return [], 0, 0
    rows = [
        TaskRow(
            task=str(item.get("task")),
            status=str(item.get("status") or "ok"),
            modes={},
            selections={},
            score=None,
        )
        for item in raw.get("tasks") or []
        if isinstance(item, dict) and item.get("task")
    ]
    return rows, int(raw.get("done") or 0), int(raw.get("failed") or 0)


def namespace_rows(ns_dir: Path, source: str) -> list[TaskRow]:
    if source == "demo":
        rows, _, _ = _demo_rows(ns_dir)
        return rows
    summaries = task_dirs(ns_dir)
    scores = _task_scores(judged_rows(ns_dir))
    rows = []
    for task_id, task_dir in summaries.items():
        summary = _read_json(task_dir / "summary.json")
        summary = summary if isinstance(summary, dict) else {}
        modes = summary.get("modes")
        selections = summary.get("selections")
        rows.append(
            TaskRow(
                task=task_id,
                status=str(summary.get("status") or "running"),
                modes=modes if isinstance(modes, dict) else {},
                selections=selections if isinstance(selections, dict) else {},
                score=scores.get(task_id),
            )
        )
    rows.sort(key=lambda row: row.task)
    return rows


def load_run_index(runs_dir: Path) -> list[NamespaceEntry]:
    """Demo first, then namespaces from legacy and scoped adapt roots in name order."""
    entries: list[NamespaceEntry] = []
    demo_dir = runs_dir / "demo"
    if (demo_dir / "demo.json").is_file():
        rows, done, failed = _demo_rows(demo_dir)
        entries.append(NamespaceEntry("demo", demo_dir, len(rows), done, failed, None, "demo"))
    adapt_roots = [
        path
        for pattern in ("adapt", "*/adapt", "*/*/adapt")
        for path in runs_dir.glob(pattern)
        if path.is_dir()
    ]
    ns_dirs = sorted(
        (
            path
            for adapt in adapt_roots
            for path in adapt.iterdir()
            if path.is_dir() and not path.name.startswith("_")
        ),
        key=lambda path: (path.name, path.as_posix()),
    )
    for ns_dir in ns_dirs:
        rows = namespace_rows(ns_dir, "adapt")
        entries.append(
            NamespaceEntry(
                name=ns_dir.name,
                path=ns_dir,
                n_tasks=len(rows),
                done=sum(1 for row in rows if row.status in DONE_STATUSES),
                failed=sum(1 for row in rows if row.status in FAILED_STATUSES),
                mean_score=mean_score(judged_rows(ns_dir)),
                source="adapt",
            )
        )
    return entries


def _matches(tree: Path, spec: ComponentSpec) -> list[Path]:
    files = {path for pattern in spec.patterns for path in tree.glob(pattern) if path.is_file()}
    return sorted(files)


def _has_any_component(tree: Path) -> bool:
    return any(_matches(tree, spec) for spec in COMPONENTS)


def _read_bounded(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"<unreadable: {exc}>"
    if len(text) > MAX_FILE_CHARS:
        return text[:MAX_FILE_CHARS] + f"\n… truncated ({len(text)} chars total)"
    return text


def resolve_tree_dir(task_dir: Path, summary: dict) -> tuple[Path | None, str | None]:
    """The adopted harness tree for a task: the selected harness per mode, else any
    harness dir carrying components, else a plain harness/ dir."""
    selections = summary.get("selections")
    selections = selections if isinstance(selections, dict) else {}
    for mode in sorted(selections):
        label = (selections.get(mode) or {}).get("chosen_label")
        candidate = task_dir / "harnesses" / str(label)
        if label and candidate.is_dir():
            return candidate, str(label)
    harnesses = task_dir / "harnesses"
    if harnesses.is_dir():
        for candidate in sorted(path for path in harnesses.iterdir() if path.is_dir()):
            if _has_any_component(candidate):
                return candidate, candidate.name
    if (task_dir / "harness").is_dir():
        return task_dir / "harness", "harness"
    return None, None


def load_harness_page(ns_dir: Path, source: str, task_id: str) -> HarnessPage | None:
    task_dir = task_dirs(ns_dir).get(task_id)
    if task_dir is None:
        return None
    summary = _read_json(task_dir / "summary.json")
    tree_dir, label = resolve_tree_dir(task_dir, summary if isinstance(summary, dict) else {})
    groups = []
    for name, subtitle, color in GROUPS:
        views = []
        for spec in COMPONENTS:
            if spec.group != name:
                continue
            files = []
            if tree_dir is not None:
                files = [
                    (path.relative_to(tree_dir).as_posix(), _read_bounded(path))
                    for path in _matches(tree_dir, spec)
                ]
            views.append(ComponentView(spec=spec, files=files, present=bool(files)))
        groups.append((name, subtitle, color, views))
    return HarnessPage(task=task_id, tree_dir=tree_dir, label=label, groups=groups)


def load_library(runs_dir: Path) -> list[LibraryDomain]:
    """Legacy flat domains plus current per-run, per-mode domain libraries."""
    library = runs_dir / "library"
    domains: list[LibraryDomain] = []
    legacy_domains = (
        sorted(path for path in library.iterdir() if path.is_dir()) if library.is_dir() else []
    )
    for domain in legacy_domains:
        git_log = None
        git_versioned = (domain / ".git").exists()
        if git_versioned:
            try:
                proc = subprocess.run(
                    ["git", "log", "--oneline", "-20"],
                    cwd=domain,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                git_log = proc.stdout if proc.returncode == 0 else None
            except (OSError, subprocess.SubprocessError):
                git_log = None
        experience_path = domain / "EXPERIENCE.md"
        experience = _read_bounded(experience_path) if experience_path.is_file() else None
        domains.append(
            LibraryDomain(
                name=domain.name,
                path=domain,
                git_versioned=git_versioned,
                git_log=git_log,
                experience=experience,
            )
        )
    for pattern in ("*/library", "*/*/library"):
        for root in sorted(runs_dir.glob(pattern)):
            if not root.is_dir():
                continue
            for index in sorted(root.glob("*/*/*/*/INDEX.md")):
                if not index.is_file():
                    continue
                domain = index.parent
                run, mode = domain.relative_to(root).parts[-3:-1]
                sections = []
                for path in sorted((domain / "harnesses").glob("*/EXPERIENCE.md")):
                    if path.is_file():
                        relative = path.relative_to(domain).as_posix()
                        sections.append(f"### {relative}\n{_read_bounded(path)}")
                domains.append(
                    LibraryDomain(
                        name=f"{domain.name} ({run}/{mode})",
                        path=domain,
                        git_versioned=False,
                        git_log=None,
                        experience="\n\n".join(sections) or None,
                    )
                )
    return sorted(domains, key=lambda domain: (domain.name, domain.path.as_posix()))
