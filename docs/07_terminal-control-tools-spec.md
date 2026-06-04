# HiLite Terminal-Control Tools Spec (drive a front-end terminal via blocking UI tools)

**Status:** Draft
**Date:** 2026-06-04
**Scope:** Add three **blocking frontend tools** to the on-demand catalog so a
serve-mode agent can drive an interactive terminal the front-end owns: read its
rendered screen, type text, and send named keys. The motivating use is letting the
agent authenticate a Claude Code MCP by driving `claude`'s `/mcp` TUI (arrow-key
menu navigation) instead of asking the user to do it by hand.
**Philosophy:** Reuse the serve-mode machinery **verbatim**. These are ordinary
catalog tools whose handlers call the **already-built** blocking
`Interactor.ui_request()` (doc 06 §4.2). No agent-loop, registry, or protocol
changes — this is additive on top of doc 06.
**Builds on:** `05_serve-mode-spec.md` (ndjson serve protocol, `Interactor`,
`ui_result`) and `06_dynamic-tools-spec.md` (`TOOL_CATALOG`,
`register_session_tools`, the `ui_action` event, `ui_request`/`ui_result`).

The consumer/front-end side is specified in the YahooCoworker repo at
`doc_folder/11.2_agent_driven_mcp_auth.md` (the terminal plumbing) and
`doc_folder/11.3_mcp_auth_architecture_options.md` (why the front-end drives the
TUI rather than HiLite).

---

## 1. Why

Doc 06 shipped the blocking `ui_request`/`ui_result` path "for stability" but noted
*"no v1 catalog tool blocks."* This spec adds the first ones.

A front-end (YahooCoworker) hosts an interactive `claude` session in its own
terminal widget. Today the agent is blind to it — it can only narrate "now type
`/mcp` and pick slack" and wait. We want the agent to **observe and act**: read the
screen, type `/mcp`, navigate the menu, confirm. Authentication (a browser OAuth
flow) and the screen rendering both live in the front-end; HiLite only needs a
thin **observe→act** loop, which the blocking `ui_request` already provides.

**Non-goals:**
- HiLite does **not** spawn or own a PTY. It does not parse ANSI or emulate a
  terminal. The front-end owns the terminal and returns *already-rendered* screen
  text. (The alternative — HiLite driving `claude` in its own PTY — is analysed
  and rejected for now in YahooCoworker `doc_folder/11.3`.)
- No streaming terminal feed (doc 05 §4 deliberately dropped `terminal`/`input`/
  `resize`). These tools are request/response via `ui_request`.
- No changes to built-ins, the agent loop, memory, or the one-shot `hilite -p`
  path.

---

## 2. Protocol (already defined by doc 06 — no new wire types)

These tools emit the existing **blocking** `ui_action` (with an `id`) and are
resolved by the existing `ui_result` command. Concretely, per call:

```jsonc
// HiLite → front-end  (blocking ui_action; id minted by Interactor)
{"type":"ui_action","action":"terminal_send_text","id":"ui_7",
 "args":{"text":"/mcp","submit":true}}

// front-end → HiLite  (resolves ui_7)
{"type":"ui_result","id":"ui_7",
 "value":{"text":"<rendered screen>","cursorRow":12,"cursorCol":4,
          "settled":true,"terminalId":"cli-1"}}
```

### 2.1 The three actions

| `action` | `args` | Front-end effect |
|----------|--------|------------------|
| `terminal_snapshot` | `{terminal_id?}` | Return the current rendered screen. Sends no input. |
| `terminal_send_text` | `{text, submit?=true, terminal_id?}` | Type `text`; if `submit`, append Enter. |
| `terminal_send_key` | `{key, count?=1, terminal_id?}` | Send a named key `count` times (§3.1). |

`terminal_id` is optional; omitted ⇒ the front-end targets its active terminal.

### 2.2 The `ui_result.value` shape (what handlers receive)

```jsonc
{ "text": "<rendered visible screen, rows joined by \n, right-trimmed>",
  "cursorRow": 12, "cursorCol": 4,
  "settled": true,           // false ⇒ front-end hit its settle timeout (still animating)
  "terminalId": "cli-1" }
```
Error case: `{ "error": "no active Claude CLI terminal" }` (e.g. no terminal open).

> **Contract note:** these tools depend on the front-end implementing the
> `terminal_*` actions and replying with `ui_result`. A front-end that doesn't
> would leave `ui_request` blocked forever — so only register them for sessions
> whose front-end supports them (it opts in via `start.tools`, §4). This matches
> how `open_web` already assumes the front-end can open a webview.

---

## 3. Implementation (all in `hilite/serve.py`)

Additive: append schemas, add one formatter, add three `TOOL_CATALOG` entries.

### 3.1 Schemas → append to `FRONTEND_TOOL_SCHEMAS`

Descriptions steer the model to the observe→act loop; `terminal_send_key`
enumerates the valid keys so the model can't invent unsupported ones.

```python
ToolParam(
    name="terminal_snapshot",
    description=(
        "Read what is currently displayed on the interactive terminal so you can "
        "decide the next keystroke. Returns the rendered screen text and cursor "
        "position. Sends no input. Use before acting, and again after acting to "
        "see the result."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "terminal_id": {"type": "string",
                "description": "Which terminal (optional; defaults to the active one)."},
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
            "submit": {"type": "boolean",
                "description": "Press Enter after typing (default true)."},
            "terminal_id": {"type": "string",
                "description": "Which terminal (optional; defaults to the active one)."},
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
            "key": {"type": "string",
                "enum": ["up", "down", "left", "right", "enter",
                         "escape", "tab", "backspace", "space", "ctrl-c"],
                "description": "Which key to send."},
            "count": {"type": "integer",
                "description": "How many times to send it (default 1)."},
            "terminal_id": {"type": "string",
                "description": "Which terminal (optional; defaults to the active one)."},
        },
        "required": ["key"],
    },
),
```

### 3.2 Result formatter (turn the `value` dict into a string the model reads)

`ui_request` returns the raw `ui_result.value`; the model needs a string.

```python
def _terminal_result(val: dict) -> str:
    if isinstance(val, dict) and "error" in val:
        return f"Error: {val['error']}"
    val = val or {}
    note = "" if val.get("settled", True) else \
        " (still updating — may be mid-render; snapshot again if needed)"
    cur = f"cursor row {val.get('cursorRow')}, col {val.get('cursorCol')}"
    return f"Terminal screen{note}:\n{val.get('text', '')}\n[{cur}]"
```

### 3.3 Catalog entries → add to `TOOL_CATALOG`

Handlers call the existing blocking `interactor.ui_request(...)` (mirrors the
`ask_user`/`exit_plan_mode` entries, which use `interactor`):

```python
"terminal_snapshot": CatalogEntry(
    _schema_by_name(FRONTEND_TOOL_SCHEMAS, "terminal_snapshot"),
    lambda interactor, ui: (
        lambda terminal_id=None: _terminal_result(
            interactor.ui_request("terminal_snapshot", terminal_id=terminal_id))
    ),
),
"terminal_send_text": CatalogEntry(
    _schema_by_name(FRONTEND_TOOL_SCHEMAS, "terminal_send_text"),
    lambda interactor, ui: (
        lambda text, submit=True, terminal_id=None: _terminal_result(
            interactor.ui_request("terminal_send_text",
                                  text=text, submit=submit, terminal_id=terminal_id))
    ),
),
"terminal_send_key": CatalogEntry(
    _schema_by_name(FRONTEND_TOOL_SCHEMAS, "terminal_send_key"),
    lambda interactor, ui: (
        lambda key, count=1, terminal_id=None: _terminal_result(
            interactor.ui_request("terminal_send_key",
                                  key=key, count=count, terminal_id=terminal_id))
    ),
),
```

`register_session_tools`, `DEFAULT_SESSION_TOOLS`, and the reader-thread
`ui_result` routing are **unchanged**. A session opts in by naming the tools in
`start.tools`.

> **`ui_request` arg-passing check (do before coding):** confirm
> `Interactor.ui_request(self, action, **args)` forwards `**args` into the emitted
> `ui_action`'s `args` object (doc 06 §4.2 shows exactly this). The handlers above
> rely on `terminal_id=None` etc. reaching the front-end inside `args`; the
> front-end drops `None`-valued keys (as `UIEmitter.action` already does) or
> tolerates them.

---

## 4. Selecting the tools per session

No new mechanism — a `start` lists them in its `tools` allowlist (doc 06 §2.1):

```jsonc
{"type":"start","cwd":"…","prompt":"…",
 "tools":["open_web","open_claude_cli",
          "terminal_snapshot","terminal_send_text","terminal_send_key","ask_user"]}
```

Granting `ask_user` alongside lets the agent fall back to "please finish `/mcp`
yourself" if it can't make progress (the front-end spec's §7 manual fallback).

---

## 5. Files touched

| File | Change |
|------|--------|
| `hilite/serve.py` | Append 3 schemas to `FRONTEND_TOOL_SCHEMAS`; add `_terminal_result`; add 3 `TOOL_CATALOG` entries. |
| `docs/06_dynamic-tools-spec.md` | One-line note: the v1 catalog now includes the blocking `terminal_*` tools (cross-ref this doc). |
| `docs/README.md` | Index entry. |

No new dependencies. No change to built-ins, the agent loop, `register_session_tools`, or the one-shot path.

---

## 6. Smoke test (no Swift required)

Drive the round-trip by hand: feed a `start` that requests `terminal_snapshot`,
then a `ui_result` resolving the blocking `ui_action`.

```bash
printf '%s\n' \
  '{"type":"start","cwd":"/tmp","prompt":"Snapshot the terminal and tell me what is on screen.","tools":["terminal_snapshot"]}' \
  '{"type":"ui_result","id":"ui_1","value":{"text":"Welcome to Claude Code","cursorRow":1,"cursorCol":0,"settled":true,"terminalId":"cli-1"}}' \
  '{"type":"stop"}' \
  | hilite --serve
```

Expected (interleaved): a `pre_tool_use` for `terminal_snapshot`, a blocking
`{"type":"ui_action","action":"terminal_snapshot","id":"ui_1","args":{}}`, then
(after the hand-fed `ui_result`) a `post_tool_use`, the model's text quoting
"Welcome to Claude Code", and `stop` / `session_complete`.

> Timing note: the `ui_result` must carry the **same `id`** the server minted
> (`ui_1` on a fresh process via `itertools.count(1)`). If you can't predict it,
> run interactively and echo the emitted `ui_action.id` back.

Negative check: with `tools` omitted, none of the `terminal_*` tools are offered
(only `ask_user`/`exit_plan_mode`), and `hilite -p "…"` registers no terminal
tools and emits nothing.

---

## 7. Acceptance criteria

1. `start.tools` containing the three names registers exactly those tools on top
   of built-ins; omitting them changes nothing (back-compat).
2. Each tool emits a **blocking** `ui_action` with the right `action`/`args` and an
   `id`, and blocks until a matching `ui_result` arrives.
3. The handler returns `_terminal_result(value)` — a readable screen string — as
   the tool result the model sees; an `{"error":…}` value becomes an `Error:`
   string (so `post_tool_use.ok=false`).
4. `terminal_send_key` rejects keys outside its enum at the schema layer.
5. `hilite -p` and sessions without these tools are byte-for-byte unaffected.
