"""Focused tests for BackupProgress with an empty (total=0) workload."""

import importlib.util
from pathlib import Path

import pytest


def load_backup_module():
    spec = importlib.util.spec_from_file_location(
        "backup_under_test", Path(__file__).resolve().parent / "backup.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_draw_progress_bar_with_zero_total_does_not_crash(capsys):
    backup = load_backup_module()
    progress = backup.BackupProgress(0, "Compressing backup...", "compressing")

    progress.draw_progress_bar("file.txt")

    out, _ = capsys.readouterr()
    assert "file.txt" in out


def test_draw_progress_bar_reports_full_progress(capsys):
    backup = load_backup_module()
    progress = backup.BackupProgress(2, "Compressing backup...", "compressing")

    progress.draw_progress_bar("a.txt")
    progress.draw_progress_bar("b.txt")

    out, _ = capsys.readouterr()
    assert "100.0%" in out
