# HiLite Serve Mode Spec (ndjson streaming + interactive backend)

**Status:** Draft
**Date:** 2026-06-03
**Scope:** A long-lived, streaming, interactive mode for HiLite so a GUI front-end
(the `YahooCoworker` SwiftUI app) can drive it as an agent backend.
**Philosophy:** Reuse HiLite's existing tool-calling loop verbatim. Add a thin
transport (`--serve`) and the minimum hooks to stream events and round-trip
interactive questions. `hilite -p` one-shot behavior must stay byte-for-byte
unchanged.

---

## 1. Why

`YahooCoworker` currently drives the real `claude` CLI through a Node sidecar +
a forked `toll-free-harness` over a PTY. That stack carries a lot of accidental
complexity (PTY spawn-helper `chmod`, ESM bundling, a forked dependency for
multi-turn, transcript-polling to recover assistant prose). The app only ever
consumed a small **newline-delimited JSON** protocol over stdin/stdout.

HiLite already *is* an agent with its own tool-calling loop. If HiLite speaks
that same ndjson protocol directly, the entire Node sidecar and the harness fork
disappear and the app spawns `hilite --serve` instead of `node harness-bridge.mjs`.

This spec defines that mode. The companion app-side migration spec lives in the
YahooCoworker repo at `doc_folder/9_hilite_backend_migration.md`.

**Non-goals:**
- A raw PTY / embedded-terminal stream (HiLite has no TUI; `terminal`/`input`/
  `resize` messages are dropped — see §4).
- Networking, multiple concurrent sessions per process, or a daemon. One process
  = one session, exactly like the sidecar it replaces.
- Changing the agent loop's semantics, tools, memory, or learning loop.

---

## 2. Process & concurrency model

One `hilite --serve` process hosts **one** live `AIAgent` for its lifetime.

```
stdin  (ndjson commands)  ─▶ reader thread ─▶ command Queue ─▶ main loop ─▶ AIAgent.run_conversation()
                                    │                                            │
                                    │ answer / plan_decision                     │ event_sink
                                    ▼                                            ▼
                              Interactor.resolve(id)                      emit() ─▶ stdout (ndjson events)
```

Two threads:

- **Main thread** runs the blocking agent loop. Between turns it blocks on a
  `queue.Queue` waiting for the next `start` / `send_prompt` / `stop`. During a
  turn, `AIAgent.run_conversation()` runs synchronously and emits events through
  an `event_sink` callback (§5). An interactive tool call blocks the main thread
  on a `threading.Event` until its answer arrives (§6).

- **Reader thread** (daemon) loops over `sys.stdin`, parses one ndjson command
  per line, and routes it:
  - `answer` / `plan_decision` → `Interactor.resolve(id, value)` (unblocks the
    waiting tool on the main thread).
  - `start` / `send_prompt` / `stop` → push onto the command `Queue`.
  - EOF on stdin (front-end died) → push a synthetic `stop`.

**stdout discipline:** stdout is reserved exclusively for ndjson events. Every
write goes through a single `emit()` guarded by a `threading.Lock`, terminated
with `\n`, and flushed. All diagnostics (learning-loop notices, warnings,
tracebacks) continue to go to **stderr** — the app logs stderr to the Xcode
console and never parses it. `run_conversation()` already returns its text rather
than printing it, so nothing in the loop pollutes stdout.

---

## 3. Wire protocol

Newline-delimited JSON, one object per line, each with a `type`. This is a
deliberate subset of the existing sidecar protocol so the Swift side changes as
little as possible.

### Commands (front-end → HiLite)

| `type`          | Fields                                          | Effect |
|-----------------|-------------------------------------------------|--------|
| `start`         | `cwd`, `prompt`, `session?`, `model?`, `skill?`, `tools?` | `chdir(cwd)`, build the `AIAgent`, run the first turn on `prompt`. |
| `send_prompt`   | `text`                                          | Run another turn on the same live agent (multi-turn). |
| `answer`        | `id`, `selectedIndex`                           | Resolve a pending `ask_user`. |
| `plan_decision` | `id`, `decision` (`approve`/`reject`), `feedback?` | Resolve a pending `review_plan`. |
| `ui_result`     | `id`, `value`                                   | Resolve a pending blocking `ui_action` (see `06_dynamic-tools-spec.md`). |
| `stop`          | —                                               | End the session, shut down tools/MCP, `session_complete`, exit. |

- `tools?: string[]` selects a per-session set of **on-demand tools** to
  register on top of the always-on built-ins. Omitted → the interactive defaults
  (`ask_user`, `exit_plan_mode`). See `06_dynamic-tools-spec.md`.

Notes:
- `cwd` sets both `os.chdir()` and `AIAgent(project_root=Path(cwd))` so skills,
  memory project-key, and relative tool paths resolve in the target repo.
- `session` continues a saved conversation (maps to `AIAgent(session_id=...)`).
- `model` / `skill` map to the existing `--model` / `--skill` semantics.
- `images` from the old protocol is dropped (HiLite tools are text-only today).

### Events (HiLite → front-end)

| `type`             | Fields                              | Emitted when |
|--------------------|-------------------------------------|--------------|
| `ready`            | —                                   | Server up, reader thread attached, before first turn. |
| `user_prompt`      | `text`                              | A turn starts (echo of the prompt being run). |
| `pre_tool_use`     | `tool`, `input`                     | About to dispatch a tool (`registry.execute`). |
| `post_tool_use`    | `tool`, `ok` (bool)                 | Tool returned. `ok=false` if the result string starts with `Error:`. |
| `assistant_start`  | —                                   | A new assistant **text block** began streaming. |
| `assistant_delta`  | `text`                              | A chunk of the open assistant text block — append to it. |
| `assistant_stop`   | —                                   | The open assistant text block is complete. |
| `assistant`        | `text`                              | **Non-streaming fallback.** A complete assistant text block, emitted only when streaming is unavailable. Serve mode streams (above) and does **not** emit this; a consumer that sees a bare `assistant` with no preceding `assistant_start` renders it as a finished bubble. |
| `ask_user`         | `id`, `header`, `text`, `options[]` | The `ask_user` tool was called; awaiting an `answer`. |
| `review_plan`      | `id`, `plan`                        | The `exit_plan_mode` tool was called; awaiting a `plan_decision`. |
| `ui_action`        | `action`, `args`, `id?`             | A frontend (UI) tool was called; drives the front-end's UI. `id` present only for blocking actions (awaiting a `ui_result`). See `06_dynamic-tools-spec.md`. |
| `stop`             | —                                   | The current turn finished (agent returned). |
| `session_complete` | `result` (`{exitCode}`)             | `stop` command handled / stdin EOF; process about to exit. |
| `session_error`    | `error`                             | An unhandled exception bubbled out of a turn. The session stays alive. |

`options[]` entries are `{label, description}`, matching the app's
`QuestionOption`.

**Dropped vs. the sidecar protocol:** `terminal`, `input`, `resize` (no PTY),
and the `bin`/`env`/`args`/`interactive` fields on `start` (no child `claude`).

---

## 4. CLI surface

Add a single flag in `hilite/cli.py`:

```
--serve    Run as an ndjson streaming/interactive backend over stdin/stdout.
```

When `--serve` is present, `cli.main()` delegates to `hilite.serve.run_server()`
and returns; **all other flags and the one-shot path are untouched**. `--model`,
`--session`, `--skill` are honored as defaults for the first `start` if it omits
them.

---

## 5. Event sink in the agent loop

Add one optional hook to `AIAgent` — no behavior change when it's absent.

`hilite/agent/agent.py`:

- `__init__(..., event_sink: Callable[[dict], None] | None = None)` →
  `self._event_sink = event_sink`.
- Helper:
  ```python
  def _emit(self, event: dict) -> None:
      if self._event_sink is not None:
          self._event_sink(event)
  ```

Emit points:

1. **Assistant prose (streamed in `_call_model`)** — when an `event_sink` is
   set, `_call_model` uses the SDK streaming API (`client.messages.stream`) and
   emits `assistant_start` on each text `content_block_start`, an
   `assistant_delta` (`{text}`) per `text_delta`, and `assistant_stop` on the
   block's `content_block_stop`. It returns `stream.get_final_message()` — the
   same `Message` shape `create()` produced — so the tool loop and message
   history are unchanged. `run_conversation()` therefore does **not** emit any
   monolithic `assistant` events; the streamed deltas already carry the text.
   A `tool_use`-only response (no text block) simply produces no
   `assistant_start/stop`. The one-shot `hilite -p` path (no sink) keeps using
   `create()` and emits nothing — preserving its byte-for-byte output.
2. **Before tool dispatch** (immediately before `result = self.tools.execute(...)`
   around `agent.py:226`):
   `self._emit({"type": "pre_tool_use", "tool": block.name, "input": block.input})`
3. **After tool dispatch:**
   `self._emit({"type": "post_tool_use", "tool": block.name,
   "ok": not (isinstance(result, str) and result.startswith("Error:"))})`

The `user_prompt`, `stop`, `ready`, and `session_*` events are emitted by the
server (§7), not the agent — the agent doesn't know about turns-as-messages.

---

## 6. Interactive tools (`ask_user`, `exit_plan_mode`)

HiLite has no interactive tools today. Add two, but register them **only in
serve mode** so `hilite -p` is unaffected and the model can't block on a question
in a non-interactive run.

### Interactor

A small object owned by the server, injected into the two tool handlers via
closures:

```python
class Interactor:
    def __init__(self, emit, next_id):
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

    def resolve(self, rid: str, value) -> None:        # called by reader thread
        with self._lock:
            p = self._pending.get(rid)
        if p:
            p["value"] = value
            p["event"].set()
```

### Tool schemas / handlers

Defined in `hilite/serve.py` and registered via
`registry.register_mcp_tools(schemas, handlers)` (the existing dynamic-registration
path) right after the agent is built:

- `ask_user(header, question, options)` → `interactor.ask(...)`. `options` is a
  list of `{label, description}`. Schema description should steer the model to
  use it only when it genuinely needs a user decision it can't make itself.
- `exit_plan_mode(plan)` → `interactor.review_plan(plan)`. Mirrors Claude Code's
  plan-approval gate: the model proposes a plan and blocks for approval.

Because these are registered only inside `run_server()`, `hilite -p` never sees
them.

---

## 7. The server loop (`hilite/serve.py`)

New module. Sketch:

```python
def run_server(default_model=None, default_session=None, default_skill=None):
    lock = threading.Lock()
    def emit(event):
        with lock:
            sys.stdout.write(json.dumps(event) + "\n")
            sys.stdout.flush()

    counter = itertools.count(1)
    next_id = lambda prefix: f"{prefix}_{next(counter)}"
    interactor = Interactor(emit, next_id)
    commands: queue.Queue = queue.Queue()

    def reader():
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
            else:                            # start / send_prompt / stop
                commands.put(msg)
        commands.put({"type": "stop"})       # stdin closed

    threading.Thread(target=reader, daemon=True).start()

    agent = None
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
            register_interactive_tools(agent.tools, interactor)
            prompt = cmd["prompt"]
        elif t == "send_prompt":
            if agent is None:                # send before start → ignore
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
```

Notes:
- The learning loop still runs at the end of each `run_conversation()` and prints
  to stderr — unchanged.
- Multi-turn is free: the same `agent` (and its `self.messages`) survives across
  `send_prompt`s. This also makes the forked-harness multi-turn workaround moot
  on the app side.

---

## 8. Files touched

| File | Change |
|------|--------|
| `hilite/cli.py` | Add `--serve`; when set, call `hilite.serve.run_server(...)` and return. |
| `hilite/agent/agent.py` | Add `event_sink` param + `_emit()`; stream assistant text in `_call_model` (`assistant_start`/`assistant_delta`/`assistant_stop`) and emit `pre_tool_use` / `post_tool_use` per §5. |
| `hilite/serve.py` | **New.** `run_server()`, `Interactor`, interactive tool schemas/handlers + `register_interactive_tools()`. |
| `docs/05_serve-mode-spec.md` | This document. |
| `docs/README.md` | Index entry. |

No new dependencies. No change to `pyproject.toml` (uses stdlib `threading`,
`queue`, `json`, `itertools`).

---

## 9. Smoke test (no Swift required)

```bash
# one-shot path must be unchanged
hilite -p "say hi"

# serve mode: pipe a couple of commands
printf '%s\n' \
  '{"type":"start","cwd":"/tmp","prompt":"list files in this directory with a tool"}' \
  '{"type":"send_prompt","text":"now read the first one"}' \
  '{"type":"stop"}' \
  | hilite --serve
```

Expected event stream (one JSON object per line, order may interleave tool
events with assistant prose):

```
{"type":"ready"}
{"type":"user_prompt","text":"list files ..."}
{"type":"pre_tool_use","tool":"execute_command","input":{"command":"ls"}}
{"type":"post_tool_use","tool":"execute_command","ok":true}
{"type":"assistant_start"}
{"type":"assistant_delta","text":"Here are "}
{"type":"assistant_delta","text":"the files ..."}
{"type":"assistant_stop"}
{"type":"stop"}
{"type":"user_prompt","text":"now read the first one"}
...
{"type":"stop"}
{"type":"session_complete","result":{"exitCode":0}}
```

Interactive check: have the model call `ask_user`, confirm an `ask_user` event is
emitted, then feed `{"type":"answer","id":"ask_1","selectedIndex":0}` and confirm
the turn resumes.

---

## 10. Acceptance criteria

1. `hilite -p "..."` and stdin one-shot behavior are byte-for-byte unchanged
   (no interactive tools registered, no events emitted).
2. `hilite --serve` emits the §3 event stream over stdout and only ndjson over
   stdout; all diagnostics stay on stderr.
3. Multi-turn works: N `send_prompt`s run N turns against one live agent with
   retained history.
4. `ask_user` / `exit_plan_mode` block the turn and resume on the matching
   `answer` / `plan_decision`.
5. stdin EOF and the `stop` command both shut down cleanly with a final
   `session_complete`.
