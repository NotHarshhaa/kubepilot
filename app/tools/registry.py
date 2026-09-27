"""Central tool registry with safety classification.

Every capability the agent can use is registered here with a SafetyCategory.
The safety model (README): reads run automatically, operational actions
(restart/scale/rollback) require approval, and destructive actions are never
exposed to the agent at all.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class SafetyCategory(str, Enum):
    READ = "read"
    OPERATIONAL = "operational"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    category: SafetyCategory
    func: Callable


_REGISTRY: dict[str, ToolSpec] = {}


def register(category: SafetyCategory) -> Callable:
    """Register a tool function under its own name with a safety category."""

    def decorator(fn: Callable) -> Callable:
        _REGISTRY[fn.__name__] = ToolSpec(
            name=fn.__name__,
            description=(fn.__doc__ or "").strip(),
            category=category,
            func=fn,
        )
        return fn

    return decorator


def get_tool(name: str) -> ToolSpec | None:
    return _REGISTRY.get(name)


def all_tools() -> list[ToolSpec]:
    return sorted(_REGISTRY.values(), key=lambda spec: spec.name)


def tools_in_category(category: SafetyCategory) -> list[ToolSpec]:
    return [spec for spec in all_tools() if spec.category is category]
