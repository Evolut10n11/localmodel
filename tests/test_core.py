import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from localmodel.cli import main
from localmodel.config import Look, Project, load_project
from localmodel.media import beat_times, cut_frames
from localmodel.storage import workspace_lock, write_json


@pytest.fixture
def project(tmp_path):
    return Project(tmp_path, tmp_path / "dance.mp4", tmp_path / "music.wav", "Adult fictional woman",
                   (Look("pink", "Pink clothes"), Look("blue", "Blue clothes")))


@pytest.mark.parametrize("seconds", [0, 2, 31, float("nan"), float("inf")])
def test_rejects_invalid_duration(project, seconds):
    with pytest.raises(ValueError):
        replace(project, seconds=seconds)


@pytest.mark.parametrize("kwargs", [
    {"width": 721}, {"height": 720}, {"fps": 29}, {"music_start": -1},
    {"dance_start": float("nan")}, {"looks": ()},
    {"looks": (Look("../escape", "x"),)},
    {"looks": (Look("pink", "x"), Look("pink", "y"))},
    {"looks": (Look("pink", ""),)}, {"seed": -1},
])
def test_rejects_invalid_config(project, kwargs):
    with pytest.raises(ValueError):
        replace(project, **kwargs)


def test_example_and_relative_paths(monkeypatch, tmp_path):
    source = Path(__file__).resolve().parents[1] / "project.toml"
    config = tmp_path / "project.toml"
    config.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.chdir(tmp_path.parent)
    loaded = load_project(config)
    assert loaded.workdir == tmp_path / "workspace" / "elaine"
    assert len(loaded.looks) == 3


def test_cut_frames_absolute_intervals():
    assert cut_frames(360, 30, 3, []) == [0, 120, 240, 360]
    assert cut_frames(360, 30, 3, [3.9, 8.1]) == [0, 117, 243, 360]


def test_cut_frames_deduplicates_out_of_range_beats():
    assert cut_frames(180, 30, 3, [-1, float("nan"), 2, 2, 4, 100]) == [0, 60, 120, 180]


def test_cut_frames_single_look():
    assert cut_frames(90, 30, 1, [1]) == [0, 90]


def test_short_segments_rejected():
    with pytest.raises(ValueError):
        cut_frames(59, 30, 2, [])


def test_silence_does_not_invent_beats():
    assert beat_times(np.zeros(44100)) == []


def test_workspace_lock_and_cleanup(tmp_path):
    with workspace_lock(tmp_path):
        with pytest.raises(RuntimeError):
            with workspace_lock(tmp_path):
                pytest.fail("Should not enter a locked workspace")
    assert not (tmp_path / ".running.lock").exists()


def test_json_atomic_unicode(tmp_path):
    path = tmp_path / "state.json"
    write_json(path, {"state": "Готово"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"state": "Готово"}
    assert not path.with_name("state.json.tmp").exists()


def test_paid_cli_gate(monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    config = Path(__file__).resolve().parents[1] / "project.toml"
    assert main(["generate", "character", "--config", str(config)]) == 2


def test_plan_is_offline(capsys):
    config = Path(__file__).resolve().parents[1] / "project.toml"
    assert main(["plan", "--config", str(config)]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["total_generated_video_seconds"] == 36
