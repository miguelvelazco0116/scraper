from pathlib import Path

import pytest

from scraper.io_utils import atomic_output_path, exclusive_file_lock


def test_atomic_output_path_replaces_existing_file(tmp_path: Path):
    target = tmp_path / "artifact.txt"
    target.write_text("old", encoding="utf-8")

    with atomic_output_path(target) as temporary:
        temporary.write_text("new", encoding="utf-8")
        assert target.read_text(encoding="utf-8") == "old"

    assert target.read_text(encoding="utf-8") == "new"
    assert not temporary.exists()


def test_atomic_output_path_preserves_previous_file_on_error(
    tmp_path: Path,
):
    target = tmp_path / "artifact.txt"
    target.write_text("stable", encoding="utf-8")

    with pytest.raises(RuntimeError):
        with atomic_output_path(target) as temporary:
            temporary.write_text("partial", encoding="utf-8")
            raise RuntimeError("simulated write failure")

    assert target.read_text(encoding="utf-8") == "stable"
    assert not temporary.exists()



def test_exclusive_file_lock_blocks_second_writer(tmp_path: Path):
    target = tmp_path / "shared.xlsx"

    with exclusive_file_lock(target):
        with pytest.raises(TimeoutError):
            with exclusive_file_lock(
                target,
                timeout_seconds=0.05,
                stale_seconds=999.0,
                poll_seconds=0.01,
            ):
                pass

    assert not (tmp_path / "shared.xlsx.lock").exists()
