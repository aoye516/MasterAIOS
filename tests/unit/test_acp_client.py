"""Unit tests for aios.acp.client (no live `claude` required)."""

from pathlib import Path

import pytest

from aios.acp.client import (
    TASK_NAME_RE,
    _last_session_id_for_cwd,
    list_tasks,
    task_session_path,
)


@pytest.mark.parametrize(
    "name",
    ["hello-py", "a", "0refactor-x1", "task-with-many-hyphens-still-valid"],
)
def test_valid_task_names(name: str):
    assert TASK_NAME_RE.match(name)


@pytest.mark.parametrize(
    "name",
    ["", "Hello", "task_1", "-foo", "with space", "x" * 65],
)
def test_invalid_task_names(name: str):
    assert not TASK_NAME_RE.match(name)


def test_task_session_path_creates_dir(tmp_path: Path):
    cwd = task_session_path("smoke-test", root=tmp_path)
    assert cwd.exists() and cwd.is_dir()
    assert cwd.name == "smoke-test"


def test_task_session_path_rejects_bad_name(tmp_path: Path):
    with pytest.raises(ValueError):
        task_session_path("Bad Name", root=tmp_path)


def test_list_tasks_returns_sorted(tmp_path: Path):
    (tmp_path / "task-b").mkdir()
    (tmp_path / "task-a").mkdir()
    (tmp_path / ".hidden").mkdir()  # included as just another dir
    assert list_tasks(root=tmp_path) == [".hidden", "task-a", "task-b"]


def test_last_session_id_returns_none_when_missing(tmp_path: Path):
    assert _last_session_id_for_cwd(tmp_path / "nonexistent") is None
