import json
from dataclasses import replace

import imageio_ffmpeg
import numpy as np
import pytest

from localmodel.cli import demo
from localmodel.config import Look, Project
from localmodel.media import audio_samples, ffmpeg, prepare_motion, probe, render


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    root = tmp_path_factory.mktemp("media test with spaces")
    output = demo(root)
    project = Project(root, root / "clips/look_1.mp4", root / "music.wav", "test adult",
                      tuple(Look(f"look_{i}", "test") for i in range(1, 4)),
                      seconds=6, width=180, height=320)
    return project, output


def test_end_to_end_frame_count_resolution_audio(rendered):
    project, output = rendered
    metadata = probe(output)
    assert metadata["size"] == (180, 320)
    assert metadata["fps"] == 30
    assert metadata["codec"] == "h264"
    assert metadata["audio_codec"] == "aac"
    assert imageio_ffmpeg.count_frames_and_secs(str(output))[0] == 180
    assert abs(metadata["duration"] - 6) < 0.05
    audio = audio_samples(output, 0, 5.9)
    assert np.max(np.abs(audio)) > 0.1
    timeline = json.loads((project.workdir / "timeline.json").read_text())
    assert timeline["timing_mode"] == "beat_snap"
    assert timeline["segments"][1]["source_start"] > 1
    assert timeline["segments"][2]["source_start"] > 3


def frame(path, time):
    raw = ffmpeg(["-ss", str(time), "-i", str(path), "-frames:v", "1",
                  "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"])
    return np.frombuffer(raw, dtype=np.uint8).astype(float)


def test_export_uses_correct_source_time_not_clip_restart(rendered):
    project, output = rendered
    # All sources have visible moving test patterns. At t=3, the second look must
    # come from t=3 of its source, not t=1 (restarted at the second cut).
    exported = frame(output, 3)
    correct = frame(project.clip(project.looks[1]), 3)
    restarted = frame(project.clip(project.looks[1]), 1)
    assert np.mean(np.abs(exported - correct)) < 8
    assert np.mean(np.abs(exported - correct)) < np.mean(np.abs(exported - restarted))


def test_audio_too_short_is_not_looped(rendered):
    project, _ = rendered
    with pytest.raises(ValueError, match="короче"):
        audio_samples(project.music, 0, 8)


def test_short_clip_refused(rendered):
    project, _ = rendered
    with pytest.raises(ValueError, match="короче"):
        render(replace(project, seconds=9))


def test_motion_preparation_uses_selected_time(rendered):
    project, _ = rendered
    short = replace(project, seconds=3, dance_start=1)
    path = prepare_motion(short)
    assert imageio_ffmpeg.count_frames_and_secs(str(path))[0] == 90
    assert np.mean(np.abs(frame(path, 0) - frame(project.dance, 1))) < 8


def test_demo_marked_as_not_ai(rendered):
    project, _ = rendered
    assert json.loads((project.workdir / "DEMO.json").read_text())["ai_generated"] is False


def test_existing_ai_workspace_protected(tmp_path):
    (tmp_path / "character.png").write_bytes(b"my reference")
    with pytest.raises(ValueError):
        demo(tmp_path)
    assert (tmp_path / "character.png").read_bytes() == b"my reference"
