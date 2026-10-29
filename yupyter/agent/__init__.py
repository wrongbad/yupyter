"""The Claude agent as a kernel (plans/poc.md section 5).

Importing this package registers the ``agent:claude`` executor.
"""
from .executor import ClaudeAgentExecutor, register

register()

__all__ = ["ClaudeAgentExecutor", "register"]
