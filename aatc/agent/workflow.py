"""LangGraph workflow for provider selection, tool execution, and fallback."""
import json
from typing import Any, Dict, List, TypedDict

from langgraph.graph import END, START, StateGraph

from .llm_provider import ProviderError
from .prompts import SYSTEM_PROMPT


class TrafficAgentState(TypedDict, total=False):
    """State carried between the traffic-agent workflow nodes."""

    question: str
    mode: str
    messages: List[Dict[str, Any]]
    assistant_message: Dict[str, Any]
    tool_calls: List[Dict[str, Any]]
    tool_steps: List[Dict[str, Any]]
    tool_rounds: int
    result: Dict[str, Any]
    provider_error: str


class TrafficAgentWorkflow:
    """Build a bounded graph that keeps model reasoning and Python tools distinct."""

    def __init__(self, provider, tools, local_handler, max_tool_rounds=4):
        self.provider = provider
        self.tools = tools
        self.local_handler = local_handler
        self.max_tool_rounds = max(0, int(max_tool_rounds))

        builder = StateGraph(TrafficAgentState)
        builder.add_node("route_mode", self._route_mode_node)
        builder.add_node("call_model", self._call_model_node)
        builder.add_node("execute_tools", self._execute_tools_node)
        builder.add_node("finish_model", self._finish_model_node)
        builder.add_node("tool_limit", self._tool_limit_node)
        builder.add_node("local_fallback", self._local_fallback_node)
        builder.add_edge(START, "route_mode")
        builder.add_conditional_edges("route_mode", self._select_mode,
                                      {"model": "call_model", "local": "local_fallback"})
        builder.add_conditional_edges("call_model", self._select_model_next,
                                      {"tools": "execute_tools", "done": "finish_model", "limit": "tool_limit"})
        builder.add_edge("execute_tools", "call_model")
        builder.add_edge("finish_model", END)
        builder.add_edge("tool_limit", END)
        builder.add_edge("local_fallback", END)
        self.graph = builder.compile()

    def invoke(self, question, mode="model", provider_error=None):
        messages = ([{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": question}]
                    if mode == "model" else [])
        initial_state = {"question": question, "mode": mode, "messages": messages,
                         "assistant_message": {}, "tool_calls": [], "tool_steps": [],
                         "tool_rounds": 0, "result": {},
                         "provider_error": provider_error or ""}
        state = self.graph.invoke(initial_state)
        return state.get("result", {})

    @staticmethod
    def _route_mode_node(_state):
        return {}

    @staticmethod
    def _select_mode(state):
        return "model" if state.get("mode") == "model" else "local"

    def _call_model_node(self, state):
        message = self.provider.complete(state["messages"], self.tools.provider_tools())
        if not isinstance(message, dict):
            raise ProviderError("Meta Model API returned an invalid chat message")
        calls = message.get("tool_calls") or []
        if not isinstance(calls, list):
            raise ProviderError("Meta Model API returned an invalid tool-call list")
        return {"assistant_message": message, "tool_calls": calls}

    def _select_model_next(self, state):
        if not state.get("tool_calls"):
            return "done"
        if state.get("tool_rounds", 0) >= self.max_tool_rounds:
            return "limit"
        return "tools"

    def _execute_tools_node(self, state):
        message = state.get("assistant_message", {})
        messages = list(state.get("messages", []))
        messages.append(message)
        steps = list(state.get("tool_steps", []))
        calls = state.get("tool_calls", [])
        for index, call in enumerate(calls):
            function = call.get("function", {}) if isinstance(call, dict) else {}
            name = function.get("name", "")
            raw_arguments = function.get("arguments", "{}")
            try:
                arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
            except (ValueError, TypeError):
                arguments = None
            result = self.tools.execute(name, arguments)
            call_id = call.get("id", "tool_call_{}".format(index)) if isinstance(call, dict) else "tool_call_{}".format(index)
            steps.append({"tool": name, "arguments": arguments, "result": result})
            messages.append({"role": "tool", "tool_call_id": call_id,
                             "content": json.dumps(result, ensure_ascii=False, separators=(",", ":"))})
        return {"messages": messages, "tool_steps": steps,
                "tool_rounds": state.get("tool_rounds", 0) + 1,
                "tool_calls": [], "assistant_message": {}}

    def _finish_model_node(self, state):
        message = state.get("assistant_message", {})
        content = message.get("content")
        if isinstance(content, list):
            content = "\n".join(item.get("text", "") for item in content if isinstance(item, dict))
        return {"result": {"answer": content or "The model returned no text.",
                           "provider": self.provider.name,
                           "tool_steps": state.get("tool_steps", []),
                           "evidence": [step["result"] for step in state.get("tool_steps", [])
                                        if "result" in step]}}

    @staticmethod
    def _tool_limit_node(_state):
        raise ProviderError("tool round limit reached")

    def _local_fallback_node(self, state):
        result = self.local_handler(state["question"],
                                    provider_error=state.get("provider_error") or None)
        return {"result": result}
