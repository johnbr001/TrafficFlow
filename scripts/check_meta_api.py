"""Check the configured Meta chat-completions request without printing secrets."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aatc.agent.meta_provider import MetaProvider
from aatc.agent.traffic_agent import _load_agent_environment


def main():
    settings = json.loads((ROOT / "config" / "agent.json").read_text(encoding="utf-8"))
    _load_agent_environment(ROOT)
    # Muse Spark's private reasoning tokens count against max_tokens. Keep
    # enough output budget for the visible READY response.
    settings["max_tokens"] = 256
    settings["reasoning_effort"] = "low"
    provider = MetaProvider.from_environment(settings)
    if provider is None:
        print("Meta check failed: configure META_MODEL and MODEL_API_KEY (or META_API_KEY) in .env.")
        return 2

    try:
        response = provider.complete(
            [{"role": "user", "content": "Reply with exactly: READY"}], [])
    except Exception as exc:
        # Provider errors contain HTTP/network diagnostics but never the bearer token.
        print("Meta chat-completions check failed: {}: {}".format(type(exc).__name__, exc))
        return 1

    content = response.get("content")
    if isinstance(content, list):
        content = " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not isinstance(content, str) or not content.strip():
        print("Meta request reached the model, but returned empty visible content. "
              "Check the model output-token budget/reasoning settings.")
        return 1
    print("Meta chat-completions check succeeded. Model: {}. Response: {}".format(
        provider.model, content.strip()[:80]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
