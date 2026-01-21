"""
RLM MCP Server - Recursive Language Model tools for Claude Code.

Enables Claude Code to analyze massive documents that exceed context windows
by providing a Python REPL environment for programmatic exploration.

No API keys required - works with Claude Code subscriptions.
"""

__version__ = "0.1.0b1"
__author__ = "Ahmed Ali"

from .server import main

__all__ = ["main", "__version__"]
