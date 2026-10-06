"""Russian-language CLI. The demo and rendering never contact the cloud."""
from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

import imageio_ffmpeg
import numpy as np

from .config import Look, Project, load_project
from .media import audio_samples, ffmpeg, prepare_motion, render
from .provider import EDIT_MODEL, TEXT_MODEL, VIDEO_MODEL, FalProvider
from .storage import workspace_lock, write_json


def generate(project: Project, stage: str, allow_paid: bool, look_id: str | None) -> list[Path]:
    selected = [look for look in project.looks if look_id is None or look.id == look_id]
    if not selected:
        raise ValueError(f"Неизвестный образ: {look_id}")
    if stage == "character" and look_id:
        raise ValueError("--look применяется к образам и клипам, не к героине.")
    # The permission gate happens before creating jobs or sending any files.
    provider = FalProvider(allow_paid)
    common = {"image_size": {"width": 720, "height": 1280}, "num_images": 1,
              "output_format": "png", "enable_safety_checker": True, "seed": project.seed}
    base = project.workdir / "character.png"
    with workspace_lock(project.workdir):
        if stage == "character":
            return [provider.produce(TEXT_MODEL, {**common, "prompt": project.character_prompt}, {}, base)]
        if stage == "looks":
            if not base.is_file():
                raise FileNotFoundError(f"Сначала создайте и утвердите героиню: {base}")
            return [provider.produce(EDIT_MODEL, {
                **common, "prompt": "Keep the same adult woman's face, hair, body proportions, "
                "pose, framing and lighting. Change only the outfit and background. " + look.prompt,
            }, {"image_urls": [base]}, project.image(look)) for look in selected]
        for look in selected:
            if not project.image(look).is_file():
                raise FileNotFoundError(f"Сначала создайте образ: {project.image(look)}")
        # Fail on missing/short audio before incurring any video-generation costs.
        audio_samples(project.music, project.music_start, project.duration)
        motion = prepare_motion(project)
        return [provider.produce(VIDEO_MODEL, {
            "prompt": "Static full-body studio camera. Preserve the adult character's face, "
                      "outfit and body proportions from the image. Follow the reference dance.",
            "character_orientation": "video", "keep_original_sound": False,
        }, {"image_url": project.image(look), "video_url": motion}, project.clip(look)) for look in selected]


def demo(root: Path) -> Path:
    root = root.resolve()
    project = Project(root, root / "unused.mp4", root / "music.wav", "Technical fixture, not AI",
                      tuple(Look(f"look_{i}", "Technical test clip") for i in range(1, 4)),
                      seconds=6, width=180, height=320)
    with workspace_lock(root):
        # The diagnostic demo must not overwrite a real character workspace.
        if (root / "character.png").exists() or list(root.rglob("*.job.json")):
            raise ValueError("Для demo используйте отдельный каталог без AI-генераций.")
        for index, look in enumerate(project.looks):
            clip = project.clip(look)
            clip.parent.mkdir(parents=True, exist_ok=True)
            ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=180x320:rate=30:duration=6",
                    "-vf", f"hue=h={index * 100}", "-an", "-c:v", "libx264",
                    "-threads", "2", "-preset", "fast", "-pix_fmt", "yuv420p", str(clip)])
        sample_rate = 44100
        t = np.arange(round(project.duration * sample_rate)) / sample_rate
        sound = 0.035 * np.sin(2 * np.pi * 220 * t)
        for beat in np.arange(0, project.duration, 0.5):
            start = round(beat * sample_rate)
            tail = np.arange(min(4410, len(sound) - start)) / sample_rate
            sound[start:start + len(tail)] += 0.6 * np.exp(-tail * 65) * np.sin(2 * np.pi * 900 * tail)
        with wave.open(str(project.music), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(sample_rate)
            stream.writeframes((np.clip(sound, -1, 1) * 32767).astype("<i2").tobytes())
    output = render(project)
    write_json(root / "DEMO.json", {"synthetic": True, "ai_generated": False,
                                   "purpose": "Тест таймлайна, кодека, музыки и смены клипов."})
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Localmodel: виртуальная героиня, танец и автоматический монтаж")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Проверка Python и FFmpeg, без сетевых запросов")
    demo_parser = commands.add_parser("demo", help="Бесплатный тест сборки, без AI-генерации")
    demo_parser.add_argument("--output", type=Path, default=Path("workspace/demo"))
    for name, help_text in [("plan", "План запросов без оплаты"), ("render", "Сборка готовых клипов")]:
        child = commands.add_parser(name, help=help_text)
        child.add_argument("--config", type=Path, default=Path("project.toml"))
    child = commands.add_parser("generate", help="Платная генерация: героиня, образы или танцы")
    child.add_argument("stage", choices=["character", "looks", "clips"])
    child.add_argument("--config", type=Path, default=Path("project.toml"))
    child.add_argument("--look", help="Генерировать только один образ по id")
    child.add_argument("--allow-paid", action="store_true", help="Разрешить платный запрос и передачу файлов fal")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            print(f"Python: {sys.version.split()[0]}\nFFmpeg: {imageio_ffmpeg.get_ffmpeg_exe()}")
            ffmpeg(["-version"])
            print("Локальный видеодвижок доступен. Облачные модели не проверялись.")
        elif args.command == "demo":
            print(f"Техническое демо, не AI-девушка: {demo(args.output)}")
        else:
            project = load_project(args.config)
            if args.command == "plan":
                print(json.dumps({"workdir": str(project.workdir), "output_seconds": project.duration,
                                  "character_images": 1, "look_images": len(project.looks),
                                  "video_requests": len(project.looks),
                                  "total_generated_video_seconds": project.duration * len(project.looks),
                                  "models": [TEXT_MODEL, EDIT_MODEL, VIDEO_MODEL],
                                  "note": "План первого полного запуска без повторов. Это не цена в валюте. "
                                          "Каждый образ генерирует полный танец, а не только свой отрезок."},
                                 ensure_ascii=False, indent=2))
            elif args.command == "render":
                print(f"Готово: {render(project)}")
            else:
                for result in generate(project, args.stage, args.allow_paid, args.look):
                    print(f"Сохранено: {result}")
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Остановлено локально. Уже отправленная облачная задача не отменяется автоматически.", file=sys.stderr)
        return 130
