"""Tests for researchos.semantic.backend (S1). Almost every case
monkeypatches urllib.request.urlopen (and the direct opener for this Mac's
address) with a fixture/queue; the few that need a real socket talk to a
server this process starts on a free loopback port -- so the suite stays
closed and reproducible in CI with no LM Studio (or any other server)
running."""

import http.server
import io
import json
import logging
import socket
import struct
import threading
import urllib.error
import urllib.request

import pytest

from researchos.semantic import backend

_SCHEMA = {
    "type": "object",
    "required": ["decision", "confidence"],
    "properties": {
        "decision": {"type": "string", "enum": ["confirm", "reject", "uncertain"]},
        "confidence": {"type": "number"},
    },
}


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info) -> None:
        return None


def _chat_payload(content: dict | str) -> dict:
    text = content if isinstance(content, str) else json.dumps(content)
    return {"choices": [{"message": {"content": text}}]}


def _queue_urlopen(monkeypatch, items: list, requests_log: list | None = None):
    """items: a list of dict payloads or Exception instances, popped in
    order as urlopen is called; every request object is appended to
    requests_log (if given) before being resolved."""
    queue = list(items)

    def _fake(request, timeout=None):
        if requests_log is not None:
            requests_log.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)

    monkeypatch.setattr(backend.urllib.request, "urlopen", _fake)
    monkeypatch.setattr(backend._DIRECT, "open", _fake)  # this Mac's address: no proxy (16.2)


def test_env_vars_override_all_defaults(monkeypatch):
    monkeypatch.setenv("RCE_LLM_BASE_URL", "http://example.local:9999/v1/")
    monkeypatch.setenv("RCE_LLM_MODEL", "some-other-model")
    monkeypatch.setenv("RCE_LLM_TIMEOUT", "12.5")
    monkeypatch.setenv("RCE_LLM_API_KEY", "env-key")

    client = backend.LlmBackend()

    assert client.base_url == "http://example.local:9999/v1"  # trailing slash stripped
    assert client.model == "some-other-model"
    assert client.timeout == 12.5
    assert client.api_key == "env-key"


@pytest.mark.parametrize("url, expected", [
    ("http://127.0.0.1:1234", "http://127.0.0.1:1234/v1"),
    ("http://localhost:1234/", "http://localhost:1234/v1"),
    ("http://[::1]:1234", "http://[::1]:1234/v1"),
    ("https://models.example.com", "https://models.example.com/v1"),
    ("http://localhost:1234/v1/", "http://localhost:1234/v1"),
    ("https://models.example.com/gateway/openai/v1/", "https://models.example.com/gateway/openai/v1"),
    ("https://models.example.com/custom", "https://models.example.com/custom"),
    ("https://models.example.com/?key=value", "https://models.example.com/?key=value"),
])
def test_server_roots_use_v1_and_explicit_api_paths_are_preserved(monkeypatch, url, expected):
    requests = []
    _queue_urlopen(monkeypatch, [{"data": [{"id": "loaded-model"}]},
                               _chat_payload({"decision": "confirm", "confidence": 0.9})], requests)
    client = backend.LlmBackend(base_url=url, warn_remote=False)
    assert client.probe() == ["loaded-model"]
    assert client.complete_json("sys", "user", _SCHEMA)["decision"] == "confirm"
    assert [request.full_url for request in requests] == [expected + "/models", expected + "/chat/completions"]


@pytest.mark.parametrize("content", ["", "   ", None])
def test_lm_studio_structured_json_in_reasoning_content_is_validated(monkeypatch, content):
    expected = {"decision": "confirm", "confidence": 0.9}
    requests = []
    reply = {"model": "prism-ml/bonsai-27b", "choices": [{"message": {
        "content": content, "reasoning_content": json.dumps(expected)}, "finish_reason": "stop"}]}
    _queue_urlopen(monkeypatch, [reply], requests)
    client = backend.LlmBackend()
    assert client.complete_json("sys", "user", _SCHEMA) == expected
    assert client.last_model == "prism-ml/bonsai-27b" and len(requests) == 1


@pytest.mark.parametrize("alternate", [
    'Thinking about the answer: {"decision":"confirm","confidence":0.9}',
    '{"decision":"confirm"}',
    '{"decision":"unknown","confidence":0.9}',
    '{"decision":"confirm","confidence":"0.9"}',
    None,
])
def test_reasoning_prose_and_invalid_structured_payloads_are_never_accepted(monkeypatch, alternate):
    reply = {"choices": [{"message": {"content": "", "reasoning_content": alternate}, "finish_reason": "stop"}]}
    _queue_urlopen(monkeypatch, [reply, reply])
    with pytest.raises(backend.LlmError, match="failed schema validation twice"):
        backend.LlmBackend().complete_json("sys", "user", _SCHEMA)


def test_nonempty_answer_takes_precedence_over_reasoning_content(monkeypatch):
    reply = {"choices": [{"message": {
        "content": json.dumps({"decision": "reject", "confidence": 0.8}),
        "reasoning_content": json.dumps({"decision": "confirm", "confidence": 0.9}),
    }, "finish_reason": "stop"}]}
    _queue_urlopen(monkeypatch, [reply])
    assert backend.LlmBackend().complete_json("sys", "user", _SCHEMA)["decision"] == "reject"


def test_truncated_reasoning_json_is_not_accepted(monkeypatch):
    reply = {"choices": [{"message": {"content": "", "reasoning_content":
        json.dumps({"decision": "confirm", "confidence": 0.9})}, "finish_reason": "length"}]}
    _queue_urlopen(monkeypatch, [reply])
    with pytest.raises(backend.LlmContextError):
        backend.LlmBackend().complete_json("sys", "user", _SCHEMA)


def test_complete_json_uses_constrained_output_when_supported(monkeypatch):
    requests_log: list = []
    _queue_urlopen(monkeypatch, [_chat_payload({"decision": "confirm", "confidence": 0.9})], requests_log)
    client = backend.LlmBackend(api_key=None)

    result = client.complete_json("sys", "user", _SCHEMA)

    assert result == {"decision": "confirm", "confidence": 0.9}
    sent_body = json.loads(requests_log[0].data)
    assert sent_body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "response", "strict": True, "schema": _SCHEMA},
    }


def test_complete_json_falls_back_when_response_format_unsupported(monkeypatch):
    requests_log: list = []
    unsupported = urllib.error.HTTPError("http://x/v1/chat/completions", 400, "Bad Request", hdrs=None, fp=None)
    _queue_urlopen(
        monkeypatch,
        [unsupported, _chat_payload({"decision": "reject", "confidence": 0.2})],
        requests_log,
    )
    client = backend.LlmBackend()

    result = client.complete_json("sys", "user", _SCHEMA)

    assert result == {"decision": "reject", "confidence": 0.2}
    assert len(requests_log) == 2
    second_body = json.loads(requests_log[1].data)
    assert "response_format" not in second_body
    assert "JSON Schema" in second_body["messages"][0]["content"]  # schema hint appended to system prompt


def test_complete_json_retries_once_after_validation_failure_then_succeeds(monkeypatch):
    _queue_urlopen(
        monkeypatch,
        [
            _chat_payload({"decision": "not-a-real-choice", "confidence": 0.5}),  # fails enum
            _chat_payload({"decision": "confirm", "confidence": 0.5}),
        ],
    )
    client = backend.LlmBackend()

    result = client.complete_json("sys", "user", _SCHEMA)

    assert result == {"decision": "confirm", "confidence": 0.5}


def test_complete_json_raises_after_second_validation_failure(monkeypatch):
    _queue_urlopen(
        monkeypatch,
        [
            _chat_payload({"decision": "confirm"}),  # missing required "confidence" both times
            _chat_payload({"decision": "confirm"}),
        ],
    )
    client = backend.LlmBackend()

    with pytest.raises(backend.LlmError, match="failed schema validation twice"):
        client.complete_json("sys", "user", _SCHEMA)


def test_probe_returns_model_ids(monkeypatch):
    _queue_urlopen(monkeypatch, [{"data": [{"id": "model-a"}, {"id": "model-b"}]}])
    client = backend.LlmBackend()

    assert client.probe() == ["model-a", "model-b"]


def test_probe_server_not_started_gives_actionable_error(monkeypatch):
    _queue_urlopen(monkeypatch, [urllib.error.URLError(ConnectionRefusedError("Connection refused"))])
    client = backend.LlmBackend()

    with pytest.raises(backend.LlmError, match="LM Studio") as exc_info:
        client.probe()
    assert "RCE_LLM_BASE_URL" in str(exc_info.value)


def test_request_timeout_raises_clear_llm_error(monkeypatch):
    _queue_urlopen(monkeypatch, [TimeoutError("timed out")])
    client = backend.LlmBackend()

    with pytest.raises(backend.LlmError, match="timed out"):
        client.probe()


@pytest.mark.parametrize(
    "api_key, expect_header",
    [("secret-123", "Bearer secret-123"), (None, None)],
)
def test_authorization_header_reflects_api_key(monkeypatch, api_key, expect_header):
    requests_log: list = []
    _queue_urlopen(monkeypatch, [{"data": []}], requests_log)
    client = backend.LlmBackend(api_key=api_key)

    client.probe()

    assert requests_log[0].get_header("Authorization") == expect_header


# -- Privacy: DESIGN.md section 7 says "local by default", not "local by
# construction" -- RCE_LLM_BASE_URL is user-configurable, so a non-local
# value must warn loudly and every time, never silently ship data offline. --


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:1234/v1",
        "http://127.0.0.1:1234/v1",
        "http://[::1]:1234/v1",
        "http://my-desktop.local:1234/v1",
        backend.DEFAULT_BASE_URL,
    ],
)
def test_local_base_urls_do_not_warn(base_url, caplog):
    with caplog.at_level(logging.WARNING):
        backend.LlmBackend(base_url=base_url)

    assert caplog.text == ""


def test_remote_base_url_warns_every_construction(caplog):
    with caplog.at_level(logging.WARNING):
        backend.LlmBackend(base_url="http://example.com:8000/v1")
        backend.LlmBackend(base_url="http://example.com:8000/v1")  # second construction, second warning

    assert caplog.text.count("NON-LOCAL") == 2
    assert "example.com" in caplog.text


def test_remote_base_url_from_env_var_also_warns(monkeypatch, caplog):
    monkeypatch.setenv("RCE_LLM_BASE_URL", "https://api.remote-inference.example/v1")

    with caplog.at_level(logging.WARNING):
        backend.LlmBackend()

    assert "NON-LOCAL" in caplog.text
    assert "api.remote-inference.example" in caplog.text


# -- DESIGN.md 16: a context overflow is told apart from "response_format
# unsupported", and every HTTP error keeps the server's own words. --

# LM Studio's exact answer when the prompt is longer than the loaded context.
_LM_STUDIO_OVERFLOW = (
    b'{"error":"The number of tokens to keep from the initial prompt is greater than the context length. '
    b'Try to load the model with a larger context length, or provide a shorter input"}'
)


def _http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x/v1/chat/completions", code, "Bad Request", hdrs=None,
                                  fp=io.BytesIO(body))


def test_context_overflow_is_raised_before_the_response_format_fallback(monkeypatch):
    requests_log: list = []
    _queue_urlopen(monkeypatch, [_http_error(400, _LM_STUDIO_OVERFLOW)], requests_log)
    client = backend.LlmBackend()

    with pytest.raises(backend.LlmContextError) as exc_info:
        client.complete_json("sys", "user", _SCHEMA)
    assert len(requests_log) == 1  # no schema-in-prompt retry
    assert "greater than the context length" in str(exc_info.value)
    assert isinstance(exc_info.value, backend.LlmError)


def test_context_overflow_without_response_format_is_also_named(monkeypatch):
    _queue_urlopen(monkeypatch, [_http_error(400, b"maximum context length is 4096 tokens")])
    with pytest.raises(backend.LlmContextError):
        backend.LlmBackend().probe()


def test_other_http_errors_carry_the_body_excerpt(monkeypatch):
    _queue_urlopen(monkeypatch, [_http_error(500, b"model crashed: out of memory" + b"x" * 5000)])
    with pytest.raises(backend.LlmError) as exc_info:
        backend.LlmBackend().probe()
    assert not isinstance(exc_info.value, backend.LlmContextError)
    assert "HTTP 500" in str(exc_info.value) and "model crashed: out of memory" in str(exc_info.value)
    assert len(str(exc_info.value)) < 2_300  # at most 2,000 bytes of the body


def test_the_answering_model_is_remembered(monkeypatch):
    payload = {**_chat_payload({"decision": "confirm", "confidence": 0.9}), "model": "loaded-model"}
    _queue_urlopen(monkeypatch, [payload, _chat_payload({"decision": "confirm", "confidence": 0.9})])
    client = backend.LlmBackend(model="asked-model")
    assert client.last_model is None
    client.complete_json("sys", "user", _SCHEMA)
    assert client.last_model == "loaded-model"
    client.complete_json("sys", "user", _SCHEMA)  # no "model" in the answer: the one asked for
    assert client.last_model == "asked-model"


# -- DESIGN.md 16.2: a server that breaks off is an LlmError like any other failure; this Mac's
# address never goes through a proxy. Real sockets on free loopback ports, never LM Studio's. --


def _misbehaving_server(mode: str) -> str:
    """A one-connection server on a free loopback port that reads the
    request, then: closes ("close"), resets ("reset"), or promises 500
    bytes and sends a few ("short")."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def run() -> None:
        conn, _ = srv.accept()
        with conn:
            conn.recv(65536)
            if mode == "reset":
                conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            elif mode == "short":
                conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 500\r\n\r\n{"choi')
        srv.close()

    threading.Thread(target=run, daemon=True).start()
    return f"http://127.0.0.1:{srv.getsockname()[1]}/v1"


@pytest.mark.parametrize("mode", ["close", "reset", "short"])
def test_a_connection_the_server_breaks_off_is_an_llm_error(mode):
    with pytest.raises(backend.LlmError) as exc_info:
        backend.LlmBackend(base_url=_misbehaving_server(mode), timeout=5).complete_json("sys", "user", _SCHEMA)
    assert not isinstance(exc_info.value, backend.LlmContextError)
    assert "LM Studio" in str(exc_info.value)


def test_a_reply_cut_off_at_the_length_limit_is_a_context_error(monkeypatch):
    payload = {"choices": [{"message": {"content": '{"decision": "conf'}, "finish_reason": "length"}]}
    requests_log: list = []
    _queue_urlopen(monkeypatch, [payload], requests_log)
    with pytest.raises(backend.LlmContextError):
        backend.LlmBackend().complete_json("sys", "user", _SCHEMA)
    assert len(requests_log) == 1  # no repair retry


def _http_thread(handler_post) -> tuple[str, http.server.ThreadingHTTPServer]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            handler_post(self)

        do_GET = do_POST  # noqa: N815

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"127.0.0.1:{httpd.server_address[1]}", httpd


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_this_macs_address_never_goes_through_a_proxy(monkeypatch, host):
    seen_by_proxy: list = []

    def proxy(h):
        seen_by_proxy.append(h.path)
        h.send_response(502)
        h.end_headers()

    def model(h):
        h.rfile.read(int(h.headers.get("Content-Length") or 0))
        raw = json.dumps(_chat_payload({"decision": "confirm", "confidence": 0.9})).encode("utf-8")
        h.send_response(200)
        h.send_header("Content-Length", str(len(raw)))
        h.end_headers()
        h.wfile.write(raw)

    proxy_at, proxy_httpd = _http_thread(proxy)
    model_at, model_httpd = _http_thread(model)
    try:
        for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
            monkeypatch.setenv(name, f"http://{proxy_at}")
        for name in ("no_proxy", "NO_PROXY"):
            monkeypatch.delenv(name, raising=False)
        assert urllib.request.getproxies().get("http") == f"http://{proxy_at}"  # urlopen would use it
        port = model_at.rsplit(":", 1)[1]
        got = backend.LlmBackend(base_url=f"http://{host}:{port}/v1", timeout=5).complete_json("sys", "user", _SCHEMA)
        assert got == {"decision": "confirm", "confidence": 0.9}
        assert seen_by_proxy == []
    finally:
        for httpd in (proxy_httpd, model_httpd):
            httpd.shutdown()
            httpd.server_close()


@pytest.mark.parametrize("host, here", [("localhost", True), ("LOCALHOST", True), ("127.0.0.2", True), ("::1", True),
                                        ("studio.local", False), ("192.168.1.9", False), ("", False), (None, False)])
def test_this_mac_is_loopback_only(host, here):
    assert backend.on_this_mac(host) is here


def test_the_draft_can_leave_out_the_judges_warning(caplog):
    with caplog.at_level(logging.WARNING):
        backend.LlmBackend(base_url="http://example.com:8000/v1", warn_remote=False)
    assert caplog.text == ""
