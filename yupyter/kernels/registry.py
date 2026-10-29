"""Maps executor names to Executor instances (one lazily created per name)."""
from __future__ import annotations

from typing import Callable

from .. import config
from .executor import Executor
from .jupyter_executor import JupyterExecutor

AgentFactory = Callable[[str], Executor]

_AGENTS: dict[str, AgentFactory] = {}


def register_agent(prefix_name: str, factory: AgentFactory) -> None:
    """Register an agent executor factory.

    ``prefix_name`` may be ``"claude"`` or ``"agent:claude"``; cells whose
    kernel is ``"agent:claude"`` are then run by ``factory("agent:claude")``.
    """
    key = prefix_name if prefix_name.startswith("agent:") else f"agent:{prefix_name}"
    _AGENTS[key] = factory


class ExecutorRegistry:
    def __init__(self) -> None:
        self._executors: dict[str, Executor] = {}

    def normalize(self, kernel_name: str) -> str:
        """Resolve aliases (``py`` -> ``python3``)."""
        return config.resolve_alias(kernel_name)

    def resolve(self, kernel_name: str) -> Executor:
        name = self.normalize(kernel_name)
        ex = self._executors.get(name)
        if ex is None:
            if name.startswith("agent:"):
                factory = _AGENTS.get(name)
                if factory is None:
                    raise KeyError(f"no agent registered for {name!r}")
                ex = factory(name)
            else:
                ex = JupyterExecutor(name)
            self._executors[name] = ex
        return ex

    def names(self) -> list[str]:
        return list(self._executors)

    async def shutdown(self) -> None:
        for ex in list(self._executors.values()):
            try:
                await ex.shutdown()
            except Exception:
                pass
        self._executors.clear()
