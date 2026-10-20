"""Local, vendor-neutral OpenAI-compatible chat-completions client for the
optional semantic layer (DESIGN.md section 7) -- stdlib urllib only, no
`openai`/`requests` dependency (mirrors researchos.ingest.wandb's constraint).

Talks to any server that speaks the OpenAI `/v1/chat/completions` and
`/v1/models` surface: LM Studio (the Owner's default -- default_base_url
below is LM Studio's out-of-the-box address), llama.cpp server, vLLM, or
Ollama's compatible endpoint. Research OS is not bound to any one of them; all four
are reached through this same class.

`complete_json` prefers constrained decoding via `response_format`
(`{"type": "json_schema", ...}`); if the endpoint rejects that field (HTTP
400/422 -- older or minimal servers don't implement it), it falls back to a
plain prompt that states the schema in the system message -- unless the
error's body says the input overflows the model's context, which is
`LlmContextError` and never a fallback. Either way, the
model's JSON is validated against a minimal local schema checker (below --
no `jsonschema` dependency) before being returned; a validation failure gets
one repair retry, then a raised LlmError rather than a guess.

Constitution note: this module only ever returns data to its caller. It
never touches researchos.db and has no notion of edge status -- the semantic layer
built on top of it must route any accept/reject judgement through
db.set_edge_status (human-only path), never db.upsert_edge with a
`confirmed`/`rejected` status.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from researchos.settings import normalize_llm_base_url

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:1234/v1"
DEFAULT_MODEL = "mlx-community/Qwen3.5-9B-MLX-4bit"
# Local inference (esp. CPU/MLX on a laptop) is slow to first token; 30s
# (researchos.ingest.wandb's network default) undercounts that badly. Configurable
# via RCE_LLM_TIMEOUT / the timeout= constructor arg if 60s is wrong for a
# given model/machine (see owner_decisions_needed).
DEFAULT_TIMEOUT_SECONDS = 60.0
_MAX_ATTEMPTS = 2  # one shot + one schema-repair retry, never more (never guess)

_SCHEMA_HINT = (
    "\n\nYou must reply with a single JSON object and nothing else -- no "
    "prose, no markdown code fence -- matching exactly this JSON Schema:\n{schema}"
)


class LlmError(RuntimeError):
    """The one exception a caller needs to catch: any network failure,
    timeout, non-200 response, unparseable JSON, or a model reply that still
    fails schema validation after one repair retry. Always carries an
    actionable, specific message -- never a bare stack trace."""


class LlmContextError(LlmError):
    """The server refused the request because the input is longer than the
    model's loaded context (LM Studio answers HTTP 400 with "...greater than
    the context length..."). Told apart from every other HTTP error by the
    server's own words, before the `response_format` fallback, so an
    overflow is never misread as "response_format unsupported"."""


class _UnsupportedResponseFormat(Exception):
    """Internal signal only (never escapes this module): the endpoint
    returned 400/422 for a request that included `response_format`."""


_ERROR_BODY_BYTES = 2_000
_CONTEXT_OVERFLOW = re.compile(
    r"context (length|size|window)|maximum context|too many tokens|exceeds the available context", re.IGNORECASE
)


def _error_body(exc: urllib.error.HTTPError) -> str:
    """The first `_ERROR_BODY_BYTES` of an HTTP error's body, as text; empty
    when there is none or it cannot be read."""
    try:
        raw = exc.read(_ERROR_BODY_BYTES) if exc.fp is not None else b""
    except (OSError, ValueError, AttributeError):
        raw = b""
    return raw.decode("utf-8", errors="ignore").strip()


def _check_type(value: Any, type_name: str) -> bool:
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    mapping = {"string": str, "boolean": bool, "object": dict, "array": list, "null": type(None)}
    expected = mapping.get(type_name)
    return expected is None or isinstance(value, expected)


def _validate(instance: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Minimal JSON-Schema checker: required fields present, types correct,
    enum values legal. No external `jsonschema` dependency (Occam rule 1)."""
    type_name = schema.get("type")
    if type_name and not _check_type(instance, type_name):
        return [f"{path}: expected type {type_name!r}, got {type(instance).__name__}"]
    errors: list[str] = []
    enum = schema.get("enum")
    if enum is not None and instance not in enum:
        errors.append(f"{path}: value {instance!r} not in enum {enum!r}")
    if type_name == "object" and isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}.{key}: required field missing")
        for key, subschema in (schema.get("properties") or {}).items():
            if key in instance:
                errors.extend(_validate(instance[key], subschema, f"{path}.{key}"))
    if type_name == "array" and isinstance(instance, list):
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(instance):
                errors.extend(_validate(item, item_schema, f"{path}[{i}]"))
    return errors


def _parse_and_validate(content: str, schema: dict[str, Any]) -> tuple[Any, list[str] | None]:
    try:
        instance = json.loads(content)
    except (TypeError, ValueError) as exc:
        return None, [f"response content is not valid JSON: {exc}"]
    errors = _validate(instance, schema)
    return instance, (errors or None)


# Hostnames/suffixes treated as "on this machine" for the privacy warning
# below. `localhost`/loopback IPv4/IPv6 cover every default the four
# backends this module targets (LM Studio, llama.cpp server, vLLM, Ollama)
# actually bind to; `*.local` covers mDNS names for another machine on the
# same LAN a user might point at intentionally (e.g. a beefier desktop) --
# still not "leaves the machine", but not the local host either, so it is
# exempted from the warning by name rather than by any deeper check this
# module has no way to perform (Occam rule 4: a hostname-shape check, not a
# network reachability probe).
_LOCAL_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})


def _is_local_host(hostname: str | None) -> bool:
    if hostname is None:
        return False
    hostname = hostname.lower()
    return hostname in _LOCAL_HOSTNAMES or hostname.endswith(".local")


def on_this_mac(hostname: str | None) -> bool:
    """`localhost`, 127.0.0.0/8 or ::1 only: a `*.local` name is another
    machine on the network (DESIGN.md 16.2), unlike `_is_local_host` (the
    judge's warning). The page's "this Mac" and the proxy bypass below
    both ask this one question."""
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


# A request to this Mac never goes through a proxy (DESIGN.md 16.2):
# urlopen follows http_proxy and the macOS system proxy, whose exceptions
# never match "localhost:1234", so a draft the page calls local could
# otherwise leave the Mac through one.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class LlmBackend:
    """One client per (base_url, model). Constructor args win over the
    RCE_LLM_* env vars, which win over the hard-coded defaults above.
    `warn_remote=False` leaves out the judge's warning below (the draft
    logs its own, DESIGN.md 16.2)."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        api_key: str | None = None,
        *,
        warn_remote: bool = True,
    ) -> None:
        self.base_url = normalize_llm_base_url(
            base_url if base_url is not None else os.environ.get("RCE_LLM_BASE_URL") or DEFAULT_BASE_URL)
        self.model = model if model is not None else (os.environ.get("RCE_LLM_MODEL") or DEFAULT_MODEL)
        self.timeout = float(timeout if timeout is not None else os.environ.get("RCE_LLM_TIMEOUT", DEFAULT_TIMEOUT_SECONDS))
        # Local servers usually need no key; a compatible endpoint that does
        # require one still gets it, from RCE_LLM_API_KEY, never hard-coded.
        self.api_key = api_key if api_key is not None else os.environ.get("RCE_LLM_API_KEY", "")
        # The model id the server named in its last chat answer (`_chat`).
        self.last_model: str | None = None

        # Privacy note (DESIGN.md section 7): the semantic layer is local by
        # *default*, not local by construction -- RCE_LLM_BASE_URL/base_url
        # can point anywhere an OpenAI-compatible server answers, including
        # a remote host, and every `researchos judge` run sends that run's full
        # params + metric names (see researchos.semantic.judge._gather_run_context)
        # to whatever `self.base_url` resolves to. A public, open-source
        # repo must not carry an unqualified "your results never leave the
        # machine" promise once that is user-configurable, so this warns on
        # every construction (not just once per process) rather than
        # silently trusting a non-default value.
        if warn_remote and not _is_local_host(urlparse(self.base_url).hostname):
            logger.warning(
                "RCE_LLM_BASE_URL/base_url is set to a NON-LOCAL endpoint (%s) -- every 'researchos "
                "judge' run will send this project's experiment params and metric names to that "
                "address over the network. The semantic layer is local by default, not local by "
                "construction: set RCE_LLM_BASE_URL/base_url to a localhost (or *.local) address "
                "if unpublished results must not leave this machine.",
                self.base_url,
            )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """One GET (`body=None`) or POST via urllib. Every failure mode
        becomes an LlmError with the endpoint and cause in the message,
        except the one case `complete_json` needs to distinguish (a 400/422
        on a `response_format` request), which is re-raised as the internal
        `_UnsupportedResponseFormat` signal instead."""
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        try:
            request = urllib.request.Request(
                url, data=data, headers=self._headers(), method="POST" if body is not None else "GET"
            )
            direct = on_this_mac(urlparse(self.base_url).hostname)
        except ValueError as exc:
            raise LlmError(f"the LLM server address {self.base_url} cannot be used: {exc}") from exc
        try:
            with (_DIRECT.open if direct else urllib.request.urlopen)(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = _error_body(exc)
            if _CONTEXT_OVERFLOW.search(detail):
                raise LlmContextError(
                    f"LLM server at {self.base_url} returned HTTP {exc.code} for {path}: the input is longer "
                    f"than the model's loaded context: {detail}"
                ) from exc
            if body is not None and body.get("response_format") is not None and exc.code in (400, 422):
                raise _UnsupportedResponseFormat() from exc
            raise LlmError(
                f"LLM server at {self.base_url} returned HTTP {exc.code} for {path}: {exc.reason}"
                + (f": {detail}" if detail else "")
            ) from exc
        except TimeoutError as exc:
            raise LlmError(f"LLM request to {self.base_url}{path} timed out after {self.timeout}s") from exc
        except urllib.error.URLError as exc:
            raise LlmError(
                f"could not reach an LLM server at {self.base_url} ({exc.reason}) -- start LM "
                "Studio's local server (Developer tab), start llama.cpp server / vLLM / Ollama's "
                "OpenAI-compatible endpoint, or set RCE_LLM_BASE_URL to point at the right one"
            ) from exc
        except (OSError, http.client.HTTPException) as exc:
            # After the clauses above (all OSErrors): urllib wraps only the
            # sending in URLError; a server that drops the connection or
            # breaks off its answer (RemoteDisconnected, a reset,
            # IncompleteRead) arrives here.
            raise LlmError(
                f"the LLM server at {self.base_url} broke off the connection for {path} ({exc!r}) -- the "
                "model may have been unloaded or the server stopped: check that LM Studio's local server "
                "is running and a model is loaded"
            ) from exc
        except (TypeError, ValueError) as exc:
            raise LlmError(f"LLM server at {self.base_url} returned unparseable JSON for {path}: {exc}") from exc

    def probe(self) -> list[str]:
        """GET /models -- a health check plus the list of model ids the
        server currently has loaded/available."""
        payload = self._request("/models")
        data = payload.get("data")
        if not isinstance(data, list):
            raise LlmError(f"unexpected /models response shape from {self.base_url}: {payload!r}")
        return [item["id"] for item in data if isinstance(item, dict) and item.get("id")]

    def _chat(self, messages: list[dict[str, str]], json_schema: dict[str, Any] | None, name: str) -> str:
        body: dict[str, Any] = {"model": self.model, "messages": messages}
        if json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": json_schema},
            }
        payload = self._request("/chat/completions", body)
        try:
            choice = payload["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError(f"unexpected /chat/completions response shape from {self.base_url}: {payload!r}") from exc
        if choice.get("finish_reason") == "length":  # a cut-off reply: no repair retry can fit either
            raise LlmContextError(
                f"LLM server at {self.base_url} cut the model's reply off at its length limit: the input and "
                "the reply do not fit the model's loaded context"
            )
        # The model the server says answered (llama.cpp server answers with
        # whatever model it was started with, whatever was asked for).
        answered = payload.get("model")
        self.last_model = answered if isinstance(answered, str) and answered else self.model
        # LM Studio can put constrained JSON in reasoning_content and leave
        # content empty (lmstudio-ai/lmstudio-bug-tracker#1698, #1971).
        # Accept only a completed, structured response whose ENTIRE alternate
        # field parses and validates. Never extract JSON from reasoning prose,
        # replace a nonempty answer, or use a truncated/tool-call response.
        if (json_schema is not None and choice.get("finish_reason") == "stop"
                and (content is None or isinstance(content, str) and not content.strip())):
            alternate = choice["message"].get("reasoning_content")
            if isinstance(alternate, str) and _parse_and_validate(alternate, json_schema)[1] is None:
                logger.info("LM Studio compatibility: using schema-valid structured JSON from reasoning_content (model %s)",
                            self.last_model)
                content = alternate
        return content

    def complete_json(self, system: str, user: str, json_schema: dict[str, Any], name: str = "response") -> dict[str, Any]:
        """POST /chat/completions and return a dict that passes `json_schema`.

        Tries constrained decoding first; falls back to a plain prompt (with
        the schema stated in the system message) the moment the endpoint
        signals it does not support `response_format`. A reply that fails
        local schema validation gets exactly one repair retry with the
        errors quoted back to the model; a second failure raises LlmError
        instead of returning an unvalidated guess.
        """
        messages: list[dict[str, str]] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        constrained = True
        errors: list[str] | None = None
        for _ in range(_MAX_ATTEMPTS):
            try:
                content = self._chat(messages, json_schema if constrained else None, name)
            except _UnsupportedResponseFormat:
                logger.warning(
                    "endpoint at %s rejected response_format; falling back to a schema-in-prompt request",
                    self.base_url,
                )
                constrained = False
                messages[0] = {"role": "system", "content": system + _SCHEMA_HINT.format(schema=json.dumps(json_schema))}
                content = self._chat(messages, None, name)
            instance, errors = _parse_and_validate(content, json_schema)
            if errors is None:
                return instance
            messages.append({"role": "assistant", "content": content})
            messages.append(
                {"role": "user", "content": f"That reply failed schema validation: {errors}. Reply again with corrected JSON only."}
            )
        raise LlmError(f"model output at {self.base_url} failed schema validation twice: {errors}")
