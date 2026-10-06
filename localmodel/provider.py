"""fal queue REST adapter. No credentials, media or paid requests in tests."""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .storage import file_hash, write_json

TEXT_MODEL = "fal-ai/flux-2"
EDIT_MODEL = "fal-ai/flux-2/edit"
VIDEO_MODEL = "fal-ai/kling-video/v2.6/pro/motion-control"
ALLOWED_MODELS = {TEXT_MODEL, EDIT_MODEL, VIDEO_MODEL}
MAX_INPUT_BYTES = 32 * 1024 * 1024


def validate_url(url: str, *, queue: bool = False) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Разрешены только HTTPS-адреса без встроенных паролей.")
    if queue and (parsed.hostname != "queue.fal.run" or parsed.port not in (None, 443)):
        raise ValueError("Ключ нельзя отправлять за пределы queue.fal.run.")
    return url


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Especially important for requests containing the API key.
        raise RuntimeError("HTTP redirect отклонён. Проверьте endpoint провайдера.")


class Transport:
    def __init__(self, key: str):
        self.key = key
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method: str, url: str, payload: dict | None = None) -> dict:
        validate_url(url, queue=True)
        data = None if payload is None else json.dumps(payload, allow_nan=False).encode()
        request = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": "Key " + self.key, "Content-Type": "application/json",
        })
        # Never automatically retry a paid POST. GET can be resumed by rerunning the command.
        try:
            with self.opener.open(request, timeout=60) as response:
                result = json.loads(response.read(2 * 1024 * 1024))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"fal HTTP {exc.code}. Проверьте запрос в личном кабинете.") from None
        except (urllib.error.URLError, TimeoutError):
            raise RuntimeError("Нет ответа fal. Состояние сохранено; автоматической повторной оплаты нет.") from None
        if not isinstance(result, dict):
            raise RuntimeError("fal вернул неожиданный формат JSON.")
        return result

    def download(self, url: str, target: Path) -> None:
        validate_url(url)
        temporary = target.with_name(target.name + ".part")
        # No authorization header on the media download.
        try:
            with self.opener.open(url, timeout=120) as response, temporary.open("wb") as stream:
                received = 0
                while chunk := response.read(1024 * 1024):
                    received += len(chunk)
                    if received > 1024 * 1024 * 1024:
                        raise RuntimeError("Результат больше 1 ГБ.")
                    stream.write(chunk)
            if not temporary.stat().st_size:
                raise RuntimeError("Провайдер вернул пустой файл.")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


def data_uri(path: Path) -> str:
    size = path.stat().st_size
    if not 0 < size <= MAX_INPUT_BYTES:
        raise ValueError(f"Входной файл должен быть от 1 байта до 32 МиБ: {path}")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


class FalProvider:
    def __init__(self, allow_paid: bool, transport=None, timeout: float = 1800, poll: float = 3):
        if not allow_paid:
            raise PermissionError("Генерация платная и отправляет файлы в облако. Нужен --allow-paid.")
        if transport is None:
            key = os.environ.get("FAL_KEY", "").strip()
            if not key:
                raise ValueError("Не задан FAL_KEY. Ключ вводится локально, не в GitHub и не в чат.")
            transport = Transport(key)
        self.transport = transport
        self.timeout = timeout
        self.poll = poll

    def produce(self, endpoint: str, arguments: dict, files: dict, target: Path) -> Path:
        if endpoint not in ALLOWED_MODELS:
            raise ValueError("Endpoint не входит в проверенный список моделей.")
        fingerprints = {}
        for key, value in files.items():
            paths = value if isinstance(value, list) else [value]
            for path in paths:
                if not path.is_file() or not 0 < path.stat().st_size <= MAX_INPUT_BYTES:
                    raise ValueError(f"Нет входного файла или он превышает 32 МиБ: {path}")
            fingerprints[key] = [file_hash(path) for path in paths]
        identity = {"endpoint": endpoint, "arguments": arguments, "files": fingerprints}
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        target.parent.mkdir(parents=True, exist_ok=True)
        state_path = target.with_name(target.name + ".job.json")
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state["fingerprint"] != fingerprint:
                raise ValueError("Параметры изменились. Используйте новый workdir, чтобы не затереть результат.")
            if target.exists() and state.get("sha256") == file_hash(target):
                return target
            if not state.get("request_id"):
                raise RuntimeError("Неизвестен результат отправки. Проверьте кабинет fal; новый запрос не отправлен.")
        else:
            if target.exists():
                raise FileExistsError(f"Не перезаписываю существующий файл без метаданных: {target}")
            payload = dict(arguments)
            for key, value in files.items():
                payload[key] = [data_uri(p) for p in value] if isinstance(value, list) else data_uri(value)
            state = {"fingerprint": fingerprint, "inputs": identity, "status": "submitting"}
            # A crash after POST but before storing its ID must not silently incur a second charge.
            write_json(state_path, state)
            response = self.transport.request("POST", "https://queue.fal.run/" + endpoint, payload)
            for key in ("request_id", "status_url", "response_url"):
                if not isinstance(response.get(key), str) or not response[key]:
                    raise RuntimeError(f"В ответе fal отсутствует {key}. Проверьте кабинет провайдера.")
                state[key] = response[key]
            validate_url(state["status_url"], queue=True)
            validate_url(state["response_url"], queue=True)
            state["status"] = "submitted"
            write_json(state_path, state)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            status = self.transport.request("GET", state["status_url"])
            state["status"] = status.get("status", "UNKNOWN")
            write_json(state_path, state)
            if status.get("error"):
                raise RuntimeError("Генерация завершилась ошибкой. Проверьте request_id в кабинете fal.")
            if state["status"] == "COMPLETED":
                result = self.transport.request("GET", state["response_url"])
                if result.get("error") or any(result.get("has_nsfw_concepts", [])):
                    raise RuntimeError("Генерация отклонена провайдером или его проверкой безопасности.")
                try:
                    url = result["video"]["url"] if endpoint == VIDEO_MODEL else result["images"][0]["url"]
                except (KeyError, IndexError, TypeError):
                    raise RuntimeError("В ответе модели нет ожидаемого файла.") from None
                self.transport.download(url, target)
                state["sha256"] = file_hash(target)
                write_json(state_path, state)
                return target
            if state["status"] not in ("IN_QUEUE", "IN_PROGRESS"):
                raise RuntimeError(f"Неожиданный статус fal: {state['status']}")
            time.sleep(self.poll)
        raise TimeoutError("Ожидание завершено. Повтор команды продолжит тот же request_id. "
                           "Облачная задача НЕ отменена и может оставаться платной.")
