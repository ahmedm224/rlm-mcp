"""
RLM MCP Server - REPL infrastructure for Claude Code.

This server provides tools for analyzing massive documents by executing
Python code in a sandboxed REPL environment. Claude Code acts as the
orchestrator while this server handles code execution.

NO API KEYS REQUIRED - works with Claude Code subscriptions.
"""

import asyncio
import sys
import os
import io
import re
import time
import pickle
import multiprocessing
from typing import Any, Optional, Dict, Callable
from dataclasses import dataclass, field
from contextlib import redirect_stdout, redirect_stderr

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent


# ============================================================================
# REPL Environment
# ============================================================================

@dataclass
class ExecutionResult:
    """Result from code execution."""
    output: str
    execution_time: float
    success: bool
    error: Optional[str] = None


def _execute_in_process(code: str, globals_dict: dict, output_queue: multiprocessing.Queue):
    """Execute code in a separate process for timeout enforcement."""
    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()

    try:
        exec_globals = {'__builtins__': __builtins__}
        for k, v in globals_dict.items():
            if k != '__builtins__':
                exec_globals[k] = v

        with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
            exec(code, exec_globals)

        result_vars = {}
        for k, v in exec_globals.items():
            if k != '__builtins__' and not callable(v):
                try:
                    result_vars[k] = v
                except:
                    result_vars[k] = str(v)

        output_queue.put({
            'success': True,
            'output': stdout_capture.getvalue(),
            'stderr': stderr_capture.getvalue(),
            'error': None,
            'variables': result_vars,
        })
    except Exception as e:
        output_queue.put({
            'success': False,
            'output': stdout_capture.getvalue(),
            'stderr': stderr_capture.getvalue(),
            'error': f"{type(e).__name__}: {str(e)}",
            'variables': {},
        })


class REPLEnvironment:
    """Sandboxed Python REPL with timeout enforcement via multiprocessing."""

    def __init__(
        self,
        context: Any = "",
        max_output_chars: int = 50000,
        execution_timeout: float = 30.0,
    ):
        self.max_output_chars = max_output_chars
        self.execution_timeout = execution_timeout
        self._globals: Dict[str, Any] = {
            '__builtins__': __builtins__,
            'context': context,
        }

    @property
    def globals(self) -> Dict[str, Any]:
        return self._globals

    def execute(self, code: str) -> ExecutionResult:
        """Execute Python code with timeout protection."""
        # Clean XML artifacts from code
        code = re.sub(r'</?\w+>', '', code)
        lines = code.split('\n')
        cleaned_lines = [line for line in lines if not re.match(r'^\s*</?\w+>\s*$', line)]
        code = '\n'.join(cleaned_lines).strip()

        if not code:
            return ExecutionResult(
                output="(empty code)",
                execution_time=0.0,
                success=True,
            )

        start_time = time.time()

        # Prepare picklable globals
        picklable_globals = {}
        for k, v in self._globals.items():
            if k == '__builtins__':
                continue
            try:
                pickle.dumps(v)
                picklable_globals[k] = v
            except:
                pass

        output_queue = multiprocessing.Queue()
        process = multiprocessing.Process(
            target=_execute_in_process,
            args=(code, picklable_globals, output_queue)
        )

        try:
            process.start()
            process.join(timeout=self.execution_timeout)

            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=1)

                return ExecutionResult(
                    output="",
                    execution_time=time.time() - start_time,
                    success=False,
                    error=f"Execution timed out after {self.execution_timeout}s (killed)",
                )

            if not output_queue.empty():
                result = output_queue.get_nowait()
                output = result['output']
                if result['stderr']:
                    output += f"\n[stderr]\n{result['stderr']}"

                # Update globals with new variables
                for k, v in result.get('variables', {}).items():
                    if k != '__builtins__':
                        self._globals[k] = v

                # Truncate if needed
                if len(output) > self.max_output_chars:
                    output = output[:self.max_output_chars] + f"\n... [truncated]"

                return ExecutionResult(
                    output=output,
                    execution_time=time.time() - start_time,
                    success=result['success'],
                    error=result['error'],
                )
            else:
                return ExecutionResult(
                    output="",
                    execution_time=time.time() - start_time,
                    success=False,
                    error="No result from execution",
                )

        except Exception as e:
            return ExecutionResult(
                output="",
                execution_time=time.time() - start_time,
                success=False,
                error=f"{type(e).__name__}: {str(e)}",
            )
        finally:
            if process.is_alive():
                process.kill()


# ============================================================================
# MCP Server
# ============================================================================

server = Server("rlm-mcp-server")
repl_sessions: Dict[str, REPLEnvironment] = {}


def get_or_create_session(session_id: str = "default", context: Any = None) -> REPLEnvironment:
    """Get existing REPL session or create new one."""
    if session_id not in repl_sessions:
        repl_sessions[session_id] = REPLEnvironment(context=context or "")
    return repl_sessions[session_id]


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available RLM tools."""
    return [
        Tool(
            name="rlm_load_file",
            description="""Load a massive file into the RLM REPL environment.

Use this for files too large for your context window. After loading:
- 'context' variable contains the file content
- Use rlm_execute_code to analyze with Python

Example: Load a 10GB log file, then search it with regex.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the file",
                    },
                    "session_id": {
                        "type": "string",
                        "default": "default",
                        "description": "Session ID for parallel analyses",
                    },
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="rlm_load_multiple_files",
            description="""Load multiple files into the REPL as a dict.

Files are accessible as context['filename.txt'].
Use for cross-file analysis.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of absolute file paths",
                    },
                    "session_id": {
                        "type": "string",
                        "default": "default",
                    },
                },
                "required": ["file_paths"],
            },
        ),
        Tool(
            name="rlm_execute_code",
            description="""Execute Python code in the REPL.

Available:
- 'context': Loaded file content
- Standard libraries (re, json, collections, etc.)
- Variables persist across calls

Use print() to see results. Code times out after 30s.

Tips:
- context[:10000] for first 10K chars
- re.findall(r'pattern', context) for searching
- len(context) for size""",
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code to execute",
                    },
                    "session_id": {
                        "type": "string",
                        "default": "default",
                    },
                },
                "required": ["code"],
            },
        ),
        Tool(
            name="rlm_get_variable",
            description="""Get a variable's value from the REPL.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "variable_name": {
                        "type": "string",
                        "description": "Variable name to retrieve",
                    },
                    "session_id": {
                        "type": "string",
                        "default": "default",
                    },
                    "max_length": {
                        "type": "integer",
                        "default": 10000,
                    },
                },
                "required": ["variable_name"],
            },
        ),
        Tool(
            name="rlm_session_info",
            description="""Get info about current REPL session.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "default": "default",
                    },
                },
            },
        ),
        Tool(
            name="rlm_reset_session",
            description="""Clear a REPL session to free memory.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "default": "default",
                    },
                },
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""
    try:
        if name == "rlm_load_file":
            file_path = arguments["file_path"]
            session_id = arguments.get("session_id", "default")

            if not os.path.exists(file_path):
                return [TextContent(type="text", text=f"Error: File not found: {file_path}")]

            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            if session_id in repl_sessions:
                del repl_sessions[session_id]
            repl = get_or_create_session(session_id, context=content)
            repl.globals["context_length"] = len(content)
            repl.globals["file_path"] = file_path

            return [TextContent(type="text", text=f"""File loaded: {file_path}
Size: {len(content):,} chars (~{len(content)//4:,} tokens)

Preview:
{content[:1000]}{'...' if len(content) > 1000 else ''}

Use rlm_execute_code to analyze.""")]

        elif name == "rlm_load_multiple_files":
            file_paths = arguments["file_paths"]
            session_id = arguments.get("session_id", "default")

            contexts = {}
            total = 0
            info = []
            for path in file_paths:
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    name = os.path.basename(path)
                    contexts[name] = content
                    total += len(content)
                    info.append(f"  {name}: {len(content):,} chars")
                else:
                    info.append(f"  {path}: NOT FOUND")

            if session_id in repl_sessions:
                del repl_sessions[session_id]
            repl = get_or_create_session(session_id, context=contexts)
            repl.globals["context_length"] = total

            return [TextContent(type="text", text=f"""Files loaded: {len(contexts)}
Total: {total:,} chars

{chr(10).join(info)}

Access: context['filename.txt']""")]

        elif name == "rlm_execute_code":
            code = arguments["code"]
            session_id = arguments.get("session_id", "default")

            repl = get_or_create_session(session_id)
            result = repl.execute(code)

            if result.success:
                output = result.output or "(no output - use print())"
                return [TextContent(type="text", text=f"OK ({result.execution_time:.2f}s)\n\n{output}")]
            else:
                return [TextContent(type="text", text=f"Error: {result.error}")]

        elif name == "rlm_get_variable":
            var_name = arguments["variable_name"]
            session_id = arguments.get("session_id", "default")
            max_len = arguments.get("max_length", 10000)

            repl = get_or_create_session(session_id)
            if var_name not in repl.globals:
                return [TextContent(type="text", text=f"Variable '{var_name}' not found")]

            value = str(repl.globals[var_name])
            if len(value) > max_len:
                value = value[:max_len] + "\n... [truncated]"
            return [TextContent(type="text", text=value)]

        elif name == "rlm_session_info":
            session_id = arguments.get("session_id", "default")
            if session_id not in repl_sessions:
                return [TextContent(type="text", text=f"Session '{session_id}' not found")]

            repl = repl_sessions[session_id]
            vars_info = []
            for k, v in repl.globals.items():
                if k not in ['__builtins__', 'context']:
                    vars_info.append(f"  {k}: {type(v).__name__}")

            ctx_len = repl.globals.get('context_length', len(str(repl.globals.get('context', ''))))
            return [TextContent(type="text", text=f"""Session: {session_id}
Context: {ctx_len:,} chars
Variables:
{chr(10).join(vars_info) or '  (none)'}""")]

        elif name == "rlm_reset_session":
            session_id = arguments.get("session_id", "default")
            if session_id in repl_sessions:
                del repl_sessions[session_id]
            return [TextContent(type="text", text=f"Session '{session_id}' reset")]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        import traceback
        return [TextContent(type="text", text=f"Error: {e}\n{traceback.format_exc()}")]


async def run_server():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main():
    """Entry point."""
    # Windows multiprocessing fix
    if sys.platform == 'win32':
        multiprocessing.set_start_method('spawn', force=True)
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
