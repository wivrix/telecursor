"""Tests for multi-project registry."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import paths
import projects


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "telecursor-home"
    home.mkdir()
    monkeypatch.setenv("TELECURSOR_HOME", str(home))
    paths.clear_path_caches()
    yield home
    paths.clear_path_caches()
    monkeypatch.delenv("TELECURSOR_HOME", raising=False)


def test_register_unregister_and_unique_slug(isolated_home: Path, tmp_path: Path) -> None:
    a = tmp_path / "App"
    b = tmp_path / "other" / "App"
    a.mkdir()
    b.mkdir(parents=True)

    p1 = projects.register_project(a)
    p2 = projects.register_project(b)
    assert p1.id == "app"
    assert p2.id == "app-2"
    assert p1.name == "App"
    assert projects.get_project(p1.id) is not None

    # Idempotent re-register
    again = projects.register_project(a)
    assert again.id == p1.id

    assert projects.unregister_project(p1.id) is True
    assert projects.get_project(p1.id) is None
    assert projects.unregister_project("missing") is False


def test_find_project_prefers_deepest(isolated_home: Path, tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    projects.register_project(parent, name="parent")
    projects.register_project(child, name="child")

    found = projects.find_project_for_path(child)
    assert found is not None
    assert found.name == "child"

    found_parent = projects.find_project_for_path(parent)
    assert found_parent is not None
    assert found_parent.name == "parent"
