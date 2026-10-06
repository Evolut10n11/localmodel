"""Validated TOML configuration. Paths are relative to the configuration file."""
from __future__ import annotations

import math
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Look:
    id: str
    prompt: str


@dataclass(frozen=True)
class Project:
    workdir: Path
    dance: Path
    music: Path
    character_prompt: str
    looks: tuple[Look, ...]
    seconds: float = 12.0
    dance_start: float = 0.0
    music_start: float = 0.0
    width: int = 720
    height: int = 1280
    fps: int = 30
    seed: int = 42

    def __post_init__(self) -> None:
        if not math.isfinite(self.seconds) or not 3 <= self.seconds <= 30:
            raise ValueError("Длительность должна быть от 3 до 30 секунд.")
        for value in (self.dance_start, self.music_start):
            if not math.isfinite(value) or value < 0:
                raise ValueError("Начальные таймкоды должны быть конечными и неотрицательными.")
        if self.fps not in (24, 25, 30, 60):
            raise ValueError("fps: 24, 25, 30 или 60.")
        if any(type(v) is not int or v % 2 for v in (self.width, self.height)):
            raise ValueError("Ширина и высота должны быть чётными целыми числами.")
        if not (90 <= self.width <= 2160 and 160 <= self.height <= 3840):
            raise ValueError("Недопустимое разрешение видео.")
        if self.width * 16 != self.height * 9:
            raise ValueError("Нужен вертикальный формат 9:16.")
        if not 1 <= len(self.looks) <= 6 or self.seconds / len(self.looks) < 1:
            raise ValueError("Нужно 1–6 образов и минимум секунда на каждый.")
        ids = [look.id for look in self.looks]
        if len(ids) != len(set(ids)):
            raise ValueError("Идентификаторы образов должны быть уникальными.")
        if any(not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", name) for name in ids):
            raise ValueError("id образа: латинские строчные буквы, цифры, _ и -.")
        if not self.character_prompt.strip() or any(not x.prompt.strip() for x in self.looks):
            raise ValueError("Промпты не должны быть пустыми.")
        if type(self.seed) is not int or not 0 <= self.seed < 2**31:
            raise ValueError("seed должен быть целым числом от 0 до 2147483647.")

    @property
    def frames(self) -> int:
        return round(self.seconds * self.fps)

    @property
    def duration(self) -> float:
        return self.frames / self.fps

    def image(self, look: Look) -> Path:
        return self.workdir / "looks" / f"{look.id}.png"

    def clip(self, look: Look) -> Path:
        return self.workdir / "clips" / f"{look.id}.mp4"


def load_project(path: Path) -> Project:
    path = path.resolve()
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    allowed = {"project", "character", "looks"}
    if set(data) - allowed:
        raise ValueError(f"Неизвестные разделы: {sorted(set(data) - allowed)}")
    settings = dict(data["project"])
    root = path.parent
    for key in ("workdir", "dance", "music"):
        settings[key] = (root / settings[key]).resolve()
    return Project(
        **settings,
        character_prompt=data["character"]["prompt"],
        looks=tuple(Look(**item) for item in data["looks"]),
    )
