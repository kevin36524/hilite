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
            "Open an interactive Claude CLI terminal in the user's artifact "
            "panel. Use when a step needs the user to run or watch the Claude "
            "CLI (e.g. logging in)."
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
]


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
        else:
            continue

        emit({"type": "user_prompt", "text": prompt})
        try:
            agent.run_conversation(prompt)   # streams via event_sink
            emit({"type": "stop"})
        except Exception as e:               # keep session alive
            emit({"type": "session_error", "error": str(e)})

    if agent is not None:
        agent.tools.shutdown()
    emit({"type": "session_complete", "result": {"exitCode": 0}})
