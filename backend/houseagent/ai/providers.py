"""AI service providers behind one interface (spec 6.4 / 15.15).

Business code (investment assessment, listing summary) only uses ``AIProvider``. Each implementation owns its
authentication, request format and error mapping; SDKs, model names and endpoints never reach the rules layer.

Every provider is asked for a fixed JSON structure (a JSON Schema) and returns the parsed object plus usage; the
caller validates the content. HTTP redirects are never followed, so a key cannot be forwarded to another host.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

OFFICIAL_URLS = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com"}
PROVIDER_TYPES = ("openai", "anthropic", "compatible")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

# Suggested models shown in the settings screen (the user can type any model ID). Prices are the providers'
# published list prices per million tokens at the date given; the user can edit them.
RECOMMENDED_MODELS: dict[str, list[dict[str, Any]]] = {
    "anthropic": [
        {
            "id": "claude-opus-5",
            "pricing": {"input_per_mtok": 5.0, "output_per_mtok": 25.0, "currency": "USD", "updated_at": "2026-09"},
            "recommended": True,
        },
        {
            "id": "claude-haiku-4-5",
            "pricing": {"input_per_mtok": 1.0, "output_per_mtok": 5.0, "currency": "USD", "updated_at": "2026-09"},
        },
        {"id": "claude-sonnet-5", "pricing": {}},
    ],
    "openai": [{"id": "gpt-5", "pricing": {}, "recommended": True}, {"id": "gpt-5-mini", "pricing": {}}],
    "compatible": [],
}


class AIError(Exception):
    """Stable error code (``error.<code>`` message key) - never carries the key or provider response body."""

    def __init__(self, code: str, detail: str | None = None, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.retryable = retryable


@dataclass
class ProviderSettings:
    provider_type: str
    model_id: str
    api_key: str
    base_url: str | None = None
    timeout_s: float = 60.0
    allowed_hosts: list[str] = field(default_factory=list)


@dataclass
class AIResult:
    data: dict[str, Any]
    input_units: int | None = None
    output_units: int | None = None
    model_version: str | None = None
    duration_ms: int = 0


@dataclass
class AIRequest:
    system: str
    user: str
    schema: dict[str, Any]
    schema_name: str = "result"
    max_tokens: int = 8000


class AIProvider(Protocol):
    def test_connection(self) -> dict[str, Any]: ...

    def assess_property(self, request: AIRequest) -> AIResult: ...

    def estimate_usage(self, request: AIRequest) -> dict[str, Any]: ...

    def generate_json(self, request: AIRequest) -> AIResult: ...


# ---- endpoint checks ---------------------------------------------------------------------------------------


def check_base_url(url: str | None, provider_type: str, allowed_hosts: list[str] | None = None) -> str:
    """Validate a custom endpoint (spec 4.7.1): HTTPS only, except an explicitly local model on this computer."""
    if not url:
        if provider_type == "compatible":
            raise AIError("ai_url_required")
        return OFFICIAL_URLS[provider_type]
    u = urlparse(url.strip())
    host = (u.hostname or "").lower()
    if not host or u.username or u.password or u.query or u.fragment:
        raise AIError("ai_url_invalid")
    if u.scheme == "http" and host not in LOCAL_HOSTS:
        raise AIError("ai_url_https_required")
    if u.scheme not in ("http", "https"):
        raise AIError("ai_url_invalid")
    allowed = [h.lower() for h in (allowed_hosts or []) if h]
    if allowed and host not in allowed and host not in LOCAL_HOSTS:
        raise AIError("ai_url_not_allowed")
    return url.strip().rstrip("/")


def check_resolved_host(url: str, allowed_hosts: list[str] | None = None) -> None:
    """Right before sending a key: a public host name must not resolve to this machine or a private network
    (DNS rebinding); local model hosts and explicitly allowed gateway hosts are fine."""
    host = (urlparse(url).hostname or "").lower()
    if host in LOCAL_HOSTS or host in [h.lower() for h in allowed_hosts or []]:
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise AIError("ai_network", "dns", retryable=True) from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
            raise AIError("ai_url_not_allowed", "resolves to a private address")


def _approx_tokens(text: str) -> int:
    # Conservative: Japanese / JSON text is roughly 2-3 characters per token.
    return max(1, int(len(text) / 2.2))


def estimate(request: AIRequest) -> dict[str, Any]:
    return {
        "input_units": _approx_tokens(request.system + request.user + json.dumps(request.schema)),
        "output_units": min(request.max_tokens, 1500),
    }


def _loads(text: str | None) -> dict[str, Any]:
    if not text:
        raise AIError("ai_bad_response", "empty")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIError("ai_bad_response", "not json") from exc
    if not isinstance(data, dict):
        raise AIError("ai_bad_response", "not an object")
    return data


TEST_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def _test(provider: Any) -> dict[str, Any]:
    started = time.monotonic()
    r = provider.generate_json(
        AIRequest(
            system="Connection test. Reply with the JSON object requested.",
            user='Return {"ok": true}.',
            schema=TEST_SCHEMA,
            schema_name="connection_test",
            max_tokens=2000,
        )
    )
    if r.data.get("ok") is not True:
        raise AIError("ai_bad_response", "structured output check failed")
    return {
        "ok": True,
        "model_version": r.model_version,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "checks": {"address": "ok", "auth": "ok", "model": "ok", "structured_output": "ok"},
        "input_units": r.input_units,
        "output_units": r.output_units,
    }


# ---- Anthropic ---------------------------------------------------------------------------------------------


class AnthropicProvider:
    def __init__(self, s: ProviderSettings) -> None:
        import anthropic

        self.s = s
        self.base_url = check_base_url(s.base_url, "anthropic", s.allowed_hosts)
        self._client = anthropic.Anthropic(
            api_key=s.api_key,
            base_url=self.base_url,
            timeout=s.timeout_s,
            max_retries=0,  # the HouseAgent gateway owns retries and limits
            http_client=anthropic.DefaultHttpxClient(follow_redirects=False),
        )

    def generate_json(self, request: AIRequest) -> AIResult:
        import anthropic

        check_resolved_host(self.base_url, self.s.allowed_hosts)
        started = time.monotonic()
        try:
            response = self._client.messages.create(
                model=self.s.model_id,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=[{"role": "user", "content": request.user}],
                output_config={"format": {"type": "json_schema", "schema": request.schema}},
            )
        except anthropic.AuthenticationError as exc:
            raise AIError("ai_auth") from exc
        except anthropic.PermissionDeniedError as exc:
            raise AIError("ai_permission") from exc
        except anthropic.NotFoundError as exc:
            raise AIError("ai_model_not_found") from exc
        except anthropic.RateLimitError as exc:
            raise AIError("ai_rate_limited", retryable=True) from exc
        except anthropic.BadRequestError as exc:
            raise AIError("ai_invalid_request") from exc
        except anthropic.APITimeoutError as exc:
            raise AIError("ai_timeout", retryable=True) from exc
        except anthropic.APIConnectionError as exc:
            raise AIError("ai_network", retryable=True) from exc
        except anthropic.APIStatusError as exc:
            if 300 <= exc.status_code < 400:
                raise AIError("ai_redirect_blocked") from exc
            raise AIError("ai_server_error", str(exc.status_code), retryable=exc.status_code >= 500) from exc
        if response.stop_reason == "refusal":
            raise AIError("ai_refused")
        if response.stop_reason == "max_tokens":
            raise AIError("ai_bad_response", "truncated")
        text = next((b.text for b in response.content if b.type == "text"), None)
        return AIResult(
            data=_loads(text),
            input_units=response.usage.input_tokens,
            output_units=response.usage.output_tokens,
            model_version=response.model,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def assess_property(self, request: AIRequest) -> AIResult:
        return self.generate_json(request)

    def estimate_usage(self, request: AIRequest) -> dict[str, Any]:
        return estimate(request)

    def test_connection(self) -> dict[str, Any]:
        return _test(self)


# ---- OpenAI and OpenAI-compatible endpoints ----------------------------------------------------------------


class OpenAIProvider:
    provider_type = "openai"

    def __init__(self, s: ProviderSettings) -> None:
        import openai

        self.s = s
        self.base_url = check_base_url(s.base_url, self.provider_type, s.allowed_hosts)
        self._client = openai.OpenAI(
            api_key=s.api_key or "not-needed",  # local compatible servers often need no key
            base_url=self.base_url,
            timeout=s.timeout_s,
            max_retries=0,
            http_client=openai.DefaultHttpxClient(follow_redirects=False),
        )

    def _create(self, request: AIRequest, response_format: dict[str, Any]) -> Any:
        return self._client.chat.completions.create(  # type: ignore[call-overload]
            model=self.s.model_id,
            messages=[{"role": "system", "content": request.system}, {"role": "user", "content": request.user}],
            response_format=response_format,
            max_completion_tokens=request.max_tokens,
        )

    def generate_json(self, request: AIRequest) -> AIResult:
        import openai

        check_resolved_host(self.base_url, self.s.allowed_hosts)
        started = time.monotonic()
        fmt = {
            "type": "json_schema",
            "json_schema": {"name": request.schema_name, "schema": request.schema, "strict": True},
        }
        try:
            try:
                response = self._create(request, fmt)
            except openai.BadRequestError:
                if self.provider_type != "compatible":
                    raise
                # Some compatible servers only know JSON mode: ask for JSON and state the schema in the prompt.
                request = AIRequest(
                    system=request.system
                    + "\n\nReturn only JSON matching this JSON Schema:\n"
                    + json.dumps(request.schema, ensure_ascii=False),
                    user=request.user,
                    schema=request.schema,
                    schema_name=request.schema_name,
                    max_tokens=request.max_tokens,
                )
                response = self._create(request, {"type": "json_object"})
        except openai.AuthenticationError as exc:
            raise AIError("ai_auth") from exc
        except openai.PermissionDeniedError as exc:
            raise AIError("ai_permission") from exc
        except openai.NotFoundError as exc:
            raise AIError("ai_model_not_found") from exc
        except openai.RateLimitError as exc:
            raise AIError("ai_rate_limited", retryable=True) from exc
        except openai.BadRequestError as exc:
            raise AIError("ai_invalid_request") from exc
        except openai.APITimeoutError as exc:
            raise AIError("ai_timeout", retryable=True) from exc
        except openai.APIConnectionError as exc:
            raise AIError("ai_network", retryable=True) from exc
        except openai.APIStatusError as exc:
            if 300 <= exc.status_code < 400:
                raise AIError("ai_redirect_blocked") from exc
            raise AIError("ai_server_error", str(exc.status_code), retryable=exc.status_code >= 500) from exc
        if not response.choices:
            raise AIError("ai_bad_response", "no choices")
        choice = response.choices[0]
        if choice.finish_reason == "content_filter" or getattr(choice.message, "refusal", None):
            raise AIError("ai_refused")
        if choice.finish_reason == "length":
            raise AIError("ai_bad_response", "truncated")
        usage = response.usage
        return AIResult(
            data=_loads(choice.message.content),
            input_units=usage.prompt_tokens if usage else None,
            output_units=usage.completion_tokens if usage else None,
            model_version=response.model,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def assess_property(self, request: AIRequest) -> AIResult:
        return self.generate_json(request)

    def estimate_usage(self, request: AIRequest) -> dict[str, Any]:
        return estimate(request)

    def test_connection(self) -> dict[str, Any]:
        return _test(self)


class CompatibleAPIProvider(OpenAIProvider):
    """Any endpoint speaking the OpenAI Chat Completions format: company gateways, hosted or local models."""

    provider_type = "compatible"


def make_provider(s: ProviderSettings) -> AIProvider:
    if s.provider_type == "anthropic":
        return AnthropicProvider(s)
    if s.provider_type == "openai":
        return OpenAIProvider(s)
    if s.provider_type == "compatible":
        return CompatibleAPIProvider(s)
    raise AIError("ai_provider_unknown")


def estimate_cost(pricing: dict[str, Any], input_units: int | None, output_units: int | None) -> float | None:
    """Estimated cost from the user-visible price table; None when prices are not set."""
    try:
        pin, pout = float(pricing["input_per_mtok"]), float(pricing["output_per_mtok"])
    except (KeyError, TypeError, ValueError):
        return None
    return round(((input_units or 0) * pin + (output_units or 0) * pout) / 1_000_000, 6)
