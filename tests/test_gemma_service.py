import pytest
import requests

from config.settings import GemmaSettings
from src.services import gemma_service
from src.services.gemma_service import GemmaService
from src.utils.errors import GemmaError

CFG = GemmaSettings(provider="ollama", model="gemma4:e4b", ollama_host="http://ollama.test", api_key=None,
                    timeout_s=5, num_ctx=2048)


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code, self._payload, self.text = status_code, payload, text

    def json(self):
        return self._payload


@pytest.fixture
def fake_ollama(monkeypatch):
    monkeypatch.setattr(gemma_service.time, "sleep", lambda s: None)

    def install(*responses):
        queue = list(responses)
        calls = []

        def post(url, json, timeout):
            calls.append(url)
            r = queue.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        monkeypatch.setattr(gemma_service.requests, "post", post)
        return calls
    return install


def test_retries_transient_runner_stop(fake_ollama):
    calls = fake_ollama(FakeResponse(500, text='{"error":"model runner has unexpectedly stopped"}'),
                        FakeResponse(200, {"message": {"content": "READY"}}))
    assert GemmaService(CFG)._chat([{"role": "user", "content": "hi"}], None, 0.0) == "READY"
    assert len(calls) == 2


def test_retries_connection_error(fake_ollama):
    calls = fake_ollama(requests.ConnectionError("refused"), FakeResponse(200, {"message": {"content": "ok"}}))
    assert GemmaService(CFG)._chat([{"role": "user", "content": "hi"}], None, 0.0) == "ok"
    assert len(calls) == 2


def test_gives_up_after_repeated_500(fake_ollama):
    calls = fake_ollama(*[FakeResponse(500, text="boom")] * 3)
    with pytest.raises(GemmaError, match="Gemma inference failed"):
        GemmaService(CFG)._chat([{"role": "user", "content": "hi"}], None, 0.0)
    assert len(calls) == 3


def test_missing_model_is_not_retried(fake_ollama):
    calls = fake_ollama(FakeResponse(404, text="model not found"))
    with pytest.raises(GemmaError, match="ollama pull gemma4:e4b"):
        GemmaService(CFG)._chat([{"role": "user", "content": "hi"}], None, 0.0)
    assert len(calls) == 1
