"""Ask the traffic assistant; defaults to an interactive local/API-backed chat."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aatc.agent.traffic_agent import TrafficAgent


def display(result, as_json=False):
    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print(result["answer"])
    if result.get("provider_fallback_reason"):
        print("Mode: local tools ({})".format(result["provider_fallback_reason"]))
    for step in result.get("tool_steps", []):
        tool_result = json.dumps(step.get("result"), ensure_ascii=False)
        if len(tool_result) > 500:
            tool_result = tool_result[:497] + "..."
        print("[tool:{}] {} -> {}".format(step.get("tool"),
              json.dumps(step.get("arguments"), ensure_ascii=False), tool_result))


def main():
    parser = argparse.ArgumentParser(description="AATSC traffic analysis agent")
    parser.add_argument("question", nargs="*", help="question; omit to open interactive mode")
    parser.add_argument("--json", action="store_true", dest="as_json", help="print structured result JSON")
    args = parser.parse_args()
    agent = TrafficAgent()
    if args.question:
        try:
            display(agent.ask(" ".join(args.question)), args.as_json)
        except (RuntimeError, ValueError) as exc:
            print("Agent request failed: {}".format(exc), file=sys.stderr)
            return 2
        return 0
    print("AATSC agent. Type 'exit' to quit.")
    while True:
        try:
            question = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if question.lower() in ("exit", "quit"):
            break
        if not question:
            continue
        try:
            display(agent.ask(question), args.as_json)
        except (RuntimeError, ValueError) as exc:
            print("Agent request failed: {}".format(exc), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
