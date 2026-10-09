"""Tool layer: explicit, permissioned capabilities exposed to the agent.

Nothing here gives shell access — each tool is a narrow function with a
declared safety category (see registry.SafetyCategory).
"""

from app.advisor import resources as _advisor  # noqa: F401
from app.diagnostics import runbooks as _diagnostics  # noqa: F401
from app.remediation import verification as _remediation  # noqa: F401
from app.tools.github import github as _github  # noqa: F401
from app.tools.gitops import argocd as _argocd  # noqa: F401
from app.tools.gitops import helm as _helm  # noqa: F401
from app.tools.kubernetes import tools as _kubernetes  # noqa: F401
from app.tools.loki import loki as _loki  # noqa: F401
from app.tools.prometheus import prometheus as _prometheus  # noqa: F401
from app.tools.registry import SafetyCategory, ToolSpec, all_tools, get_tool, register, tools_in_category

__all__ = [
    "SafetyCategory",
    "ToolSpec",
    "all_tools",
    "get_tool",
    "register",
    "tools_in_category",
]


def build_tools() -> list:
    """Build LangChain tools from the registry.

    Read tools pass straight through; operational tools are wrapped with the
    approval guard so the agent can only propose them. Destructive tools are
    never registered in the first place.
    """
    # Import modules for their registration side effects.
    from langchain_core.tools import StructuredTool

    from app.tools.approvals import guarded_tool

    tools = []
    for spec in all_tools():
        func = spec.func if spec.category is SafetyCategory.READ else guarded_tool(spec)
        tools.append(StructuredTool.from_function(func=func, name=spec.name, description=spec.description))
    return tools
