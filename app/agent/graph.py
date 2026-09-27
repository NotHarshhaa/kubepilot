"""Single-agent LangGraph workflow: a ReAct loop over the registered tool layer."""

from langchain_core.messages import ToolMessage

from app.agent.llm import get_llm
from app.agent.prompts import SYSTEM_PROMPT
from app.tools import build_tools

_agent = None


def get_agent():
    """Build the agent once per process (tools and settings are fixed at startup)."""
    global _agent
    if _agent is None:
        from langgraph.prebuilt import create_react_agent

        _agent = create_react_agent(get_llm(), build_tools(), prompt=SYSTEM_PROMPT)
    return _agent


def run_agent(question: str) -> dict:
    """Run the agent on a natural-language question.

    Returns the final answer plus the ordered list of tools the agent called,
    so callers can show the investigation trace.
    """
    result = get_agent().invoke({"messages": [("user", question)]})
    messages = result["messages"]
    trace = [message.name for message in messages if isinstance(message, ToolMessage)]
    answer = messages[-1].content
    if isinstance(answer, list):  # provider-specific content blocks
        answer = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in answer
        )
    return {"question": question, "answer": answer, "tools_used": trace}
