"""
RLM MCP Server - REPL infrastructure for Claude Code.

This server provides tools for analyzing massive documents by executing
Python code in a sandboxed REPL environment. Claude Code acts as the
orchestrator while this server handles code execution.

NO API KEYS REQUIRED - works with Claude Code subscriptions.

SAFEGUARDS:
- 10s execution timeout (auto-kill)
- Max 10 executions per session
- Small file warnings (<50KB)
- Session auto-cleanup
"""

import asyncio
import sys
import os
import io
import re
import time
import pickle
import multiprocessing
from typing import Any, Optional, Dict
from dataclasses import dataclass
from contextlib import redirect_stdout, redirect_stderr

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent


# ============================================================================
# Configuration
# ============================================================================

MAX_EXECUTIONS_PER_SESSION = 10  # Prevent infinite loops
EXECUTION_TIMEOUT = 10.0  # Seconds (reduced from 30)
SMALL_FILE_THRESHOLD = 50000  # 50KB - suggest direct reading
MAX_OUTPUT_CHARS = 20000  # Truncate large outputs


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
    """Sandboxed Python REPL with timeout and execution limits."""

    def __init__(self, context: Any = ""):
        self._globals: Dict[str, Any] = {
            '__builtins__': __builtins__,
            'context': context,
        }
        self.execution_count = 0
        self.created_at = time.time()

    @property
    def globals(self) -> Dict[str, Any]:
        return self._globals

    def execute(self, code: str) -> ExecutionResult:
        """Execute Python code with timeout and limits."""
        # Check execution limit
        if self.execution_count >= MAX_EXECUTIONS_PER_SESSION:
            return ExecutionResult(
                output="",
                execution_time=0.0,
                success=False,
                error=f"Session limit reached ({MAX_EXECUTIONS_PER_SESSION} executions). Use rlm_reset_session to start fresh.",
            )

        self.execution_count += 1

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
            process.join(timeout=EXECUTION_TIMEOUT)

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
                    error=f"TIMEOUT after {EXECUTION_TIMEOUT}s. Code was killed. Simplify your code or break it into smaller steps.",
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
                if len(output) > MAX_OUTPUT_CHARS:
                    output = output[:MAX_OUTPUT_CHARS] + f"\n\n... [OUTPUT TRUNCATED - {len(output):,} chars total]"

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
            description="""Load a LARGE file (>50KB) into RLM for analysis.

WARNING: Only use for files TOO LARGE for your context window.
For small files (<50KB), use direct Read tool instead - it's faster.

After loading, use rlm_execute_code with simple Python:
- print(len(context)) - file size
- print(context[:1000]) - preview
- re.findall(r'pattern', context) - search

LIMITS: 10 code executions per session, 10s timeout per execution.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to file",
                    },
                    "session_id": {
                        "type": "string",
                        "default": "default",
                    },
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="rlm_load_multiple_files",
            description="""Load multiple LARGE files for cross-file analysis.

Access files as: context['filename.txt']

Only use when total size exceeds your context window.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
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
            description="""Run Python code on loaded content.

KEEP CODE SIMPLE:
- One task per execution
- Use print() to see results
- 10s timeout - avoid loops over large data

GOOD: print(context.count('error'))
GOOD: print(re.findall(r'ERROR.*', context)[:10])
BAD: for line in context.split('\\n'): ... (slow!)

Remaining executions shown in response.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Simple Python code",
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
            description="""Get a variable's value from the session.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "variable_name": {"type": "string"},
                    "session_id": {"type": "string", "default": "default"},
                    "max_length": {"type": "integer", "default": 10000},
                },
                "required": ["variable_name"],
            },
        ),
        Tool(
            name="rlm_session_info",
            description="""Check session status and remaining executions.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "default": "default"},
                },
            },
        ),
        Tool(
            name="rlm_reset_session",
            description="""Reset session to get 10 fresh executions.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "default": "default"},
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

            file_size = len(content)

            # Warn if file is small
            if file_size < SMALL_FILE_THRESHOLD:
                return [TextContent(type="text", text=f"""⚠️ SMALL FILE WARNING

File: {file_path}
Size: {file_size:,} chars ({file_size//1000}KB)

This file is SMALL enough to read directly.
Use the Read tool instead - it's faster and simpler.

RLM is designed for files >50KB that exceed context limits.

If you still want to use RLM, call rlm_load_file again.""")]

            # Clear old session
            if session_id in repl_sessions:
                del repl_sessions[session_id]

            repl = get_or_create_session(session_id, context=content)
            repl.globals["context_length"] = file_size
            repl.globals["file_path"] = file_path

            return [TextContent(type="text", text=f"""✓ File loaded

Path: {file_path}
Size: {file_size:,} chars (~{file_size//4:,} tokens)

Preview (first 500 chars):
{content[:500]}{'...' if file_size > 500 else ''}

Session: {session_id}
Executions remaining: {MAX_EXECUTIONS_PER_SESSION}

Use rlm_execute_code with simple Python to analyze.""")]

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
                    fname = os.path.basename(path)
                    contexts[fname] = content
                    total += len(content)
                    info.append(f"  {fname}: {len(content):,} chars")
                else:
                    info.append(f"  {path}: NOT FOUND")

            if total < SMALL_FILE_THRESHOLD:
                return [TextContent(type="text", text=f"""⚠️ SMALL FILES WARNING

Total size: {total:,} chars ({total//1000}KB)

These files are small enough to read directly.
Use the Read tool instead.

RLM is for files >50KB total.""")]

            if session_id in repl_sessions:
                del repl_sessions[session_id]
            repl = get_or_create_session(session_id, context=contexts)
            repl.globals["context_length"] = total

            return [TextContent(type="text", text=f"""✓ Files loaded: {len(contexts)}
Total: {total:,} chars

{chr(10).join(info)}

Access: context['filename.txt']
Executions remaining: {MAX_EXECUTIONS_PER_SESSION}""")]

        elif name == "rlm_execute_code":
            code = arguments["code"]
            session_id = arguments.get("session_id", "default")

            if session_id not in repl_sessions:
                return [TextContent(type="text", text="Error: No file loaded. Use rlm_load_file first.")]

            repl = repl_sessions[session_id]
            remaining_before = MAX_EXECUTIONS_PER_SESSION - repl.execution_count

            result = repl.execute(code)
            remaining_after = MAX_EXECUTIONS_PER_SESSION - repl.execution_count

            if result.success:
                output = result.output or "(no output - use print())"
                status = f"✓ OK ({result.execution_time:.1f}s) | {remaining_after} executions left"
                if remaining_after <= 3:
                    status += " ⚠️"
                return [TextContent(type="text", text=f"{status}\n\n{output}")]
            else:
                return [TextContent(type="text", text=f"✗ Error | {remaining_after} executions left\n\n{result.error}")]

        elif name == "rlm_get_variable":
            var_name = arguments["variable_name"]
            session_id = arguments.get("session_id", "default")
            max_len = arguments.get("max_length", 10000)

            if session_id not in repl_sessions:
                return [TextContent(type="text", text="No session found")]

            repl = repl_sessions[session_id]
            if var_name not in repl.globals:
                return [TextContent(type="text", text=f"Variable '{var_name}' not found")]

            value = str(repl.globals[var_name])
            if len(value) > max_len:
                value = value[:max_len] + "\n... [truncated]"
            return [TextContent(type="text", text=value)]

        elif name == "rlm_session_info":
            session_id = arguments.get("session_id", "default")
            if session_id not in repl_sessions:
                return [TextContent(type="text", text=f"No session '{session_id}'. Use rlm_load_file to start.")]

            repl = repl_sessions[session_id]
            remaining = MAX_EXECUTIONS_PER_SESSION - repl.execution_count
            ctx_len = repl.globals.get('context_length', 0)
            age = int(time.time() - repl.created_at)

            vars_info = [k for k in repl.globals.keys() if k not in ['__builtins__', 'context']]

            return [TextContent(type="text", text=f"""Session: {session_id}
Context: {ctx_len:,} chars
Executions: {repl.execution_count}/{MAX_EXECUTIONS_PER_SESSION} used
Remaining: {remaining}
Age: {age}s
Variables: {', '.join(vars_info) or '(none)'}""")]

        elif name == "rlm_reset_session":
            session_id = arguments.get("session_id", "default")
            if session_id in repl_sessions:
                del repl_sessions[session_id]
            return [TextContent(type="text", text=f"✓ Session '{session_id}' reset. Load a file to start fresh.")]

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
    if sys.platform == 'win32':
        multiprocessing.set_start_method('spawn', force=True)
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
