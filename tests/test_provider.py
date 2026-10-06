import json
from pathlib import Path

import pytest

from localmodel.provider import TEXT_MODEL, VIDEO_MODEL, FalProvider, Transport, data_uri, validate_url


class FakeTransport:
    """Only a test double. No production fallback returns fake generated media."""
    def __init__(self):
        self.posts = 0
        self.downloads = 0
        self.pending = False
        self.fail_post = False
        self.fail_download = False
        self.model_error = False
        self.payload = None

    def request(self, method, url, payload=None):
        if method == "POST":
            self.posts += 1
            self.payload = payload
            if self.fail_post:
                raise TimeoutError("Network timeout after POST")
            return {"request_id": "test-request", "status_url": "https://queue.fal.run/status",
                    "response_url": "https://queue.fal.run/result"}
        if url.endswith("status"):
            if self.model_error:
                return {"status": "COMPLETED", "error": "generation failed"}
            return {"status": "IN_PROGRESS" if self.pending else "COMPLETED"}
        return {"images": [{"url": "https://fal.media/result.png"}],
                "video": {"url": "https://fal.media/result.mp4"}}

    def download(self, url, target: Path):
        self.downloads += 1
        if self.fail_download:
            raise TimeoutError("Download interrupted")
        target.write_bytes(b"test fixture media, not a generated character")


def test_gate_is_required_even_with_test_transport():
    with pytest.raises(PermissionError):
        FalProvider(False, FakeTransport())


def test_key_missing(monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    with pytest.raises(ValueError):
        FalProvider(True)


def test_completed_request_cached(tmp_path):
    transport = FakeTransport()
    provider = FalProvider(True, transport)
    target = tmp_path / "image.png"
    provider.produce(TEXT_MODEL, {"prompt": "A fictional adult"}, {}, target)
    provider.produce(TEXT_MODEL, {"prompt": "A fictional adult"}, {}, target)
    assert transport.posts == transport.downloads == 1
    assert json.loads((tmp_path / "image.png.job.json").read_text())["request_id"] == "test-request"


def test_changed_prompt_cannot_silently_charge_again(tmp_path):
    transport = FakeTransport()
    provider = FalProvider(True, transport)
    target = tmp_path / "image.png"
    provider.produce(TEXT_MODEL, {"prompt": "one"}, {}, target)
    with pytest.raises(ValueError):
        provider.produce(TEXT_MODEL, {"prompt": "two"}, {}, target)
    assert transport.posts == 1


def test_resume_without_post(tmp_path):
    transport = FakeTransport()
    transport.pending = True
    provider = FalProvider(True, transport, timeout=0.01, poll=0.001)
    target = tmp_path / "image.png"
    with pytest.raises(TimeoutError):
        provider.produce(TEXT_MODEL, {}, {}, target)
    transport.pending = False
    provider.produce(TEXT_MODEL, {}, {}, target)
    assert transport.posts == 1


def test_ambiguous_submission_is_not_retried(tmp_path):
    transport = FakeTransport()
    transport.fail_post = True
    provider = FalProvider(True, transport)
    target = tmp_path / "image.png"
    with pytest.raises(TimeoutError):
        provider.produce(TEXT_MODEL, {}, {}, target)
    transport.fail_post = False
    with pytest.raises(RuntimeError, match="Неизвестен"):
        provider.produce(TEXT_MODEL, {}, {}, target)
    assert transport.posts == 1


def test_interrupted_download_does_not_generate_again(tmp_path):
    transport = FakeTransport()
    transport.fail_download = True
    provider = FalProvider(True, transport)
    target = tmp_path / "clip.mp4"
    with pytest.raises(TimeoutError):
        provider.produce(VIDEO_MODEL, {}, {}, target)
    transport.fail_download = False
    provider.produce(VIDEO_MODEL, {}, {}, target)
    assert transport.posts == 1


def test_model_failure_does_not_download(tmp_path):
    transport = FakeTransport()
    transport.model_error = True
    with pytest.raises(RuntimeError):
        FalProvider(True, transport).produce(TEXT_MODEL, {}, {}, tmp_path / "image.png")
    assert transport.downloads == 0


def test_do_not_overwrite_untracked_media(tmp_path):
    target = tmp_path / "image.png"
    target.write_bytes(b"owned reference")
    with pytest.raises(FileExistsError):
        FalProvider(True, FakeTransport()).produce(TEXT_MODEL, {}, {}, target)
    assert target.read_bytes() == b"owned reference"


def test_missing_input_fails_before_post(tmp_path):
    transport = FakeTransport()
    with pytest.raises(ValueError):
        FalProvider(True, transport).produce(VIDEO_MODEL, {},
            {"video_url": tmp_path / "missing.mp4"}, tmp_path / "result.mp4")
    assert transport.posts == 0


def test_input_encoded_but_not_saved_in_state(tmp_path):
    source = tmp_path / "source.png"
    source.write_bytes(b"private reference")
    transport = FakeTransport()
    target = tmp_path / "result.png"
    FalProvider(True, transport).produce(TEXT_MODEL, {}, {"image_urls": [source]}, target)
    assert transport.payload["image_urls"][0].startswith("data:image/png;base64,")
    state = (tmp_path / "result.png.job.json").read_text()
    assert "base64" not in state
    assert "private reference" not in state


@pytest.mark.parametrize("url", ["http://queue.fal.run/status", "https://evil.example/status",
                               "https://queue.fal.run.evil.example/status", "https://a:b@queue.fal.run/status"])
def test_auth_host_allowlist(url):
    with pytest.raises(ValueError):
        validate_url(url, queue=True)


def test_transport_validates_before_network():
    with pytest.raises(ValueError):
        Transport("never-send-this").request("GET", "https://example.com")


def test_empty_input_is_rejected(tmp_path):
    path = tmp_path / "empty.png"
    path.touch()
    with pytest.raises(ValueError):
        data_uri(path)
