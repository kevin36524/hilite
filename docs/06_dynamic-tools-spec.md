# HiLite Dynamic / On-Demand Tools Spec (per-session tool injection + UI tools)

**Status:** Draft
**Date:** 2026-06-03
**Scope:** Let a serve-mode front-end inject a per-session set of **on-demand
tools** at `start`, on top of HiLite's always-on built-ins. Adds a catalog of
**frontend (UI) tools** that drive the front-end's UI by emitting a new
`ui_action` event (mirroring how `ask_user` emits `ask_user`).
**Philosophy:** Reuse the serve-mode machinery verbatim — the `Interactor` +
`register_mcp_tools` + `emit()` pattern in `serve.py` is the template. The
one-shot `hilite -p` path stays byte-for-byte unchanged.
**Builds on:** `05_serve-mode-spec.md` (the ndjson serve protocol + `Interactor`)
and `03_mcp-and-skills-spec.md` (dynamic tool registration via
`ToolRegistry.register_mcp_tools`).

The consumer is the YahooCoworker app; its side is specified in
`YahooCoworker/doc_folder/11_onboarding_experience_plan.md` (§5) and
`11.1_phase1_app_spec.md`.

---

## 1. Why

YahooCoworker drives HiLite as a **per-step** backend: each onboarding step
spawns a fresh `hilite --serve` and wants the model to have exactly the tools
that step needs — no more. Two needs follow:

1. **Per-session tool injection.** Beyond the always-on built-ins (`read_file`,
   `write_file`, `list_directory`, `execute_command`, `execute_python`, memory,
   skills, MCP), a session should be able to request a specific set of optional
   tools at `start`. A "show the login page" step wants `open_web`; a "log into
   Claude" step wants `open_claude_cli` + `ask_user`; a pure-shell step wants
   none of them.

2. **Frontend (UI) tools.** Some of those tools have no Python-side effect — they
   exist to drive the front-end's UI (open a webview, open a terminal, render
   markdown, close panels). HiLite tool handlers run in-process and can't touch
   the GUI, so a UI tool must **emit an event the front-end consumes** — exactly
   how `ask_user` already emits an `ask_user` event and blocks on a reply.

Today `serve.py` hard-registers `ask_user` + `exit_plan_mode` for every session
(`register_interactive_tools`). This spec generalizes that into an **on-demand
catalog** selected per `start`.

**Non-goals:** changing built-in tools, the agent loop, memory, or the one-shot
path; per-tool auth; tool *removal* of built-ins (injection is additive).

---

## 2. Protocol delta (additions to `05_serve-mode-spec.md`)

### 2.1 `start` gains an optional `tools` allowlist

| `type`  | New field | Meaning |
|---------|-----------|---------|
| `start` | `tools?: string[]` | Names of **on-demand catalog tools** to register for this session, on top of the always-on built-ins. |

Resolution:
- **`tools` omitted** → back-compat: register the interactive defaults
  (`ask_user`, `exit_plan_mode`) exactly as today.
- **`tools` present** → register **only** the named catalog tools (still on top
  of built-ins). `["open_web"]` grants `open_web` and nothing interactive.
- Unknown names are ignored with a stderr warning.
- The list never affects built-ins — `execute_command` et al. are always present.

```jsonc
// app → HiLite
{"type":"start","cwd":"…","prompt":"…","skill":"onboarding-mcp-setup",
 "tools":["open_claude_cli","open_web","ask_user"]}
```

### 2.2 New event: `ui_action` (HiLite → front-end)

A frontend tool emits one `ui_action` and (for fire-and-forget tools) returns
immediately.

| Field | Type | Notes |
|-------|------|-------|
| `type` | `"ui_action"` | constant |
| `action` | string | the tool's action name (`open_web`, `open_claude_cli`, …) |
| `args` | object | action-specific payload |
| `id` | string | **present only for blocking actions** (§4.2); mints `ui_N` |

### 2.3 New command: `ui_result` (front-end → HiLite)

Resolves a **blocking** `ui_action`. Routed by the reader thread to the
`Interactor` (identical to `answer` / `plan_decision`).

| Field | Type |
|-------|------|
| `type` | `"ui_result"` |
| `id` | string (echoes the `ui_action.id`) |
| `value` | object (action-specific result handed back to the tool) |

Fire-and-forget actions never emit an `id` and need no `ui_result`.

---

## 3. The on-demand tool catalog

A single module-level catalog maps tool name → (`ToolParam` schema, handler
factory). Built-ins are unaffected. v1 catalog:

| Name | Kind | `ui_action.action` | Blocking? | `args` |
|------|------|--------------------|-----------|--------|
| `ask_user` | interactive | *(uses existing `ask_user` event)* | yes | — |
| `exit_plan_mode` | interactive | *(uses existing `review_plan` event)* | yes | — |
| `open_web` | frontend | `open_web` | no | `{url, title?}` |
| `open_claude_cli` | frontend | `open_claude_cli` | no | `{cwd?, title?}` |
| `open_markdown` | frontend | `open_markdown` | no | `{title, text}` |
| `close_artifacts` | frontend | `close_artifacts` | no | `{}` |
| `focus_artifact` | frontend | `focus_artifact` | no | `{id}` |

Frontend-tool schemas steer the model to call them at the right beat (e.g.
`open_web`: *"Open a web page in the user's artifact panel. Use when you want the
user to see or interact with a page (a login form, docs)."*). New tools are added
by appending to the catalog — that's the whole extension story.

---

## 4. Implementation

All changes are in `hilite/serve.py` plus a tiny addition the existing
`Interactor`. No `agent.py` / `registry.py` changes (registration already flows
through `register_mcp_tools`).

### 4.1 Fire-and-forget frontend tools

A `UIEmitter` wraps the locked `emit()` and exposes one call per action; each
emits a `ui_action` and returns a short confirmation string the model sees:

```python
class UIEmitter:
    def __init__(self, emit):  # the locked stdout emit()
        self._emit = emit

    def action(self, name: str, **args) -> str:
        self._emit({"type": "ui_action", "action": name, "args": args})
        return f"Done: {name}"
```

Handlers bind to it:

```python
"open_web":        lambda url, title=None: ui.action("open_web", url=url, title=title),
"open_claude_cli": lambda cwd=None, title=None: ui.action("open_claude_cli", cwd=cwd, title=title),
"open_markdown":   lambda title, text: ui.action("open_markdown", title=title, text=text),
"close_artifacts": lambda: ui.action("close_artifacts"),
"focus_artifact":  lambda id: ui.action("focus_artifact", id=id),
```

### 4.2 Blocking frontend tools (optional, when an action must await the UI)

Reuse the `Interactor` pattern: emit with an `id`, block on a `threading.Event`,
resolve via `ui_result`. Add to `Interactor`:

```python
def ui_request(self, action: str, **args):
    rid = self._next_id("ui")
    ev = threading.Event()
    with self._lock:
        self._pending[rid] = {"event": ev, "value": None}
    self._emit({"type": "ui_action", "action": action, "id": rid, "args": args})
    ev.wait()
    return self._pending.pop(rid)["value"]   # the ui_result.value
```

and route it in the reader thread alongside `answer` / `plan_decision`:

```python
elif t == "ui_result":
    interactor.resolve(msg["id"], msg["value"])
```

v1 ships the fire-and-forget tools; blocking is specified here so the wire format
is stable, but no v1 catalog tool needs it.

### 4.3 Selecting the per-session set

Replace `register_interactive_tools` with a catalog-driven
`register_session_tools` called from the `start` branch of `run_server`:

```python
def register_session_tools(registry, interactor, ui, allow=None):
    """Register the requested on-demand tools (default: interactive tools)."""
    if allow is None:
        allow = ["ask_user", "exit_plan_mode"]      # back-compat default
    schemas, handlers = [], {}
    for name in allow:
        entry = TOOL_CATALOG.get(name)
        if entry is None:
            print(f"[hilite] Warning: unknown on-demand tool '{name}'", file=sys.stderr)
            continue
        schemas.append(entry.schema)
        handlers[name] = entry.make_handler(interactor, ui)
    registry.register_mcp_tools(schemas, handlers)   # existing dynamic path
```

In `run_server`:

```python
ui = UIEmitter(emit)
...
if t == "start":
    os.chdir(cmd["cwd"])
    agent = AIAgent(..., event_sink=emit)
    register_session_tools(agent.tools, interactor, ui, allow=cmd.get("tools"))
    prompt = cmd["prompt"]
```

`TOOL_CATALOG` is `{name: CatalogEntry(schema, make_handler)}` where
`make_handler(interactor, ui)` returns the bound callable (interactive tools use
`interactor`; frontend tools use `ui`). `INTERACTIVE_TOOL_SCHEMAS` folds into it.

---

## 5. Files touched

| File | Change |
|------|--------|
| `hilite/serve.py` | Add `UIEmitter`, `FRONTEND_TOOL_SCHEMAS`, `TOOL_CATALOG`, `register_session_tools()`; wire `cmd.get("tools")` in the `start` branch; route `ui_result` in the reader; add `Interactor.ui_request` (for future blocking tools). Remove/retire `register_interactive_tools` (folded into the catalog). |
| `docs/05_serve-mode-spec.md` | Note the `tools` field on `start`, the `ui_action` event, and the `ui_result` command (cross-ref this doc). |
| `docs/README.md` | Index entry. |

No new dependencies. No change to the one-shot path, the agent loop, or built-in
tools.

---

## 6. Smoke test (no Swift required)

```bash
# inject only open_web; ask the model to open a page
printf '%s\n' \
  '{"type":"start","cwd":"/tmp","prompt":"Open https://example.com for me using your open_web tool.","tools":["open_web"]}' \
  '{"type":"stop"}' \
  | hilite --serve
```

Expected (interleaved): a `pre_tool_use` for `open_web`, a
`{"type":"ui_action","action":"open_web","args":{"url":"https://example.com",...}}`,
a `post_tool_use`, then `stop` / `session_complete`.

Negative check: with `"tools":["open_web"]`, the model must **not** be offered
`ask_user` (not in the allowlist); with `tools` omitted, `ask_user` /
`exit_plan_mode` are present (back-compat).

---

## 7. Acceptance criteria

1. `hilite -p "…"` and stdin one-shot behavior remain byte-for-byte unchanged
   (no on-demand tools registered, no events emitted).
2. `start.tools` registers exactly the named catalog tools on top of built-ins;
   omitted → the interactive defaults; unknown names warn on stderr and are
   skipped.
3. A frontend tool call emits a `ui_action` with the right `action`/`args` and
   returns a confirmation string to the model.
4. `ui_result` resolves a blocking `ui_action` by `id` (mechanism verified even
   though no v1 catalog tool blocks).
5. Built-in tools (`execute_command`, file, memory, skills, MCP) are always
   available regardless of `tools`.
