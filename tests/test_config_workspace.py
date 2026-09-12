"""Tests for workspace path confinement."""

from __future__ import annotations

from pathlib import Path

import pytest

from config import validate_workspace


def test_validate_workspace_accepts_subpath(tmp_path: Path) -> None:
    root = tmp_path / "project"
    sub = root / "src"
    sub.mkdir(parents=True)
    resolved = validate_workspace(sub, root)
    assert resolved == sub.resolve()


def test_validate_workspace_accepts_relative_under_root(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "docs").mkdir(parents=True)
    resolved = validate_workspace("docs", root)
    assert resolved == (root / "docs").resolve()


def test_validate_workspace_rejects_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "other"
    outside.mkdir()
    with pytest.raises(ValueError, match="outside"):
        validate_workspace(outside, root)


def test_validate_workspace_allows_extra_registered_roots(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    nested = root_b / "nested"
    nested.mkdir()
    resolved = validate_workspace(nested, root_a, extra_roots=[root_b])
    assert resolved == nested.resolve()


def test_validate_workspace_rejects_missing_dir(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    with pytest.raises(ValueError, match="does not exist"):
        validate_workspace(root / "missing", root)
