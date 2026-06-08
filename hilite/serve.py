"""Serve mode -- an ndjson streaming/interactive backend over stdin/stdout.

One ``hilite --serve`` process hosts a single live ``AIAgent`` for its
lifetime and speaks a newline-delimited JSON protocol so a GUI front-end can
drive it as an agent backend.  See ``docs/05_serve-mode-spec.md``.

stdout is reserved exclusively for ndjson events (one object per line, each
guarded by a lock and flushed).  All diagnostics stay on stderr.
"""

from __future__ import annotations

import itertools
import json
import os
import queue
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from anthropic.types import ToolParam

from hilite.agent.agent import AIAgent
from hilite.constants import DEFAULT_MODEL
from hilite.tools.registry import ToolRegistry


class Interactor:
    """Round-trips interactive questions between the agent loop and the
    front-end.

    Owned by the server and injected into the interactive tool handlers.  Each
    ``ask`` / ``review_plan`` emits an event, blocks the main (agent) thread on
    a ``threading.Event`` until the reader thread calls ``resolve`` with the
    matching answer, then returns the tool result string the model sees.
    """

    def __init__(self, emit: Callable[[dict], None], next_id: Callable[[str], str]):
        self._emit = emit            # the locked stdout emit()
        self._next_id = next_id      # mints "ask_N" / "plan_N"
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()

    def ask(self, header: str, question: str, options: list[dict]) -> str:
        rid = self._next_id("ask")
        ev = threading.Event()
        with self._lock:
            self._pending[rid] = {"event": ev, "value": None}
        self._emit({"type": "ask_user", "id": rid, "header": header,
                    "text": question, "options": options})
        ev.wait()
        idx = self._pending.pop(rid)["value"]          # selectedIndex
        return options[idx]["label"]                   # tool result the model sees

    def review_plan(self, plan: str) -> str:
        rid = self._next_id("plan")
        ev = threading.Event()
        with self._lock:
            self._pending[rid] = {"event": ev, "value": None}
        self._emit({"type": "review_plan", "id": rid, "plan": plan})
        ev.wait()
        decision = self._pending.pop(rid)["value"]     # {"decision","feedback"}
        if decision["decision"] == "approve":
            return "Plan approved. Proceed with implementation."
        return f"Plan rejected. Feedback: {decision.get('feedback', '')}"

    def ui_request(self, action: str, **args: Any) -> Any:
        """Emit a *blocking* ``ui_action`` and wait for its ``ui_result``.

        Mirrors ``ask`` / ``review_plan``: mint an id, block on a
        ``threading.Event`` until the reader thread resolves it, then hand the
        front-end's ``value`` back to the tool. No v1 catalog tool blocks, but
        the wire format ships now so it stays stable.
        """
        rid = self._next_id("ui")
        ev = threading.Event()
        with self._lock:
            self._pending[rid] = {"event": ev, "value": None}
        self._emit({"type": "ui_action", "action": action, "id": rid, "args": args})
        ev.wait()
        return self._pending.pop(rid)["value"]         # the ui_result.value

    def resolve(self, rid: str, value: Any) -> None:   # called by reader thread
        with self._lock:
            p = self._pending.get(rid)
        if p:
            p["value"] = value
            p["event"].set()


# --- Interactive tool schemas (registered only in serve mode) ---------------

INTERACTIVE_TOOL_SCHEMAS: list[ToolParam] = [
    ToolParam(
        name="ask_user",
        description=(
            "Ask the user to choose between options when you genuinely need a "
            "decision you cannot make yourself (a preference, an ambiguous "
            "requirement, or an action that needs their approval). Do not use "
            "it for questions you can answer by inspecting the project."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "header": {
                    "type": "string",
                    "description": "Very short label for the question (a few words).",
                },
                "question": {
                    "type": "string",
                    "description": "The full question to ask the user.",
                },
                "options": {
                    "type": "array",
                    "description": "The choices to present.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "required": ["label"],
                    },
                },
            },
            "required": ["header", "question", "options"],
        },
    ),
    ToolParam(
        name="exit_plan_mode",
        description=(
            "Present a proposed implementation plan to the user and block for "
            "their approval before making changes. Use after you have finished "
            "planning a non-trivial task and are ready to start implementing."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "plan": {
                    "type": "string",
                    "description": "The implementation plan to present, in markdown.",
                },
            },
            "required": ["plan"],
        },
    ),
]


# --- Frontend (UI) tools -----------------------------------------------------

class UIEmitter:
    """Drives the front-end's UI by emitting fire-and-forget ``ui_action`` events.

    HiLite tool handlers run in-process and can't touch the GUI, so a UI tool
    instead emits an event the front-end consumes (open a webview, open a
    terminal, render markdown, close panels). Each call returns a short
    confirmation string the model sees as the tool result.
    """

    def __init__(self, emit: Callable[[dict], None]):
        self._emit = emit            # the locked stdout emit()

    def action(self, name: str, **args: Any) -> str:
        # Drop None-valued optional args so the front-end gets a clean payload.
        payload = {k: v for k, v in args.items() if v is not None}
        self._emit({"type": "ui_action", "action": name, "args": payload})
        return f"Done: {name}"


FRONTEND_TOOL_SCHEMAS: list[ToolParam] = [
    ToolParam(
        name="open_web",
        description=(
            "Open a web page in the user's artifact panel. Use when you want the "
            "user to see or interact with a page (a login form, docs, a dashboard)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to open."},
                "title": {
                    "type": "string",
                    "description": "Optional panel title.",
                },
            },
            "required": ["url"],
        },
    ),
    ToolParam(
        name="open_claude_cli",
        description=(
            "The PRIMARY way to do real work in the current repo — coding, "
            "edits, refactors, planning, architecture, and exploration. Open "
            "the Claude CLI in the user's artifact panel (rooted in the repo), "
            "then DRIVE it with terminal_send_text / terminal_snapshot / "
            "terminal_send_key to carry out the task and observe results. Do "
            "NOT do coding or design work yourself (read_file / execute_command "
            "/ edits) and do NOT answer implementation or design questions "
            "directly — hand them to the Claude CLI. Also used to let the user "
            "run or watch the CLI (e.g. logging in)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "cwd": {
                    "type": "string",
                    "description": "Optional working directory for the terminal.",
                },
                "title": {
                    "type": "string",
                    "description": "Optional panel title.",
                },
            },
            "required": [],
        },
    ),
    ToolParam(
        name="open_markdown",
        description=(
            "Render markdown in the user's artifact panel. Use to show "
            "instructions, a summary, or formatted reference material."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Panel title."},
                "text": {"type": "string", "description": "The markdown to render."},
            },
            "required": ["title", "text"],
        },
    ),
    ToolParam(
        name="close_artifacts",
        description="Close all open artifact panels in the user's UI.",
        input_schema={"type": "object", "properties": {}, "required": []},
    ),
    ToolParam(
        name="focus_artifact",
        description="Bring a specific open artifact panel to the foreground by id.",
        input_schema={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "The artifact id to focus."},
            },
            "required": ["id"],
        },
    ),
    ToolParam(
        name="terminal_snapshot",
        description=(
            "Read what is currently displayed on the interactive terminal so you "
            "can decide the next keystroke. Returns the rendered screen text and "
            "cursor position. Sends no input. Use before acting, and again after "
            "acting to see the result."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "terminal_id": {
                    "type": "string",
                    "description": "Which terminal (optional; defaults to the active one).",
                },
            },
            "required": [],
        },
    ),
    ToolParam(
        name="terminal_send_text",
        description=(
            "Type text into the interactive terminal (e.g. a slash command like "
            "'/mcp'). Returns the resulting screen. Set submit=true to press Enter "
            "after the text."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to type."},
                "submit": {
                    "type": "boolean",
                    "description": "Press Enter after typing (default true).",
                },
                "terminal_id": {
                    "type": "string",
                    "description": "Which terminal (optional; defaults to the active one).",
                },
            },
            "required": ["text"],
        },
    ),
    ToolParam(
        name="terminal_send_key",
        description=(
            "Send a special key to the interactive terminal to navigate menus "
            "(e.g. arrow keys then Enter to pick an item). Returns the resulting "
            "screen. Snapshot first so you know what is highlighted."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "enum": ["up", "down", "left", "right", "enter",
                             "escape", "tab", "backspace", "space", "ctrl-c"],
                    "description": "Which key to send.",
                },
                "count": {
                    "type": "integer",
                    "description": "How many times to send it (default 1).",
                },
                "terminal_id": {
                    "type": "string",
                    "description": "Which terminal (optional; defaults to the active one).",
                },
            },
            "required": ["key"],
        },
    ),
    ToolParam(
        name="terminal_wait_for_bell",
        description=(
            "Block until the Claude CLI needs attention — either it FINISHES a turn "
            "and returns to the input prompt, or it shows a PERMISSION PROMPT and is "
            "waiting for your approval. Call this ONLY right AFTER you submit work "
            "with terminal_send_text (or answer a prompt with terminal_send_key): it "
            "consumes nothing while waiting, then wakes you exactly when Claude is "
            "ready. The reply's 'reason' tells you what happened: 'stop'/'idle_prompt' "
            "= turn complete; 'permission_prompt' = it needs you to approve a tool "
            "use (answer with terminal_send_key); 'timeout' = nothing happened in "
            "time; 'process_exit' = the CLI quit. The reply already includes the "
            "settled screen in 'text' — ACT ON IT DIRECTLY; only call "
            "terminal_snapshot again if bellCount > 1 or belled is false. "
            "DO NOT use this to wait for the CLI to START UP or become ready — no "
            "signal fires at launch, so it will just burn the full timeout. To check "
            "readiness after open_claude_cli, use terminal_snapshot (the input prompt "
            "'>' means ready), then send your task and wait. On timeout, judge the "
            "screen yourself, then wait again. Returns an error if the terminal is "
            "not found."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "terminal_id": {
                    "type": "string",
                    "description": "Which terminal (optional; defaults to the active one).",
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": (
                        "How long to wait before giving up, in ms (default 600000 = "
                        "10 min). Raise it for known-long tasks; on timeout the reply "
                        "has belled=false so you can re-arm and wait again."
                    ),
                },
                "quiet_ms": {
                    "type": "integer",
                    "description": (
                        "Reserved screen-quiescence hint, in ms (default 400). The app "
                        "settles the screen before replying; you rarely need to set this."
                    ),
                },
            },
            "required": [],
        },
    ),
    # --- Cross-repo delegation (doc 13). Orchestrator-only; the front-end spawns a
    #     scoped worker session per call and resolves the blocking ui_request with a
    #     result contract. ---
    ToolParam(
        name="run_in_workspace",
        description=(
            "Delegate a self-contained coding task to one of the project's repos (a "
            "'workspace', addressed by its role label like 'BE' or 'FE') and WAIT for "
            "it. Blocks until that repo's worker finishes, then returns a result "
            "contract: a summary, the files it changed, and any cross-repo contract "
            "(e.g. a new endpoint signature) the next repo needs. Use when the result "
            "must be in hand before you continue — e.g. a BE change the FE then "
            "consumes. The worker runs with that repo's own memory and MCP config."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": "Which repo to run in, by role label (e.g. 'BE', 'FE') or name.",
                },
                "task": {
                    "type": "string",
                    "description": "The self-contained task for that repo's worker to carry out.",
                },
            },
            "required": ["workspace", "task"],
        },
    ),
    ToolParam(
        name="dispatch_to_workspace",
        description=(
            "Start a coding task in one of the project's repos WITHOUT waiting. "
            "Returns immediately with a handle; the worker runs in the background and "
            "its result is delivered to you as a later message. Use to run "
            "independent repos in parallel — dispatch BE and FE, narrate it, and keep "
            "working. For dependent work where you need the result now, use "
            "run_in_workspace instead."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": "Which repo to run in, by role label (e.g. 'BE', 'FE') or name.",
                },
                "task": {
                    "type": "string",
                    "description": "The self-contained task for that repo's worker to carry out.",
                },
            },
            "required": ["workspace", "task"],
        },
    ),
    ToolParam(
        name="await_workspaces",
        description=(
            "Block until every dispatched worker in the given list of handles has "
            "finished, then return all their result contracts together. Use for a "
            "hard join — e.g. a final integration step that needs both the BE and FE "
            "results in one turn. For independent work, prefer letting each "
            "completion arrive on its own rather than blocking here."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "handles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Handles returned by earlier dispatch_to_workspace calls.",
                },
            },
            "required": ["handles"],
        },
    ),
    # --- Dynamic project discovery (doc 14.1). HiLite only declares these and
    #     proxies them over the ui_request/ui_result channel; the behavior lives
    #     app-side (doc 14.2: write the HILITE.md + index, run the loader agent,
    #     spawn child sessions, deliver child→parent callbacks). ---
    ToolParam(
        name="write_hilite_md",
        description=(
            "Create or refresh a repo's HILITE.md project file (the tool-managed, "
            "progressive-disclosure context file) and its entry in the global "
            "project index. Use after deriving a repo's context for the first time, "
            "or when its overview/sections need updating. The frontmatter's name + "
            "description become the repo's routing signal in the global index."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Repo root where HILITE.md should be written.",
                },
                "frontmatter": {
                    "type": "object",
                    "description": (
                        "YAML frontmatter fields: name, description, and optionally "
                        "project, role, siblings."
                    ),
                },
                "overview": {
                    "type": "string",
                    "description": "The always-loaded ## Overview body (keep it to a few lines).",
                },
                "sections": {
                    "type": "object",
                    "description": (
                        "Optional on-demand sections, as a map of section name to "
                        "markdown body (e.g. {'Build & test': '...'})."
                    ),
                },
            },
            "required": ["path", "frontmatter", "overview"],
        },
    ),
    ToolParam(
        name="find_relevant_projects",
        description=(
            "Route a request to a DIFFERENT project than the current repo. Given "
            "a free-text request, finds the most relevant repos by matching the "
            "global project index (every repo's HILITE.md name + description); "
            "blocks until the side loader agent resolves the ranked matches. Call "
            "this ONLY when the work clearly does NOT belong to the current repo. "
            "For work in the current repo, do NOT call this — carry it out here "
            "by driving the Claude CLI (open_claude_cli)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": "The user's request or task, in their own words.",
                },
            },
            "required": ["request"],
        },
    ),
    ToolParam(
        name="open_project_session",
        description=(
            "Open a new scoped session in ANOTHER project/repo to carry out a "
            "task, seeding it with context. Returns immediately (the app spawns the "
            "child session and posts a deeplink); it does not block for the result. "
            "Use ONLY after find_relevant_projects identifies a DIFFERENT repo — "
            "never to open a session in the current repo (do that work here by "
            "driving the Claude CLI)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "The project name (as in the index / HILITE.md frontmatter).",
                },
                "repo": {
                    "type": "string",
                    "description": "The repo path or identifier to root the new session in.",
                },
                "task": {
                    "type": "string",
                    "description": "The task for the new session to carry out.",
                },
                "context": {
                    "type": "string",
                    "description": "Optional extra context to seed the new session with.",
                },
            },
            "required": ["project", "repo", "task"],
        },
    ),
    ToolParam(
        name="report_to_parent",
        description=(
            "Report a summary of this session's outcome back to the session that "
            "opened it. Returns immediately. A no-op if this session has no parent, "
            "so it is always safe to call when you finish a delegated task."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "A concise summary of what was accomplished, for the parent session.",
                },
            },
            "required": ["summary"],
        },
    ),
]


def _terminal_result(val: dict) -> str:
    """Turn a ``terminal_*`` ``ui_result.value`` into a string the model reads."""
    if isinstance(val, dict) and "error" in val:
        return f"Error: {val['error']}"
    val = val or {}
    note = "" if val.get("settled", True) else \
        " (still updating -- may be mid-render; snapshot again if needed)"
    cur = f"cursor row {val.get('cursorRow')}, col {val.get('cursorCol')}"
    return f"Terminal screen{note}:\n{val.get('text', '')}\n[{cur}]"


def _bell_result(val: Any) -> str:
    """Turn a ``terminal_wait_for_bell`` ``ui_result.value`` into a string.

    Leads with *why* the wait ended (bell / timeout / process_exit) and how long
    it took, then folds in the settled screen so the model can act in one step
    (doc 15 §4a). When the bell rang more than once, or the wait timed out, it
    nudges the model to re-snapshot rather than trust the inlined frame.
    """
    if isinstance(val, dict) and "error" in val:
        return f"Error: {val['error']}"
    val = val if isinstance(val, dict) else {}

    reason = val.get("reason", "bell")
    waited = val.get("waitedMs")
    waited_s = f" after {round(waited / 1000)}s" if isinstance(waited, (int, float)) else ""
    count = val.get("bellCount")

    if reason == "permission_prompt":
        head = (f"Claude needs your approval{waited_s} — it is showing a permission "
                "prompt and is blocked until you answer. Read the screen below and "
                "respond with terminal_send_key (e.g. arrow keys + enter, or 'enter' "
                "to accept the default).")
    elif reason in ("stop", "idle_prompt"):
        head = f"Claude finished its turn{waited_s} and is waiting at the input prompt."
    elif reason == "bell":
        head = f"Claude rang the terminal bell{waited_s} — it has finished and is waiting."
    elif reason == "process_exit":
        head = (f"The Claude CLI process exited{waited_s} before signalling. "
                "The terminal is gone.")
    else:  # timeout
        head = (f"No completion signal within the timeout{waited_s} (Claude may still "
                "be working). Judge the screen below; if it shows the idle input "
                "prompt '>' then Claude is already done (don't wait again — you may "
                "have waited before submitting, or for startup, which fires no "
                "signal). Otherwise snapshot again or wait once more.")

    if reason in ("bell", "stop", "idle_prompt") and isinstance(count, int) and count > 1:
        head += (f" ({count} signals arrived while the screen settled; the frame below "
                 "may be stale — snapshot again if it doesn't look final.)")

    return f"{head}\n\n{_terminal_result(val)}"

def _bell_nudge_prompt(reason: str) -> str:
    """The synthesized prompt for an unsolicited terminal bell (doc 15 §4b). Framed
    as a system nudge: glance at the terminal and help only if it's relevant —
    explicitly OK to do nothing, so an incidental signal doesn't force busywork."""
    return (
        f"(System notice: the Claude CLI in the terminal signalled '{reason}' while "
        "you were idle — most likely the user ran something there by hand and it just "
        "finished. Take a quick look with terminal_snapshot. If it's relevant to "
        "helping the user — e.g. summarize the result, flag an error, or offer a next "
        "step — do so briefly. If it isn't something you should act on, just say so in "
        "one line and stop. Do NOT answer prompts the user is clearly driving "
        "themselves.)"
    )


def _workspace_result(val: dict) -> str:
    """Render a worker's result contract (doc 13 §4.3) as text the model reads."""
    if isinstance(val, dict) and "error" in val:
        return f"Error: {val['error']}"
    val = val or {}
    lines = [
        f"Workspace: {val.get('workspace', '?')}",
        f"Summary: {val.get('summary', '')}",
    ]
    files = val.get("filesChanged") or []
    if files:
        lines.append("Files changed:\n" + "\n".join(f"  - {f}" for f in files))
    if val.get("buildStatus"):
        lines.append(f"Build: {val['buildStatus']}")
    if val.get("testStatus"):
        lines.append(f"Tests: {val['testStatus']}")
    if val.get("contract"):
        lines.append(f"Contract: {val['contract']}")
    follow = val.get("followUps") or []
    if follow:
        lines.append("Follow-ups:\n" + "\n".join(f"  - {x}" for x in follow))
    return "\n".join(lines)


def _dispatch_result(val: dict) -> str:
    """Render the immediate handle a dispatch returns (doc 13 §4)."""
    if isinstance(val, dict) and "error" in val:
        return f"Error: {val['error']}"
    val = val or {}
    return (
        f"Dispatched to {val.get('workspace', '?')} "
        f"(handle {val.get('handle', '?')}, status {val.get('status', 'running')}). "
        "It runs in the background; its result will arrive as a later message, "
        "or call await_workspaces to block on it."
    )


def _await_result(val: dict) -> str:
    """Render the list of contracts an await_workspaces barrier returns."""
    if isinstance(val, dict) and "error" in val:
        return f"Error: {val['error']}"
    results = (val or {}).get("results", []) if isinstance(val, dict) else (val or [])
    if not results:
        return "No worker results to report."
    return "\n\n".join(_workspace_result(r) for r in results)


def _ack_result(val: Any, ok_msg: str) -> str:
    """Render an acknowledgement-style ui_result the app resolves (doc 14.1).

    Passes an ``error`` through, honors an explicit ``message`` or a plain
    string, and otherwise falls back to ``ok_msg`` for fire-and-acknowledge
    tools (``write_hilite_md`` / ``open_project_session`` / ``report_to_parent``).
    """
    if isinstance(val, dict) and "error" in val:
        return f"Error: {val['error']}"
    if isinstance(val, str) and val:
        return val
    if isinstance(val, dict) and val.get("message"):
        return val["message"]
    return ok_msg


def _projects_result(val: Any) -> str:
    """Render the ranked matches find_relevant_projects resolves with."""
    if isinstance(val, dict) and "error" in val:
        return f"Error: {val['error']}"
    matches = (val or {}).get("matches") if isinstance(val, dict) else val
    matches = matches or []
    if not matches:
        return "No matching projects found."
    lines = ["Relevant projects:"]
    for m in matches:
        if not isinstance(m, dict):
            lines.append(f"  - {m}")
            continue
        name = m.get("name") or m.get("project") or "?"
        head = f"  - {name}"
        if m.get("role"):
            head += f" ({m['role']})"
        if m.get("repo"):
            head += f" — {m['repo']}"
        lines.append(head)
        if m.get("description"):
            lines.append(f"      {m['description']}")
    return "\n".join(lines)


# --- On-demand tool catalog --------------------------------------------------

class CatalogEntry:
    """A registerable on-demand tool: its schema plus a handler factory.

    ``make_handler(interactor, ui)`` binds the handler to the session's
    ``Interactor`` (interactive tools) or ``UIEmitter`` (frontend tools).
    """

    def __init__(
        self,
        schema: ToolParam,
        make_handler: Callable[[Interactor, UIEmitter], Callable[..., Any]],
    ):
        self.schema = schema
        self.make_handler = make_handler


def _schema_by_name(schemas: list[ToolParam], name: str) -> ToolParam:
    return next(s for s in schemas if s["name"] == name)


TOOL_CATALOG: dict[str, CatalogEntry] = {
    "ask_user": CatalogEntry(
        _schema_by_name(INTERACTIVE_TOOL_SCHEMAS, "ask_user"),
        lambda interactor, ui: (
            lambda header, question, options: interactor.ask(
                header, question, options
            )
        ),
    ),
    "exit_plan_mode": CatalogEntry(
        _schema_by_name(INTERACTIVE_TOOL_SCHEMAS, "exit_plan_mode"),
        lambda interactor, ui: (lambda plan: interactor.review_plan(plan)),
    ),
    "open_web": CatalogEntry(
        _schema_by_name(FRONTEND_TOOL_SCHEMAS, "open_web"),
        lambda interactor, ui: (
            lambda url, title=None: ui.action("open_web", url=url, title=title)
        ),
    ),
    "open_claude_cli": CatalogEntry(
        _schema_by_name(FRONTEND_TOOL_SCHEMAS, "open_claude_cli"),
        lambda interactor, ui: (
            lambda cwd=None, title=None: ui.action(
                "open_claude_cli", cwd=cwd, title=title
            )
        ),
    ),
    "open_markdown": CatalogEntry(
        _schema_by_name(FRONTEND_TOOL_SCHEMAS, "open_markdown"),
        lambda interactor, ui: (
            lambda title, text: ui.action("open_markdown", title=title, text=text)
        ),
    ),
    "close_artifacts": CatalogEntry(
        _schema_by_name(FRONTEND_TOOL_SCHEMAS, "close_artifacts"),
        lambda interactor, ui: (lambda: ui.action("close_artifacts")),
    ),
    "focus_artifact": CatalogEntry(
        _schema_by_name(FRONTEND_TOOL_SCHEMAS, "focus_artifact"),
        lambda interactor, ui: (lambda id: ui.action("focus_artifact", id=id)),
    ),
    "terminal_snapshot": CatalogEntry(
        _schema_by_name(FRONTEND_TOOL_SCHEMAS, "terminal_snapshot"),
        lambda interactor, ui: (
            lambda terminal_id=None: _terminal_result(
                interactor.ui_request("terminal_snapshot", terminal_id=terminal_id)
            )
        ),
    ),
    "terminal_send_text": CatalogEntry(
        _schema_by_name(FRONTEND_TOOL_SCHEMAS, "terminal_send_text"),
        lambda interactor, ui: (
            lambda text, submit=True, terminal_id=None: _terminal_result(
                interactor.ui_request(
                    "terminal_send_text",
                    text=text, submit=submit, terminal_id=terminal_id,
                )
            )
        ),
    ),
    "terminal_send_key": CatalogEntry(
        _schema_by_name(FRONTEND_TOOL_SCHEMAS, "terminal_send_key"),
        lambda interactor, ui: (
            lambda key, count=1, terminal_id=None: _terminal_result(
                interactor.ui_request(
                    "terminal_send_key",
                    key=key, count=count, terminal_id=terminal_id,
                )
            )
        ),
    ),
    "terminal_wait_for_bell": CatalogEntry(
        _schema_by_name(FRONTEND_TOOL_SCHEMAS, "terminal_wait_for_bell"),
        lambda interactor, ui: (
            lambda terminal_id=None, timeout_ms=600_000, quiet_ms=400: _bell_result(
                interactor.ui_request(
                    "terminal_wait_for_bell",
                    terminal_id=terminal_id,
                    timeout_ms=timeout_ms,
                    quiet_ms=quiet_ms,
                )
            )
        ),
    ),
    "run_in_workspace": CatalogEntry(
        _schema_by_name(FRONTEND_TOOL_SCHEMAS, "run_in_workspace"),
        lambda interactor, ui: (
            lambda workspace, task: _workspace_result(
                interactor.ui_request("run_in_workspace", workspace=workspace, task=task)
            )
        ),
    ),
    "dispatch_to_workspace": CatalogEntry(
        _schema_by_name(FRONTEND_TOOL_SCHEMAS, "dispatch_to_workspace"),
        lambda interactor, ui: (
            lambda workspace, task: _dispatch_result(
                interactor.ui_request("dispatch_to_workspace", workspace=workspace, task=task)
            )
        ),
    ),
    "await_workspaces": CatalogEntry(
        _schema_by_name(FRONTEND_TOOL_SCHEMAS, "await_workspaces"),
        lambda interactor, ui: (
            lambda handles: _await_result(
                interactor.ui_request("await_workspaces", handles=handles)
            )
        ),
    ),
    "write_hilite_md": CatalogEntry(
        _schema_by_name(FRONTEND_TOOL_SCHEMAS, "write_hilite_md"),
        lambda interactor, ui: (
            lambda path, frontmatter, overview, sections=None: _ack_result(
                interactor.ui_request(
                    "write_hilite_md",
                    path=path,
                    frontmatter=frontmatter,
                    overview=overview,
                    sections=sections,
                ),
                f"Wrote HILITE.md for {path}.",
            )
        ),
    ),
    "find_relevant_projects": CatalogEntry(
        _schema_by_name(FRONTEND_TOOL_SCHEMAS, "find_relevant_projects"),
        lambda interactor, ui: (
            lambda request: _projects_result(
                interactor.ui_request("find_relevant_projects", request=request)
            )
        ),
    ),
    "open_project_session": CatalogEntry(
        _schema_by_name(FRONTEND_TOOL_SCHEMAS, "open_project_session"),
        lambda interactor, ui: (
            lambda project, repo, task, context=None: _ack_result(
                interactor.ui_request(
                    "open_project_session",
                    project=project,
                    repo=repo,
                    task=task,
                    context=context,
                ),
                f"Opened a session in {project} ({repo}).",
            )
        ),
    ),
    "report_to_parent": CatalogEntry(
        _schema_by_name(FRONTEND_TOOL_SCHEMAS, "report_to_parent"),
        lambda interactor, ui: (
            lambda summary: _ack_result(
                interactor.ui_request("report_to_parent", summary=summary),
                "Reported to parent session.",
            )
        ),
    ),
}

# Back-compat default when ``start`` omits ``tools``.
DEFAULT_SESSION_TOOLS = ["ask_user", "exit_plan_mode"]


def register_session_tools(
    registry: ToolRegistry,
    interactor: Interactor,
    ui: UIEmitter,
    allow: list[str] | None = None,
) -> None:
    """Register the requested on-demand tools, on top of the always-on built-ins.

    ``allow is None`` registers the interactive defaults (``ask_user`` /
    ``exit_plan_mode``) for back-compat. Otherwise only the named catalog tools
    are registered. Unknown names warn on stderr and are skipped. Built-ins are
    never affected — injection is additive.

    Called only inside ``run_server()`` so the one-shot path never sees these
    tools and the model can't block on a question in a non-interactive run.
    """
    if allow is None:
        allow = DEFAULT_SESSION_TOOLS
    schemas: list[ToolParam] = []
    handlers: dict[str, Callable[..., Any]] = {}
    for name in allow:
        entry = TOOL_CATALOG.get(name)
        if entry is None:
            print(f"[hilite] Warning: unknown on-demand tool '{name}'", file=sys.stderr)
            continue
        schemas.append(entry.schema)
        handlers[name] = entry.make_handler(interactor, ui)
    registry.register_mcp_tools(schemas, handlers)   # existing dynamic path


def run_server(
    default_model: str | None = None,
    default_session: str | None = None,
    default_skill: str | None = None,
) -> None:
    """Run the ndjson streaming/interactive backend over stdin/stdout."""
    lock = threading.Lock()

    def emit(event: dict) -> None:
        with lock:
            sys.stdout.write(json.dumps(event) + "\n")
            sys.stdout.flush()

    counter = itertools.count(1)
    next_id = lambda prefix: f"{prefix}_{next(counter)}"  # noqa: E731
    interactor = Interactor(emit, next_id)
    ui = UIEmitter(emit)
    commands: queue.Queue = queue.Queue()

    def reader() -> None:
        for line in sys.stdin:               # blocks; ends on EOF
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = msg.get("type")
            if t == "answer":
                interactor.resolve(msg["id"], msg["selectedIndex"])
            elif t == "plan_decision":
                interactor.resolve(msg["id"], msg)
            elif t == "ui_result":
                interactor.resolve(msg["id"], msg["value"])
            else:                            # start / send_prompt / stop
                commands.put(msg)
        commands.put({"type": "stop"})       # stdin closed

    threading.Thread(target=reader, daemon=True).start()

    agent: AIAgent | None = None
    emit({"type": "ready"})
    while True:
        cmd = commands.get()
        t = cmd.get("type")
        if t == "stop":
            break
        show_prompt = True                   # whether to echo `prompt` as a user message
        if t == "start":
            os.chdir(cmd["cwd"])
            agent = AIAgent(
                model=cmd.get("model") or default_model or DEFAULT_MODEL,
                session_id=cmd.get("session") or default_session,
                project_root=Path(cmd["cwd"]),
                preload_skill=cmd.get("skill") or default_skill,
                event_sink=emit,
            )
            register_session_tools(agent.tools, interactor, ui, allow=cmd.get("tools"))
            prompt = cmd["prompt"]
        elif t == "send_prompt":
            if agent is None:                # send before start -> ignore
                continue
            prompt = cmd["text"]
        elif t == "terminal_bell":
            # Unsolicited terminal attention (doc 15 §4b): a Claude CLI tab signalled
            # while the agent was idle — likely the user ran something there by hand.
            # Single-threaded loop ⇒ if a turn is in flight this was queued and only
            # runs now, i.e. *between* turns (doc 13 §4.4). Nudge the agent to glance
            # at the terminal. Skip permission_prompt: that's the user mid-command, not
            # ours to answer.
            if agent is None:
                continue
            reason = cmd.get("reason", "bell")
            if reason == "permission_prompt":
                continue
            prompt = _bell_nudge_prompt(reason)
            show_prompt = False              # system nudge, not a real user message
        else:
            continue

        if show_prompt:
            emit({"type": "user_prompt", "text": prompt})
        try:
            agent.run_conversation(prompt)   # streams via event_sink
            emit({"type": "stop"})
        except Exception as e:               # keep session alive
            emit({"type": "session_error", "error": str(e)})

    if agent is not None:
        agent.tools.shutdown()
    emit({"type": "session_complete", "result": {"exitCode": 0}})
