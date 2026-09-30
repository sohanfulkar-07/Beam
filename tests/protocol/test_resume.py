"""
Tests: ResumeManager — persist and restore transfer state
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'protocol'))

import pytest
from pathlib import Path
from src.resume import ResumeManager


def make_manager(tmp_path) -> ResumeManager:
    return ResumeManager(state_dir=tmp_path / "resume")


def test_save_and_load_state(tmp_path):
    rm = make_manager(tmp_path)
    rm.save_state(
        session_id="sess1",
        file_id="file1",
        file_name="photo.jpg",
        file_size=10000,
        total_chunks=3,
        received_chunks=[0, 1],
        dest_tmp_path="/tmp/photo.pbtemp",
        sha256="abc123",
        chunk_size=4096,
    )
    state = rm.load_state("sess1", "file1")
    assert state is not None
    assert state["file_name"] == "photo.jpg"
    assert state["received_chunks"] == [0, 1]
    assert state["total_chunks"] == 3
    assert state["sha256"] == "abc123"


def test_load_nonexistent_returns_none(tmp_path):
    rm = make_manager(tmp_path)
    assert rm.load_state("sess-x", "file-x") is None


def test_clear_state(tmp_path):
    rm = make_manager(tmp_path)
    rm.save_state("s", "f", "file.bin", 100, 1, [0], "/tmp/f.pbtemp", "sha", 100)
    rm.clear_state("s", "f")
    assert rm.load_state("s", "f") is None


def test_list_resumable(tmp_path):
    rm = make_manager(tmp_path)
    rm.save_state("s1", "f1", "a.bin", 100, 1, [0], "/tmp/a", "sha1", 100)
    rm.save_state("s1", "f2", "b.bin", 200, 2, [0, 1], "/tmp/b", "sha2", 100)
    rm.save_state("s2", "f3", "c.bin", 300, 3, [], "/tmp/c", "sha3", 100)

    resumable = rm.list_resumable("s1")
    assert len(resumable) == 2
    names = {r["file_name"] for r in resumable}
    assert names == {"a.bin", "b.bin"}


def test_clear_session(tmp_path):
    rm = make_manager(tmp_path)
    rm.save_state("s1", "f1", "a.bin", 100, 1, [], "/tmp/a", "sha1", 100)
    rm.save_state("s1", "f2", "b.bin", 100, 1, [], "/tmp/b", "sha2", 100)
    rm.save_state("s2", "f3", "c.bin", 100, 1, [], "/tmp/c", "sha3", 100)
    rm.clear_session("s1")
    assert rm.load_state("s1", "f1") is None
    assert rm.load_state("s1", "f2") is None
    assert rm.load_state("s2", "f3") is not None  # different session, untouched


def test_save_overwrites_previous(tmp_path):
    rm = make_manager(tmp_path)
    rm.save_state("s", "f", "file.bin", 100, 3, [0], "/tmp/f", "sha", 100)
    rm.save_state("s", "f", "file.bin", 100, 3, [0, 1, 2], "/tmp/f", "sha", 100)
    state = rm.load_state("s", "f")
    assert state["received_chunks"] == [0, 1, 2]
