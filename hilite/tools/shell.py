"""Shell and Python execution tools."""

from __future__ import annotations

import subprocess
import sys
import traceback
from io import StringIO


def execute_command(command: str, timeout: int = 60) -> str:
    """Execute a shell command and return stdout + stderr."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = []
        if result.stdout:
            output.append(result.stdout)
        if result.stderr:
            output.append(f"[stderr]\n{result.stderr}")
        if result.returncode != 0:
            output.append(f"[exit code: {result.returncode}]")
        return "\n".join(output) if output else "[no output]"
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout}s"
    except Exception as e:
        return f"Error executing command: {e}"


def execute_python(code: str) -> str:
    """Execute Python code and return the output (stdout + any exception)."""
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    stdout_buffer = StringIO()
    stderr_buffer = StringIO()
    sys.stdout = stdout_buffer
    sys.stderr = stderr_buffer

    try:
        # Execute in a clean namespace
        namespace: dict[str, object] = {}
        exec(code, namespace)  # noqa: S102
    except Exception:
        stderr_buffer.write(traceback.format_exc())
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    output = []
    stdout_val = stdout_buffer.getvalue()
    stderr_val = stderr_buffer.getvalue()
    if stdout_val:
        output.append(stdout_val)
    if stderr_val:
        output.append(f"[stderr]\n{stderr_val}")
    return "\n".join(output) if output else "[no output]"
