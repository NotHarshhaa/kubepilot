"""Single-agent LangGraph workflow: a ReAct loop over the registered tool layer with session memory."""

import uuid
from collections.abc import Generator

from langchain_core.messages import ToolMessage
from langgraph.checkpoint.memory import MemorySaver

from app.agent.llm import get_llm
from app.agent.prompts import SYSTEM_PROMPT
from app.tools import build_tools

_agent = None
_memory = MemorySaver()


def get_agent():
    """Build the agent once per process (tools and settings are fixed at startup)."""
    global _agent, _memory
    if _agent is None:
        from langgraph.prebuilt import create_react_agent

        _agent = create_react_agent(
            get_llm(),
            build_tools(),
            prompt=SYSTEM_PROMPT,
            checkpointer=_memory,
        )
    return _agent


def reset_agent() -> None:
    """Reset the agent instance and memory checkpointer (e.g. for testing)."""
    global _agent, _memory
    _agent = None
    _memory = MemorySaver()


def run_agent(question: str, session_id: str | None = None) -> dict:
    """Run the agent on a natural-language question within an optional session thread.

    Returns the final answer, the ordered list of tools used, and the session_id
    for multi-turn conversational continuity.
    """
    sid = session_id or uuid.uuid4().hex[:12]
    config = {"configurable": {"thread_id": sid}}
    result = get_agent().invoke({"messages": [("user", question)]}, config=config)
    messages = result["messages"]
    trace = [message.name for message in messages if isinstance(message, ToolMessage)]
    answer = messages[-1].content
    if isinstance(answer, list):  # provider-specific content blocks
        answer = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in answer)
    return {"question": question, "answer": answer, "tools_used": trace, "session_id": sid}


def stream_agent(question: str, session_id: str | None = None) -> Generator[dict, None, None]:
    """Stream agent execution step-by-step (tool calls, tool results, and final answer)."""
    sid = session_id or uuid.uuid4().hex[:12]
    config = {"configurable": {"thread_id": sid}}
    agent = get_agent()

    tools_used = []

    for chunk in agent.stream({"messages": [("user", question)]}, config=config, stream_mode="updates"):
        for node_name, node_update in chunk.items():
            if node_name == "agent":
                msgs = node_update.get("messages", [])
                for msg in msgs:
                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                        for tc in msg.tool_calls:
                            t_name = tc.get("name", "unknown")
                            tools_used.append(t_name)
                            yield {
                                "type": "tool_call",
                                "tool": t_name,
                                "args": tc.get("args", {}),
                                "session_id": sid,
                            }
                    elif getattr(msg, "content", None):
                        content = msg.content
                        if isinstance(content, list):
                            content = "".join(
                                part.get("text", "") if isinstance(part, dict) else str(part)
                                for part in content
                            )
                        yield {
                            "type": "content",
                            "content": content,
                            "session_id": sid,
                        }
            elif node_name == "tools":
                msgs = node_update.get("messages", [])
                for msg in msgs:
                    yield {
                        "type": "tool_result",
                        "tool": getattr(msg, "name", "unknown"),
                        "result": str(getattr(msg, "content", "")),
                        "session_id": sid,
                    }

    state = agent.get_state(config)
    messages = state.values.get("messages", []) if state else []
    final_answer = ""
    if messages:
        last = messages[-1]
        final_answer = last.content
        if isinstance(final_answer, list):
            final_answer = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part) for part in final_answer
            )

    yield {
        "type": "done",
        "question": question,
        "answer": final_answer,
        "tools_used": tools_used,
        "session_id": sid,
    }


def get_session_history(session_id: str) -> list[dict]:
    """Retrieve message history for a given session thread."""
    config = {"configurable": {"thread_id": session_id}}
    state = get_agent().get_state(config)
    if not state or not state.values:
        return []
    history = []
    for msg in state.values.get("messages", []):
        history.append(
            {
                "role": getattr(msg, "type", "unknown"),
                "content": getattr(msg, "content", ""),
                "name": getattr(msg, "name", None),
            }
        )
    return history
