"""Registered project roots for multi-project Telegram workflows."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from paths import app_home


@dataclass
class Project:
    id: str
    name: str
    path: str  # absolute path string
    registered_at: str

    @property
    def root(self) -> Path:
        return Path(self.path)


def projects_path() -> Path:
    return app_home() / "projects.json"


def _slug_from_path(path: Path) -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "-", path.name.strip()).strip("-").lower()
    return base or "project"


def _unique_id(path: Path, existing: dict[str, Project]) -> str:
    base = _slug_from_path(path)
    if base not in existing:
        return base
    # Same path already registered under another id?
    resolved = str(path.resolve())
    for proj in existing.values():
        if proj.path == resolved:
            return proj.id
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


def load_projects() -> dict[str, Project]:
    path = projects_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    items = raw.get("projects", raw if isinstance(raw, list) else [])
    out: dict[str, Project] = {}
    if isinstance(items, dict):
        items = list(items.values())
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            proj = Project(
                id=str(item["id"]),
                name=str(item.get("name") or item["id"]),
                path=str(Path(item["path"]).expanduser().resolve()),
                registered_at=str(item.get("registered_at") or ""),
            )
        except (KeyError, TypeError, OSError):
            continue
        out[proj.id] = proj
    return out


def save_projects(projects: dict[str, Project]) -> None:
    path = projects_path()
    payload = {
        "projects": [asdict(p) for p in sorted(projects.values(), key=lambda p: p.name.lower())]
    }
    data = json.dumps(payload, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix="projects.",
        suffix=".json.tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def register_project(path: Path | str, *, name: str | None = None) -> Project:
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")
    projects = load_projects()
    resolved = str(root)
    for existing in projects.values():
        if existing.path == resolved:
            return existing
    pid = _unique_id(root, projects)
    proj = Project(
        id=pid,
        name=name or root.name,
        path=resolved,
        registered_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    )
    projects[pid] = proj
    save_projects(projects)
    return proj


def unregister_project(project_id: str) -> bool:
    projects = load_projects()
    if project_id not in projects:
        return False
    del projects[project_id]
    save_projects(projects)
    return True


def get_project(project_id: str) -> Project | None:
    return load_projects().get(project_id)


def list_projects() -> list[Project]:
    return sorted(load_projects().values(), key=lambda p: p.name.lower())


def find_project_for_path(path: Path | str) -> Project | None:
    """Return the most specific (deepest) registered project containing path."""
    target = Path(path).expanduser().resolve()
    best: Project | None = None
    best_depth = -1
    for proj in list_projects():
        try:
            target.relative_to(proj.root.resolve())
        except ValueError:
            continue
        depth = len(proj.root.resolve().parts)
        if depth > best_depth:
            best = proj
            best_depth = depth
    return best


def is_under_any_project(path: Path, projects: list[Project] | None = None) -> bool:
    return find_by_path(path, projects) is not None


def find_by_path(path: Path, projects: list[Project] | None = None) -> Project | None:
    target = path.expanduser().resolve()
    best: Project | None = None
    best_depth = -1
    for proj in projects if projects is not None else list_projects():
        try:
            target.relative_to(proj.root.resolve())
        except ValueError:
            continue
        depth = len(proj.root.resolve().parts)
        if depth > best_depth:
            best = proj
            best_depth = depth
    return best
