"""FFmpeg subprocesses without shell commands or a system-wide installation."""
from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg
import numpy as np

from .config import Project
from .storage import file_hash, workspace_lock, write_json

SAMPLE_RATE = 22050


def ffmpeg(arguments: list[str], timeout: int = 300) -> bytes:
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
               "-nostdin", "-y", "-threads", "2", *map(str, arguments)]
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"FFmpeg превысил лимит {timeout} секунд.") from None
    if result.returncode:
        raise RuntimeError("FFmpeg: " + result.stderr.decode("utf-8", errors="replace")[-3000:])
    return result.stdout


def probe(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"Нет файла: {path}")
    reader = imageio_ffmpeg.read_frames(str(path))
    try:
        return next(reader)
    finally:
        reader.close()


def fit_filter(project: Project) -> str:
    return (
        f"fps={project.fps},scale={project.width}:{project.height}:"
        "force_original_aspect_ratio=decrease:force_divisible_by=2,"
        f"pad={project.width}:{project.height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )


def prepare_motion(project: Project) -> Path:
    metadata = probe(project.dance)
    if metadata["duration"] + 0.02 < project.dance_start + project.duration:
        raise ValueError("Референс танца короче выбранного отрезка.")
    output = project.workdir / "motion.mp4"
    if output.resolve() == project.dance.resolve():
        raise ValueError("Исходник танца нельзя перезаписать подготовленным видео.")
    # Encode every look from the same trimmed and normalized motion reference.
    temporary = output.with_name("motion.part.mp4")
    ffmpeg(["-ss", str(project.dance_start), "-i", str(project.dance), "-an",
            "-vf", fit_filter(project), "-frames:v", str(project.frames),
            "-c:v", "libx264", "-threads", "2", "-preset", "fast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(temporary)])
    temporary.replace(output)
    return output


def audio_samples(path: Path, start: float, duration: float) -> np.ndarray:
    if not path.is_file():
        raise FileNotFoundError(f"Нет музыки: {path}")
    raw = ffmpeg(["-ss", str(start), "-i", str(path), "-t", str(duration),
                  "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
                  "-f", "f32le", "pipe:1"])
    samples = np.frombuffer(raw, dtype="<f4")
    if len(samples) < round(duration * SAMPLE_RATE) - 2:
        raise ValueError("Музыка короче ролика. Автоматического зацикливания нет.")
    if not np.isfinite(samples).all():
        raise ValueError("Аудио содержит некорректные отсчёты.")
    return samples


def beat_times(samples: np.ndarray) -> list[float]:
    if samples.size == 0 or float(np.max(np.abs(samples))) < 1e-6:
        return []
    import librosa

    _, beats = librosa.beat.beat_track(y=samples, sr=SAMPLE_RATE, units="time", trim=False)
    return [float(t) for t in np.asarray(beats).reshape(-1) if np.isfinite(t)]


def cut_frames(frames: int, fps: int, count: int, beats: list[float]) -> list[int]:
    """Use each look once; snap near equally spaced targets to detected beats."""
    if frames <= 0 or fps <= 0 or count < 1 or frames < count * fps:
        raise ValueError("Недостаточно кадров для выбранного количества образов.")
    candidates = sorted({round(t * fps) for t in beats if np.isfinite(t) and t > 0})
    cuts = [0]
    for index in range(1, count):
        target = round(frames * index / count)
        lower = cuts[-1] + fps
        upper = frames - (count - index) * fps
        nearby = [b for b in candidates if lower <= b <= upper and abs(b - target) <= fps * 0.6]
        cuts.append(min(nearby, key=lambda b: (abs(b - target), b)) if nearby
                    else min(upper, max(lower, target)))
    return cuts + [frames]


def render(project: Project) -> Path:
    with workspace_lock(project.workdir):
        clips = [project.clip(look) for look in project.looks]
        for path in clips:
            metadata = probe(path)
            if metadata["duration"] + 0.02 < project.duration:
                raise ValueError(f"Клип короче общей шкалы танца: {path}")
        samples = audio_samples(project.music, project.music_start, project.duration)
        beats = beat_times(samples)
        cuts = cut_frames(project.frames, project.fps, len(clips), beats)
        segments = []
        filters = []
        arguments = ["-filter_complex_threads", "1"]
        for index, (look, clip) in enumerate(zip(project.looks, clips)):
            arguments += ["-i", str(clip)]
            start, end = cuts[index:index + 2]
            # The second clip uses the SECOND interval, not its first seconds.
            filters.append(f"[{index}:v]{fit_filter(project)},trim=start_frame={start}:"
                           f"end_frame={end},setpts=PTS-STARTPTS[v{index}]")
            segments.append({"look": look.id, "source": str(clip), "sha256": file_hash(clip),
                             "start_frame": start, "end_frame": end,
                             "source_start": start / project.fps, "source_end": end / project.fps})
        filters.append("".join(f"[v{i}]" for i in range(len(clips)))
                       + f"concat=n={len(clips)}:v=1:a=0[outv]")
        arguments += ["-ss", str(project.music_start), "-i", str(project.music)]
        filters.append(f"[{len(clips)}:a]atrim=duration={project.duration},asetpts=PTS-STARTPTS[outa]")
        output = project.workdir / "final.mp4"
        if output.resolve() in {p.resolve() for p in [*clips, project.music, project.dance]}:
            raise ValueError("Выходной файл совпадает с исходником.")
        temporary = output.with_name("final.part.mp4")
        ffmpeg([*arguments, "-filter_complex", ";".join(filters), "-map", "[outv]", "-map", "[outa]",
                "-frames:v", str(project.frames), "-r", str(project.fps), "-fps_mode", "cfr", "-t", str(project.duration),
                "-c:v", "libx264", "-threads", "2", "-preset", "fast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                "-ac", "2", "-movflags", "+faststart", str(temporary)])
        # Decode output once: verify frame count, not just container duration.
        actual_frames, _ = imageio_ffmpeg.count_frames_and_secs(str(temporary))
        if actual_frames != project.frames:
            temporary.unlink(missing_ok=True)
            raise RuntimeError(f"Неполный экспорт: {actual_frames} кадров вместо {project.frames}.")
        temporary.replace(output)
        write_json(project.workdir / "timeline.json", {
            "version": 1, "output": str(output), "output_sha256": file_hash(output),
            "frames": actual_frames, "fps": project.fps, "seconds": project.duration,
            "resolution": [project.width, project.height], "cuts": cuts,
            "beats_seconds": beats, "timing_mode": "beat_snap" if beats else "uniform_fallback",
            "music": str(project.music), "music_sha256": file_hash(project.music),
            "music_start": project.music_start, "segments": segments,
            "warnings": ([] if beats else ["Биты не найдены; равномерные переходы."])
                        + ["Сходство лица и совпадение поз автоматически не проверяются."],
        })
        return output
