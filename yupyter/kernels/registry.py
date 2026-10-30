"""Maps executor names to Executor instances (one lazily created per name)."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Hashable

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


def agent_names() -> list[str]:
    """Registered agent kernel names, e.g. ``["agent:claude"]``."""
    return list(_AGENTS)


class ExecutorRegistry:
    """One executor (kernel / agent) per ``(notebook path, executor name)``.

    A plain-name key in ``_executors`` is a shared executor that serves every
    notebook (tests inject fakes that way).
    """

    def __init__(self) -> None:
        self._executors: dict[Hashable, Executor] = {}

    def normalize(self, kernel_name: str) -> str:
        """Resolve aliases (``py`` -> ``python3``)."""
        return config.resolve_alias(kernel_name)

    @staticmethod
    def _key(name: str, path: Path | str | None) -> Hashable:
        return name if path is None else (str(path), name)

    def resolve(self, kernel_name: str, path: Path | str | None = None) -> Executor:
        name = self.normalize(kernel_name)
        ex = self._executors.get(name)  # shared override
        if ex is not None:
            return ex
        key = self._key(name, path)
        ex = self._executors.get(key)
        if ex is None:
            if name.startswith("agent:"):
                factory = _AGENTS.get(name)
                if factory is None:
                    raise KeyError(f"no agent registered for {name!r}")
                ex = factory(name)
            else:
                ex = JupyterExecutor(name)
            self._executors[key] = ex
        return ex

    def find(self, kernel_name: str, path: Path | str | None = None) -> list[Executor]:
        """Existing executors with this name; for one notebook, or all if ``path`` is None."""
        name = self.normalize(kernel_name)
        out = []
        for k, ex in self._executors.items():
            if k == name or (isinstance(k, tuple) and k[1] == name and (path is None or k[0] == str(path))):
                out.append(ex)
        return out

    def names(self) -> list[str]:
        return list(dict.fromkeys(k if isinstance(k, str) else k[1] for k in self._executors))

    def running(self, path: Path | str) -> bool:
        """True if a kernel process for this notebook is up."""
        return any(
            isinstance(k, tuple) and k[0] == str(path) and getattr(ex, "is_started", False) is True
            for k, ex in self._executors.items()
        )

    def rename(self, old: Path | str, new: Path | str) -> None:
        for k in [k for k in self._executors if isinstance(k, tuple) and k[0] == str(old)]:
            self._executors[(str(new), k[1])] = self._executors.pop(k)

    async def shutdown(self) -> None:
        for ex in list(self._executors.values()):
            try:
                await ex.shutdown()
            except Exception:
                pass
        self._executors.clear()
