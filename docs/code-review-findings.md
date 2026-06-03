# HiLite Code Review — Findings

_Last reviewed: 2026-06-02 (against `main` @ `8c7bc24`)_

This document records issues found in a full review of the HiLite codebase. Each
item notes severity, location, the problem, and a suggested fix. Status tracks
whether the issue has been addressed.

Severity scale: **critical** (broken or exploitable out of the box) ·
**high** · **medium** · **low** (cleanup).

---

## Operational issues

These affect whether the agent works correctly and observably. **Targeted for the
current fix pass.**

### OP-1 — Invalid default model ID (critical) — _status: in progress_
**Where:** `constants.py:10`, `config.py:21`, `agent.py:55`, `learning_loop.py:115`
(and the `--model` help text in `hilite.py:26`).

`claude-sonnet-4-6-20250601` is not a real model string — Sonnet 4.6 is the bare
alias `claude-sonnet-4-6`. Every `messages.create` call would 404
(`not_found_error`) out of the box. The ID is hardcoded in 4–5 places.

**Fix:** centralize on `constants.DEFAULT_MODEL = "claude-sonnet-4-6"` and import
it everywhere instead of repeating the literal.

### OP-2 — `--system` override silently drops memory + project context (high) — _status: in progress_
**Where:** `agent.py:75-83`.

The comment claims the override "still gets memory blocks and project context
appended," but the branch assigns `system_prompt` verbatim. Passing `--system`
loses USER.md, MEMORY.md, project context, **and** the memory-management
instructions (which the memory tooling depends on).

**Fix:** route the override through `MemoryStore.build_system_prompt(...)` as an
identity override so memory/context/management blocks are still appended.

### OP-3 — No prompt caching (medium) — _status: in progress_
**Where:** `agent.py:317` (`_call_model`).

The large, session-frozen system prompt (SOUL + USER + MEMORY + project context +
skills index) and the stable tool list are re-billed at full price on every turn
of the tool loop. This is the canonical case for prompt caching.

**Fix:** send `system` as a content block with
`cache_control: {"type": "ephemeral"}` (caches the tools + system prefix). Verify
via `usage.cache_read_input_tokens`.

### OP-4 — No typed API error handling; `max_tokens` never checked (medium) — _status: in progress_
**Where:** `agent.py:317`, `hilite.py:103`.

`messages.create` has no try/except. Rate-limit / auth / overloaded errors
propagate to a bare `except Exception` in `hilite.py` and die with a generic
`Error: …`. `stop_reason == "max_tokens"` is never checked (fixed
`max_tokens=4096`), so truncated responses pass silently.

**Fix:** catch `anthropic.APIStatusError` / `APIConnectionError` and surface a
clear message (status, retry-after, credential/model hints); warn on
`stop_reason == "max_tokens"`.

### OP-5 — Learning-loop failures silently swallowed (medium) — _status: in progress_
**Where:** `learning_loop.py:148`.

`_nudge` wraps everything in `except Exception → return ""`. Callers treat `""`
as "nothing to do," so a bad model ID or auth error turns the entire learning
loop into a silent no-op with only a stderr line — and makes OP-1 undetectable in
normal use.

**Fix:** distinguish fatal config errors (401/404) from transient ones; disable
the loop for the session after a fatal error and report it once, clearly.

---

## Security issues (deferred)

The tool layer (`shell`, `python`, `file`) has no sandbox or confinement, so any
prompt-injection reaching the model can escalate to host actions. **Deferred — not
part of the current fix pass; tracked here for later.**

### SEC-1 — `execute_command`: arbitrary shell execution, no guardrails (critical)
**Where:** `tools/shell.py:11`.
`subprocess.run(command, shell=True, …)` with model-controlled input. No
allowlist, sandbox, cwd confinement, or confirmation. Combined with SEC-4/SEC-5,
any injection in a file the agent reads becomes host RCE.
**Fix:** gate behind per-call confirmation; run sandboxed / `shell=False` with an
allowlist; confine `cwd`; drop privileges.

### SEC-2 — `execute_python`: `exec()` in-process, no timeout (critical)
**Where:** `tools/shell.py:35-47`.
Model code runs `exec(code, namespace)` in the agent's own process: full builtins
(`import os`), reachable agent globals/API token in memory, no timeout
(`while True` hangs forever — `execute_command` has a 60s timeout, this doesn't),
no output cap.
**Fix:** run in a killable subprocess with a wall-clock timeout and bounded
captured output; sandbox for real isolation.

### SEC-3 — File tools: no root confinement / path traversal (high)
**Where:** `tools/file.py:8-41`.
`_resolve_path` resolves any absolute path with no base check, and `.resolve()`
follows symlinks. The model can read `~/.ssh/id_rsa` or overwrite shell configs
for persistence.
**Fix:** establish a configured root; reject paths where
`not resolved.is_relative_to(root)`; guard against symlink escape.

### SEC-4 — No size limits on file read/write (high)
**Where:** `tools/file.py:16-41`.
`f.read()` with no cap → OOM / token blowup on a large or pseudo-file
(`/dev/zero`); writes are unbounded.
**Fix:** cap read size with a truncation marker; reject non-regular files; bound
write length.

### SEC-5 — `load_skill` path traversal (high)
**Where:** `skills.py:166`.
`directory / f"{name}.md"` with no validation of model-controlled `name`;
`../`-prefixed names escape the skills dir. Constrained to `.md` files (the suffix
is appended), but still arbitrary `.md` read.
**Fix:** validate `name` with `_is_kebab_case` (already exists in `skills_auto.py`)
and assert the resolved path stays inside a discovered skills dir.

### SEC-6 — Auto-generated skill content written to disk unsanitized (high)
**Where:** `skills_auto.py:34-82`.
LLM-generated skill bodies (including from the autonomous loop) are `write_text`'d
verbatim, then re-injected into future system prompts — a self-poisoning /
injection-persistence vector. Unlike memory, they never pass through `_sanitize()`.
**Fix:** run skill `content` and indexed `description` through `_sanitize()` before
writing; bound length.

### SEC-7 — `memory_manage(action="read")` returns raw unsanitized content (medium)
**Where:** `tools/memory.py:25`, `memory.py:325`.
The system-prompt path sanitizes memory, but the tool read path bypasses
`_sanitize()`, re-exposing any stored injection to the model.
**Fix:** sanitize content returned by `read_memory_file`.

### SEC-8 — Non-atomic writes + no `session_id` path validation (medium)
**Where:** `state.py:21-41`, `memory.py:332`.
Crash mid-`json.dump` leaves corrupt JSON that `load_session`'s unguarded
`json.load` throws on; caller-supplied `session_id` with `../` escapes
`SESSIONS_DIR`.
**Fix:** write to temp file + `os.replace()`; guard `json.load`; validate
`session_id` against a safe pattern.

---

## MCP module (deferred — validate against installed `mcp` version)

High-impact but dependent on the exact SDK API; confirm before fixing.

### MCP-1 — anyio cross-task lifecycle bug (critical)
**Where:** `mcp/session.py:44-72`.
Transport `__aenter__`/`__aexit__` run in different asyncio tasks (separate
`_run_on_mcp_loop` calls) → `RuntimeError: cancel scope in a different task`. Clean
shutdown/reconnect won't work.
**Fix:** run the whole session lifecycle inside one long-lived task; dispatch
`list_tools`/`call_tool`/`close` via a queue.

### MCP-2 — `ClientSession` never entered as context manager (critical)
**Where:** `mcp/session.py:55`.
`initialize()` is called on an unstarted session; the receive loop never runs.
**Fix:** `async with ClientSession(...) as session:` within the same task as the
transport.

### MCP-3 — Wrong SDK symbol `streamable_http_client` (high)
**Where:** `mcp/transport.py:98,128`.
The SDK exports `streamablehttp_client`; HTTP MCP (the default transport)
`ImportError`s. The `http_client=` kwarg also doesn't match the SDK signature.
**Fix:** use `streamablehttp_client` and pass `headers`/`timeout`/
`httpx_client_factory`.

### MCP-4 — SSRF + credential leak (high)
**Where:** `mcp/transport.py:104-166`.
`cfg.url` is unvalidated, `follow_redirects=True`, and the `Authorization` header is
attached at client level → metadata-endpoint/internal-host reach plus token exfil
via redirect.
**Fix:** enforce https + host allowlist, block private/link-local ranges, set
`follow_redirects=False` (or strip auth on cross-origin redirect).

### MCP-5 — OAuth flow is not actually PKCE (high)
**Where:** `mcp/oauth.py:163-181,247-261`.
No `code_challenge`/`code_verifier`/`state` → CSRF / code-injection exposure; token
endpoint fetched via `urlopen` with no https enforcement; `client_secret` persisted
to disk. `redirect_uri` also mismatches (`localhost` vs `127.0.0.1`) and advertises
port `0`.
**Fix:** implement S256 PKCE + `state`; require https; don't persist `client_secret`;
bind the callback port first and build both URLs from it.

### MCP-6 — OAuth provider never wired into transport (high)
**Where:** `mcp/client.py` (`_build_transport_config`, `_http_transport`).
`oauth_provider` is dead code; HTTP servers needing OAuth get no auth header.
**Fix:** build the provider when OAuth config is present and pass it through.

---

## Low-severity cleanups (deferred)

- **Error detection by `"Error:"` string prefix** (`agent.py:229`) — brittle; silently
  disables skill-improvement if a tool reports errors differently.
- **Wrong Claude Code creds fallback path** (`anthropic.py:44-57`) — reads
  `~/.claude/settings.json`; real location is `~/.claude/.credentials.json`. Dead on
  non-macOS.
- **Empty assistant message persisted** (`agent.py:183-194`) — `content=[]` saved; can
  trigger a 400 on resume.
- **Config `setdefault` doesn't deep-merge** (`config.py:42`) — a partial `learning_loop`
  override drops sibling defaults.
- **`tool_result` content mis-sliced in compression** (`learning_loop.py:194`) —
  slices a list, not 200 chars; siblings at 255/307 wrap in `str()`.
- **`_sanitize` offset drift on overlapping matches** (`memory.py:156`).
- **`"NO_SKILL" in response.upper()` over-matches** (`learning_loop.py:266`); dead
  override branch (`skills.py:77`); narrow `JSONDecodeError`-only catch
  (`learning_loop.py:344`); registry doesn't validate args against `input_schema`
  before dispatch (`registry.py:251`).
