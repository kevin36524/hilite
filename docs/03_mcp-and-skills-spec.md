# HiLite MCP Client & Skills Spec

**Status:** Draft  
**Date:** 2026-06-01  
**Scope:** MCP client support + skills system for HiLite  
**Philosophy:** Borrow the *ideas* from Hermes, keep the *code* minimal.

---

## 1. Goals

1. **MCP Client**: Let HiLite connect to external MCP servers (e.g. Slack, filesystem, GitHub) and use their tools as if they were built-in.
2. **Skills**: Let users write reusable procedural knowledge (workflows, best practices) as markdown files that HiLite can load on demand.

**Non-goals:**
- Hermes as an MCP server (out of scope — HiLite is client-only)
- Sampling (server-initiated LLM calls)
- Tool Search progressive disclosure
- Skill bundles, conditional activation, platform gating
- Agent-managed skill CRUD (skills are hand-written, not agent-generated)
- Circuit breaker (can add later if needed)

---

## 2. MCP Client

### 2.1 Configuration

MCP servers are declared in `~/.hilite/config.yaml`:

#### Stdio transport (default)

```yaml
mcp_servers:
  slack:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-slack"]
    env:
      SLACK_BOT_TOKEN: "${SLACK_BOT_TOKEN}"   # resolves from os.environ
      SLACK_TEAM_ID: "${SLACK_TEAM_ID}"
    enabled: true
    timeout: 60          # per-tool-call timeout (default: 60)
```

#### HTTP / SSE transport

```yaml
mcp_servers:
  internal-api:
    url: "https://mcp.internal.company.com/mcp"
    transport: "streamable-http"   # or "sse" for SSE transport
    headers:
      X-Custom-Header: "value"
    timeout: 60
    connect_timeout: 30
```

#### With mTLS

```yaml
mcp_servers:
  secure-api:
    url: "https://mcp.secure.company.com/mcp"
    client_cert: "~/.certs/client.pem"      # combined cert+key, or:
    client_cert: ["~/.certs/cert.pem", "~/.certs/key.pem"]
    client_cert: ["~/.certs/cert.pem", "~/.certs/key.pem", "passphrase"]
    ssl_verify: true                        # default: true; false for self-signed
```

#### With OAuth 2.1 PKCE

```yaml
mcp_servers:
  google-drive:
    url: "https://mcp.googleapis.com/mcp"
    auth: oauth
    oauth:
      client_id: "pre-registered-id"         # optional: skip dynamic registration
      client_secret: "secret"                # optional: confidential clients only
      scope: "drive.readonly"                # optional: default = server-provided
      redirect_port: 0                       # 0 = auto-pick free port
      client_name: "HiLite"                  # default: "HiLite Agent"
```

**Config schema summary:**

| Key | Type | Required for | Description |
|-----|------|--------------|-------------|
| `command` | string | stdio | Executable to spawn |
| `args` | list[str] | stdio | Arguments for command |
| `env` | dict | either | Extra env vars (supports `${ENV}` interpolation) |
| `url` | string | HTTP/SSE | Server URL |
| `transport` | string | HTTP/SSE | `"streamable-http"` (default) or `"sse"` |
| `headers` | dict | HTTP/SSE | Extra HTTP headers |
| `timeout` | int | either | Per-tool-call timeout (default: 60s) |
| `connect_timeout` | int | either | Initial connection timeout (default: 30s) |
| `enabled` | bool | either | Whether to connect on startup (default: true) |
| `client_cert` | str/list | HTTP/SSE | mTLS client certificate |
| `client_key` | str | HTTP/SSE | mTLS private key (if not combined in `client_cert`) |
| `ssl_verify` | bool | HTTP/SSE | Verify TLS certs (default: true) |
| `auth` | string | HTTP/SSE | `"oauth"` for OAuth 2.1 PKCE |
| `oauth` | dict | OAuth | OAuth-specific options |

**Design decisions:**
- `${ENV_VAR}` interpolation from `os.environ` (like Hermes)
- `enabled: false` skips the server on startup
- No per-tool include/exclude in v1 (all tools from a server are registered)
- Transport is auto-detected: `command` present → stdio, `url` present → HTTP/SSE

### 2.1.1 Project-Level MCP Activation

MCP server **definitions** (with secrets) live globally in `~/.hilite/config.yaml`. But **which servers are active** can be controlled per-project via `.hilite/config.yaml` in the project root:

```yaml
# .hilite/config.yaml (in project repo — safe to commit, no secrets)
mcp:
  # Only these servers are active for this project
  enabled:
    - slack
    - github

  # OR disable specific servers while keeping others
  disabled:
    - filesystem
```

**Resolution rules:**

1. Read global `~/.hilite/config.yaml` → get all `mcp_servers` definitions
2. Read project `.hilite/config.yaml` → get `mcp.enabled` / `mcp.disabled`
3. Apply filters:
   - If `mcp.enabled` is present → only those servers are active
   - If `mcp.disabled` is present → those servers are excluded
   - If neither → all globally-enabled servers are active
4. A server with `enabled: false` in global config is always skipped

**Project-specific servers:** You can also define MCP servers directly in `.hilite/config.yaml` (e.g. a local dev server). These are merged with global definitions. If a name collides, the project-level definition wins:

```yaml
# .hilite/config.yaml
mcp_servers:
  local-llm:
    command: "python"
    args: ["./local_mcp_server.py"]
```

**Rationale:** Secrets stay in `~/.hilite/` (outside git). Activation lists live in the repo so the whole team shares the same tool context. This mirrors how Claude Code separates `.claude/settings.local.json` (secrets, local) from `.claude/settings.json` (shareable).

### 2.2 Tool Discovery & Registration

**When:** At `AIAgent` startup, before the first model call.

**Flow:**

```
AIAgent.__init__()
  └─> ToolRegistry.__init__()
        ├─> load built-in schemas (existing)
        └─> MCPClient.discover_all()
              ├─> read ~/.hilite/config.yaml
              ├─> for each enabled server:
              │     └─> connect via stdio (subprocess)
              │         ├─> send initialize handshake
              │         ├─> call tools/list
              │         └─> register each tool
              └─> store connections for reuse
```

**Tool naming:**

MCP tools are prefixed to avoid collisions:

```
Built-in:  read_file
MCP:       mcp_slack_post_message
           mcp_slack_get_channel_history
           mcp_filesystem_read_file
```

**Schema normalization:**

MCP tools may use draft-07 JSON Schema features not supported by Anthropic's API (e.g. `const`, `patternProperties`, complex `anyOf`). We normalize:

- Drop `const` → replace with `enum: [value]`
- Drop `patternProperties` → remove the key
- Flatten simple `anyOf`/`oneOf` → keep first non-null option
- Remove unsupported keywords: `$id`, `$schema`, `definitions`, `examples`, `if/then/else`
- Ensure every property has a `type`
- Prune `required` array of properties that were dropped

This is a best-effort transform. If a schema is still invalid, we log a warning and skip that tool.

### 2.3 Tool Call Dispatch

**Synchronous wrapper over async MCP client:**

```python
def _call_mcp_tool(server_name: str, tool_name: str, arguments: dict) -> str:
    """Synchronous entry point called by ToolRegistry.execute()."""
    # Schedule on the MCP event loop thread, block until done
    return _run_on_mcp_loop(_async_call_tool(server_name, tool_name, arguments))
```

**Dedicated event loop thread (like Hermes):**

A single background daemon thread (`"hilite-mcp-loop"`) runs one `asyncio` event loop for *all* MCP connections. This prevents:
- "Event loop is closed" errors on repeated tool calls
- Subprocess leaks (we keep handles for cleanup)
- Re-creating HTTP clients on every call (for future HTTP transport)

```python
def _ensure_mcp_loop() -> asyncio.AbstractEventLoop:
    """Start the background MCP event loop if not running."""
```

**Tool execution:**

```python
async def _async_call_tool(server_name, tool_name, arguments):
    session = _servers[server_name]          # get cached session
    result = await session.call_tool(tool_name, arguments=arguments)
    # result.content is list[TextContent | ImageContent]
    # For v1: join all TextContent, ignore ImageContent (log warning)
    return "\n".join(c.text for c in result.content if hasattr(c, "text"))
```

**Error handling:**

- Connection lost → attempt one reconnect → retry call → fail gracefully with error message
- Timeout → return `"Error: MCP tool call timed out after {timeout}s"`
- Server process died → return `"Error: MCP server '{name}' is not running"`
- No circuit breaker in v1 (Hermes has one; we can add if needed)

### 2.4 mTLS Support

For HTTP/SSE MCP servers that require client certificate authentication.

**Config parsing:**

```python
def _resolve_client_cert(server_name: str, config: dict):
    """Resolve client_cert / client_key config for httpx cert= parameter.

    Returns:
      - None: no cert configured
      - str: single PEM file path (combined cert + key)
      - (cert_path, key_path): separate cert and key files
      - (cert_path, key_path, password): with key passphrase
    """
```

**Path resolution:**
- Supports `~` expansion
- Validates files exist (raise `FileNotFoundError` with clear message)
- `client_cert` as string → single combined PEM
- `client_cert` as 2-element list → `(cert, key)` tuple
- `client_cert` as 3-element list → `(cert, key, password)` tuple
- `client_cert` + `client_key` as separate strings → `(cert, key)` tuple
- Error if `client_cert` is a list AND `client_key` is also set

**HTTP client injection:**

For StreamableHTTP transport, pass `cert=` and `verify=` directly to the MCP SDK's `httpx.AsyncClient`.

For SSE transport, the MCP SDK's `sse_client` doesn't expose `cert`/`verify` kwargs directly. We use an `httpx_client_factory` callback:

```python
def _make_http_client_factory(cert, verify):
    def factory(headers=None, timeout=None, auth=None):
        kwargs = {"follow_redirects": True, "verify": verify}
        if timeout:
            kwargs["timeout"] = timeout
        if headers:
            kwargs["headers"] = headers
        if auth:
            kwargs["auth"] = auth
        if cert:
            kwargs["cert"] = cert
        return httpx.AsyncClient(**kwargs)
    return factory
```

**`ssl_verify: false`** disables TLS certificate verification (useful for self-signed certs in internal environments).

### 2.5 OAuth 2.1 PKCE Support

For HTTP/SSE MCP servers that require OAuth authentication instead of static API keys.

**Architecture:**

```
MCPOAuthManager (singleton)
  ├─> Per-server provider cache
  ├─> Token storage on disk (~/.hilite/mcp-oauth/<server>.json)
  └─> Browser-based PKCE flow
```

**Flow:**

1. **Discovery**: MCP SDK discovers server's OAuth metadata (`.well-known/oauth-authorization-server`) and dynamic client registration endpoint
2. **Registration**: If no `client_id` in config, dynamically register a new OAuth client with the server
3. **Authorization**: Launch browser with PKCE authorization URL; user authenticates and approves
4. **Callback**: Ephemeral localhost HTTP server captures the authorization code
5. **Token exchange**: Exchange code + PKCE verifier for access token + refresh token
6. **Storage**: Persist tokens and client info to disk (`~/.hilite/mcp-oauth/<server>.json`)
7. **Refresh**: SDK auto-refreshes expired tokens using the refresh token

**Token storage (`~/.hilite/mcp-oauth/`):**

```json
{
  "access_token": "ya29...",
  "refresh_token": "1//...",
  "token_type": "Bearer",
  "expires_at": "2026-06-02T10:00:00Z",
  "client_info": {
    "client_id": "...",
    "client_secret": "..."
  }
}
```

Files are created with `0o600` permissions. Parent directory with `0o700`.

**Hermes features we adopt:**
- **Lazy-import**: MCP SDK's OAuth module is optional. If unavailable, OAuth servers fail gracefully with a clear error.
- **Browser launch**: Use Python's `webbrowser.open()` to launch the default browser.
- **Callback server**: `http.server.HTTPServer` on localhost, auto-picks a free port (or uses configured `redirect_port`).

**Hermes features we skip (v1):**
- Cross-process token reload via mtime watch (tokens survive process restarts via disk, but we don't watch for external changes)
- 401 thundering-herd deduplication (simpler: on 401, refresh and retry once)
- Step-up authorization (re-auth on scope changes)

**Non-interactive environments:**

If `stdin` is not a TTY (e.g. CI, script), the OAuth flow can't open a browser. In this case:
- If cached tokens exist on disk → use them (may fail with 401 if expired)
- If no cached tokens → fail with clear error: `"OAuth authentication required but running non-interactively. Please run HiLite interactively once to authenticate."`

**Auth recovery on 401:**

When an MCP tool call returns 401 (token expired or revoked):
1. Detect `McpError` with `Unauthorized` code, or `httpx.HTTPStatusError` with 401 status
2. Attempt token refresh via `OAuthClientProvider`
3. Retry the tool call once
4. If refresh fails → return error message suggesting re-authentication

### 2.6 Lifecycle

**Startup:**
- For stdio: spawn subprocess, send initialize handshake, call `tools/list`
- For HTTP/SSE: create `httpx.AsyncClient` (with mTLS if configured), send initialize, call `tools/list`
- For OAuth HTTP/SSE: set up OAuth provider (may trigger browser flow if no cached tokens)
- If a server fails to start, log the error but continue with others

**Shutdown:**
- For stdio: send shutdown notification, wait 2s, SIGTERM, SIGKILL after 5s
- For HTTP/SSE: close httpx client, close session
- `atexit` handler ensures cleanup even on unclean exit

**Dynamic reload:**
- Not in v1. User restarts HiLite to pick up config changes.
- Future: `--reload-mcp` flag.

### 2.7 Files

```
hilite/
└── mcp/
    ├── __init__.py
    ├── client.py      # MCPClient: discovery, connection, registration
    ├── transport.py   # StdioTransport + HTTP/SSE transport wrappers
    ├── session.py     # MCPSession: async call_tool wrapper
    ├── auth.py        # mTLS cert resolution
    └── oauth.py       # OAuth 2.1 PKCE: token storage, callback server, flow
```

**Dependencies:**

```toml
[project.dependencies]
"mcp>=1.0.0"            # Model Context Protocol SDK
```

The `mcp` SDK provides `ClientSession`, `StdioServerParameters`, `stdio_client`, etc.

---

## 3. Skills System

### 3.1 Skill Format

A skill is a single markdown file with YAML frontmatter:

```markdown
---
name: slack-latest
description: Get the latest messages from a Slack channel and summarize them.
tags: [slack, messaging, summary]
---

# Slack Latest Messages

Use this skill when the user asks about recent activity in a Slack channel.

## Steps

1. Find the channel using `mcp_slack_list_channels`
2. Get recent messages using `mcp_slack_get_channel_history` (limit: 20)
3. Summarize the key topics and action items
4. If the user asks for details on a specific thread, use `mcp_slack_get_thread_replies`

## Notes

- Default to #general if no channel is specified
- Ignore bot messages unless the user asks for them
```

**Required frontmatter:**
- `name` (kebab-case, unique) — used as the skill identifier
- `description` (1-2 sentences) — shown in the skills list

**Optional frontmatter:**
- `tags` (list[str]) — for organization
- `version` (str) — semantic version
- `author` (str)

**Body:** Free-form markdown. The agent reads it and follows the instructions.

### 3.2 Directory Layout

Skills live in two tiers — **global** (personal) and **project-level** (team, versioned):

```
~/.hilite/
└── skills/
    ├── slack-latest.md
    ├── pr-workflow.md
    ├── write-tests.md
    └── ...

my-project/
├── .hilite/
│   └── skills/
│       └── onboarding.md      # "How we set up this repo"
├── .agents/
│   └── skills/
│       └── api-conventions.md # cross-tool compatible
└── src/
    └── ...
```

**Search paths** (in precedence order — later paths override earlier ones):

1. `~/.hilite/skills/*.md` — global personal skills
2. `.hilite/skills/*.md` — project-level HiLite-specific skills
3. `.agents/skills/*.md` — generic cross-tool agent skills

Flat directories in v1. No subdirectories, no `references/`, no `templates/`.

**Why both `.hilite/skills/` and `.agents/skills/`?**

- `.hilite/skills/` — HiLite-specific conventions (e.g. "How to run our custom test harness")
- `.agents/skills/` — cross-tool compatible skills that work with any agent (Cursor, Cline, etc.)

If a skill with the same `name` exists in both global and project dirs, the **project-level version wins**.

### 3.3 Discovery

At `AIAgent` startup, scan all skill directories:

```python
def discover_skills(project_root: Path | None) -> list[Skill]:
    """Return all skills from global + project directories.

    Later directories override earlier ones for duplicate names.
    """
    dirs = [Path.home() / ".hilite" / "skills"]
    if project_root:
        dirs.extend([
            project_root / ".hilite" / "skills",
            project_root / ".agents" / "skills",
        ])
    # ... scan, dedupe by name, return
```

Parse frontmatter from each file. Skip files with invalid/missing frontmatter (log warning).

### 3.4 System Prompt Integration

Inject a compact skills index into the system prompt:

```
You have access to the following skills. Use them when relevant by calling
the skill_view tool with the skill name.

- slack-latest: Get the latest messages from a Slack channel and summarize them.
- pr-workflow: GitHub PR lifecycle: branch, commit, open, review, merge.
- write-tests: Write unit tests following the project's testing conventions.
```

This index is appended to the system prompt (after SOUL.md/USER.md/MEMORY.md).

**Token budget:** The index is typically <500 tokens even with 20 skills. If it grows large, we can truncate or skip it.

### 3.5 Invocation

**Method 1: Tool call (agent-initiated)**

Add a new built-in tool:

```python
skill_view = ToolParam(
    name="skill_view",
    description="Load a skill by name to get detailed instructions for a specific workflow.",
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Name of the skill to load (e.g., 'slack-latest')."
            }
        },
        "required": ["name"]
    }
)
```

The agent can call `skill_view(name="slack-latest")` when it decides the skill is relevant.

**Method 2: User slash command**

The CLI entry point can intercept `/skill-name` before sending to the model:

```python
if user_input.startswith("/"):
    skill_name = user_input[1:]
    # Prepend skill content to the next user message
    content = load_skill(skill_name)
    user_message = f"[Skill: {skill_name}]\n\n{content}\n\nNow, {user_input}"
```

**Method 3: Preload via CLI flag**

```bash
hilite --skill slack-latest "What did I miss in #engineering?"
```

### 3.6 Skill Tool Implementation

```python
def skill_view(name: str) -> str:
    """Load and return the content of a skill by name."""
    path = Path.home() / ".hilite" / "skills" / f"{name}.md"
    if not path.exists():
        return f"Error: Skill '{name}' not found."
    content = path.read_text()
    # Strip frontmatter, return just the body
    if content.startswith("---"):
        _, _, body = content.partition("---")
        _, _, body = body.partition("---")
        return body.strip()
    return content
```

### 3.7 Files

```
hilite/
└── skills.py      # Skill discovery, loading, system prompt integration
```

No new subpackage needed — ~80 lines should suffice.

---

## 4. Integration with Existing Code

### 4.1 ToolRegistry Changes

```python
# hilite/tools/registry.py

class ToolRegistry:
    def __init__(self):
        self._schemas = list(TOOL_SCHEMAS)          # built-in
        self._handlers = dict(TOOL_HANDLERS)        # built-in
        self._mcp_client = MCPClient(self)          # new
        self._mcp_client.discover_all()             # connect to servers, register tools

    def register_mcp_tools(self, server_name: str, tools: list[ToolParam], handlers: dict):
        """Called by MCPClient after discovering tools from a server."""
        self._schemas.extend(tools)
        self._handlers.update(handlers)
```

### 4.2 AIAgent Changes

```python
# hilite/agent/agent.py

class AIAgent:
    def __init__(self, ...):
        # ... existing init ...
        self.skills = SkillStore()                   # new
        skills_index = self.skills.build_index()
        if skills_index:
            self.system_prompt += f"\n\n{skills_index}"
```

### 4.3 Config Changes

`hilite/config.py` already loads `~/.hilite/config.yaml`. Extend it to read `mcp_servers:` key.

### 4.4 New Built-in Tool

Add `skill_view` to `TOOL_SCHEMAS` and `TOOL_HANDLERS` in `registry.py`.

---

## 5. User Flow: Slack Example

**Step 1:** User installs the Slack MCP server (one-time):

```bash
# The server is an npm package; npx fetches it on first run
```

**Step 2:** User configures HiLite:

```yaml
# ~/.hilite/config.yaml
mcp_servers:
  slack:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-slack"]
    env:
      SLACK_BOT_TOKEN: "${SLACK_BOT_TOKEN}"
      SLACK_TEAM_ID: "${SLACK_TEAM_ID}"
```

**Step 3:** User creates a skill (optional but recommended):

```markdown
# ~/.hilite/skills/slack-latest.md
---
name: slack-latest
description: Get latest messages from Slack and summarize them.
---

When asked about Slack activity:
1. Call `mcp_slack_list_channels` to find the channel
2. Call `mcp_slack_get_channel_history` with limit 20
3. Summarize key topics, decisions, and action items
```

**Step 4:** User runs HiLite:

```bash
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_TEAM_ID=T...

hilite "What did I miss in #engineering today?"
```

**What happens:**

1. `AIAgent` starts, creates `ToolRegistry`
2. `MCPClient` reads config, spawns `npx -y @modelcontextprotocol/server-slack`
3. Handshake completes, `tools/list` returns:
   - `slack_post_message` → registered as `mcp_slack_post_message`
   - `slack_get_channel_history` → registered as `mcp_slack_get_channel_history`
   - ...
4. `SkillStore` scans `~/.hilite/skills/`, finds `slack-latest.md`
5. System prompt includes skills index mentioning `slack-latest`
6. Model sees user question, decides to call `skill_view(name="slack-latest")`
7. Skill content is returned: "When asked about Slack activity: 1. Call `mcp_slack_list_channels`..."
8. Model follows the skill, calls `mcp_slack_list_channels`, then `mcp_slack_get_channel_history`
9. Results returned, model summarizes for user

---

## 6. Implementation Order

1. **MCP stdio transport** (`hilite/mcp/transport.py` + `session.py`)
   - Stdio subprocess + JSON-RPC
   - Initialize handshake
   - `tools/list` call

2. **MCP client core** (`hilite/mcp/client.py`)
   - Config reading
   - Connection management (stdio)
   - Tool registration with `ToolRegistry`
   - Dedicated event loop thread

3. **ToolRegistry integration**
   - Accept dynamic tool registration
   - MCP tool prefixing
   - Schema normalization

4. **mTLS support** (`hilite/mcp/auth.py` + transport updates)
   - `client_cert` / `client_key` / `ssl_verify` config parsing
   - `httpx.AsyncClient` cert injection for HTTP/SSE

5. **OAuth 2.1 PKCE** (`hilite/mcp/oauth.py`)
   - Token storage on disk (`~/.hilite/mcp-oauth/`)
   - Callback server for authorization code capture
   - Browser launch for interactive flow
   - Auth recovery on 401 (refresh + retry)
   - Integration with HTTP/SSE transport

6. **HTTP/SSE transport** (transport.py updates)
   - `streamable-http` transport via MCP SDK
   - `sse` transport via MCP SDK
   - Wire mTLS and OAuth into transport creation

7. **Skills system** (`hilite/skills.py`)
   - Discovery, loading, `skill_view` tool
   - System prompt index injection

8. **Project-level config** (`hilite/config.py` updates)
   - Merge global `~/.hilite/config.yaml` + project `.hilite/config.yaml`
   - MCP activation filters (`enabled` / `disabled`)
   - Project-level `mcp_servers` merge/override

9. **CLI enhancements**
   - `--skill` flag
   - Slash command interception

10. **Testing**
   - Mock MCP server for stdio tests
   - Test schema normalization edge cases
   - Test skill discovery and loading
   - Test mTLS cert resolution
   - Test OAuth token storage (without browser flow)

---

## 7. Estimated Code Size

| Component | Lines (est.) |
|-----------|-------------|
| `hilite/mcp/transport.py` | 120 |
| `hilite/mcp/session.py` | 60 |
| `hilite/mcp/client.py` | 180 |
| `hilite/mcp/auth.py` | 60 |
| `hilite/mcp/oauth.py` | 200 |
| `hilite/skills.py` | 80 |
| Changes to `registry.py` | 30 |
| Changes to `agent.py` | 15 |
| Changes to `config.py` | 10 |
| **Total new** | **~755 lines** |

This brings HiLite to ~1,930 total lines — more substantial, but with full MCP client, OAuth, mTLS, and skills support.

---

## 8. Reference Implementation (Hermes)

The following table maps each HiLite spec section to the corresponding Hermes source files at `../hermes-agent/`. Use these as reference implementations when building HiLite's version.

> **Note:** All paths are relative to `../hermes-agent/`. Line numbers are approximate (check surrounding context).

### MCP Client

| HiLite Spec | Hermes File | Key Symbols (line ~) | Description |
|-------------|-------------|----------------------|-------------|
| §2.1 Config format | `hermes_cli/mcp_config.py` | `_get_mcp_servers()` (l.77), `_save_mcp_server()` (l.90) | Read/write `mcp_servers` from `~/.hermes/config.yaml` |
| §2.1.1 Project activation | `cli.py` | `_check_config_mcp_changes()` (l.10456), `_reload_mcp()` (l.10702) | Config watcher, reload, toolset validation |
| §2.2 Discovery | `tools/mcp_tool.py` | `discover_mcp_tools()` (l.3467), `_discover_and_register_server()` (l.3343) | Entry point, per-server connect + register |
| §2.2 Tool prefixing | `tools/mcp_tool.py` | `_register_server_tools()` (~l.3400) | Prefixes tools as `mcp_{server}_{tool}` |
| §2.2 Schema normalization | `tools/mcp_tool.py` | `_normalize_schema()` (~l.3300) | Collapses `anyOf`, drops unsupported keywords |
| §2.3 Dispatch | `tools/mcp_tool.py` | `_make_tool_handler()` (l.2473), `MCPServerTask.call_tool()` (~l.2400) | Sync wrapper, async execution |
| §2.3 Event loop thread | `tools/mcp_tool.py` | `_ensure_mcp_loop()` (l.2325), `_run_on_mcp_loop()` (~l.2350) | Dedicated "mcp-event-loop" daemon thread |
| §2.4 mTLS | `tools/mcp_tool.py` | `_resolve_client_cert()` (l.573) | Parses `client_cert`/`client_key`/`ssl_verify` |
| §2.4 mTLS httpx factory | `tools/mcp_tool.py` | `MCPServerTask._run_http()` (l.1460), `_mcp_http_client_factory()` (~l.1540) | Injects cert/verify into SSE via factory callback |
| §2.5 OAuth | `tools/mcp_oauth.py` | `HermesTokenStorage` (l.218), `build_oauth_auth()` (l.725), `_Handler` (l.366) | Token storage, browser flow, callback server |
| §2.5 OAuth manager | `tools/mcp_oauth_manager.py` | `MCPOAuthManager` (l.339), `get_manager()` (l.594), `get_or_build_provider()` (l.353) | Singleton, per-server state, 401 dedup |
| §2.5 OAuth 401 recovery | `tools/mcp_tool.py` | `_auth_failure_types()` (l.1915), `_is_auth_failure()` (l.1966), `_try_oauth_recovery()` (~l.1995) | Detect auth errors, refresh tokens |
| §2.6 Lifecycle | `tools/mcp_tool.py` | `MCPServerTask` class (l.1096), `run()` (l.1661), `_run_stdio()` (l.1340), `_run_http()` (l.1460) | Server lifecycle: connect, discover, keepalive, reconnect |
| §2.6 Shutdown | `tools/mcp_tool.py` | `shutdown_mcp_servers()` (l.3640), `_kill_orphaned_mcp_children()` (~l.3700) | Graceful shutdown, SIGTERM → SIGKILL |
| §2.7 File layout | `tools/mcp_tool.py` | Entire file (3,792 lines) | The canonical MCP client reference |

### Tool Registry & Orchestration

| HiLite Spec | Hermes File | Key Symbols (line ~) | Description |
|-------------|-------------|----------------------|-------------|
| §4.1 ToolRegistry | `tools/registry.py` | `ToolRegistry.register()` (l.234), `.deregister()` (l.307), `.dispatch()` (l.390), `.get_definitions()` (l.337) | Central registry for built-in + MCP + plugin tools |
| §4.1 Toolset aliases | `tools/registry.py` | `register_toolset_alias()` (l.208), `get_tool_names_for_toolset()` (l.201) | Named tool collections |
| §4.2 Model tools | `model_tools.py` | `get_tool_definitions()` (l.264), `handle_function_call()` (l.802) | Orchestration: discovery, schema building, dispatch |
| §4.2 Builtin discovery | `tools/registry.py` | `discover_builtin_tools()` (l.57) | Auto-import `tools/*.py` modules |

### Skills System

| HiLite Spec | Hermes File | Key Symbols (line ~) | Description |
|-------------|-------------|----------------------|-------------|
| §3.1 Skill format | `skills/<category>/<name>/SKILL.md` | Various | YAML frontmatter + markdown body pattern |
| §3.3 Discovery | `agent/skill_commands.py` | `scan_skill_commands()` (l.263), `get_skill_commands()` (l.329) | Filesystem scan, slash command registration |
| §3.5 skill_view | `tools/skills_tool.py` | `skill_view()` (l.807), `skills_list()` (l.632) | Load skill by name, list all skills |
| §3.5 Slash commands | `agent/skill_commands.py` | `build_skill_invocation_message()` (l.428) | Convert `/skill-name` to tool call |
| §3.5 System prompt | `agent/prompt_builder.py` | `build_skills_system_prompt()` (l.1039) | Compact skills index injected into prompt |
| §3.5 Preprocessing | `agent/skill_preprocessing.py` | Various | Template var substitution, inline shell expansion |

### Hermes as MCP Server (context only — not in HiLite scope)

| HiLite Spec | Hermes File | Key Symbols (line ~) | Description |
|-------------|-------------|----------------------|-------------|
| — | `mcp_serve.py` | Entire file | Hermes exposes conversations as MCP server |
| — | `acp_adapter/` | `server.py`, `session.py`, `tools.py` | ACP (Agent Communication Protocol) adapter |

---

## 9. Comparison: Hermes vs. HiLite Approach

| Feature | Hermes | HiLite (this spec) |
|---------|--------|-------------------|
| **MCP transports** | stdio, HTTP, SSE, StreamableHTTP | stdio, HTTP, SSE |
| **MCP auth** | OAuth 2.1 PKCE, mTLS, API key | OAuth 2.1 PKCE, mTLS, env vars |
| **MCP discovery** | Lazy, background thread | Eager, blocking at startup |
| **MCP tool prefix** | `mcp_{server}_{tool}` | Same |
| **MCP event loop** | Dedicated daemon thread | Same |
| **MCP circuit breaker** | Yes (3 failures → 60s) | No |
| **MCP dynamic reload** | Yes (config watcher) | No (restart required) |
| **MCP sampling** | Yes (server asks LLM) | No |
| **OAuth token reload** | mtime-based disk watch | No (read on startup only) |
| **OAuth 401 dedup** | In-flight futures | No (retry once) |
| **Skill format** | YAML frontmatter + markdown body | Same |
| **Skill structure** | Category dirs + subdirs | Flat dirs (`~/.hilite/`, `.hilite/`, `.agents/`) |
| **Skill extras** | references/, templates/, scripts/ | None |
| **Skill bundles** | Yes | No |
| **Skill conditional activation** | Yes (platform, toolset) | No |
| **Skill agent CRUD** | Yes (`skill_manage` tool) | No |
| **Skill system prompt** | Compact index with categories | Flat list |
| **Skill invocation** | `skill_view()`, `/command`, auto | `skill_view()`, `/command`, `--skill` |
| **MCP project-level activation** | No (global only) | Yes (`.hilite/config.yaml`) |
| **Skills project-level** | No (global only) | Yes (`.hilite/skills/`, `.agents/skills/`) |

---

## 9. Open Questions

1. **Should MCP tools be opt-in per session?** Hermes lets you enable/disable toolsets. For HiLite, maybe `--mcp` flag to enable MCP on a per-run basis (avoids slow startup when not needed)?

2. **What if two MCP servers expose the same tool name?** We prefix with server name (`mcp_slack_post_message` vs `mcp_discord_post_message`), so collisions are unlikely. But what if a single server has duplicate names? Skip with warning.

3. **ImageContent from MCP tools?** The Slack MCP server might return image attachments. For v1, we ignore them (log warning). Future: save to temp file, return `![image](path)` markdown.

4. **Skill vs. MCP tool overlap?** A skill might document how to use MCP tools. That's fine — skills are procedural knowledge, MCP tools are capabilities. They compose.

5. **Memory integration?** Should skills be auto-suggested based on conversation context? No in v1 — the model sees the index and decides.

6. **OAuth in non-interactive environments?** The browser flow requires a TTY. For CI/scripts, should we support a `--oauth-callback-url` flag or a pre-authentication command (e.g. `hilite mcp auth <server>`)?

7. **mTLS with encrypted keys?** The spec supports key passphrases via `client_cert: [cert, key, password]`. Should we also support macOS Keychain / ssh-agent for key storage? Probably v2.

8. **HTTP proxy support?** Corporate environments often require HTTP proxies. Should we support `HTTP_PROXY`/`HTTPS_PROXY` env vars for MCP HTTP/SSE connections? The MCP SDK may already handle this via `httpx`.

9. **`.agents/skills` vs `.hilite/skills` priority?** If both exist in a project, which takes precedence? The spec says `.hilite/skills` wins (HiLite-specific overrides generic). Is that the right default, or should it be configurable?

10. **Should project-level MCP servers override global or merge?** The spec says project-level `mcp_servers` entries win on name collision. What if the user wants to *extend* a global server config (e.g. add a project-specific header) rather than replace it entirely? Probably v2 — keep v1 simple (replacement).
