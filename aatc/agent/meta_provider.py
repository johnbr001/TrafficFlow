"""Meta Model API adapter using its documented Chat Completions tool format."""
import json
import os
import socket
import urllib.error
import urllib.request

from .llm_provider import LLMProvider, ProviderError


class MetaProvider(LLMProvider):
    name = "meta"

    def __init__(self, api_key, model, base_url="https://api.meta.ai/v1", timeout=45,
                 temperature=0.2, max_tokens=800, opener=None, reasoning_effort="low"):
        if not api_key:
            raise ValueError("Meta Model API key is required")
        if not model:
            raise ValueError("META_MODEL must name a model available to your Meta API account")
        self._api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self.temperature = float(temperature)
        self.max_tokens = int(max_tokens)
        self.reasoning_effort = reasoning_effort
        self._open = opener or urllib.request.urlopen

    @classmethod
    def from_environment(cls, settings):
        key_name = settings.get("api_key_env", "MODEL_API_KEY")
        model_name = settings.get("model_env", "META_MODEL")
        api_key = os.environ.get(key_name) or os.environ.get("META_API_KEY")
        model = os.environ.get(model_name)
        if not api_key or not model:
            return None
        return cls(api_key, model, settings.get("base_url", "https://api.meta.ai/v1"),
                   settings.get("timeout_seconds", 45), settings.get("temperature", 0.2),
                   settings.get("max_tokens", 800),
                   reasoning_effort=settings.get("reasoning_effort", "low"))

    def complete(self, messages, tools):
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        # Meta's documented text-only request needs just model and messages. Include
        # tool options only for actual tool-calling requests.
        if tools:
            payload.update({"tools": tools, "tool_choice": "auto",
                            "parallel_tool_calls": False})
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": "Bearer " + self._api_key,
                     "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._open(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ProviderError("Meta Model API returned HTTP {}".format(exc.code)) from None
        except urllib.error.URLError as exc:
            reason = exc.reason
            detail = "{}: {}".format(type(reason).__name__, reason)
            raise ProviderError("Meta Model API network request failed ({})".format(detail[:240])) from None
        except (socket.timeout, TimeoutError, OSError) as exc:
            raise ProviderError("Meta Model API connection failed ({}: {})".format(
                type(exc).__name__, str(exc)[:200])) from None
        except (ValueError, UnicodeError) as exc:
            raise ProviderError("Meta Model API returned invalid JSON ({})".format(type(exc).__name__)) from None
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError("Meta Model API response did not contain a chat message") from None
        if not isinstance(message, dict):
            raise ProviderError("Meta Model API returned an invalid chat message")
        return message
