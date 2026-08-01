# Claude Code: Monitoring and Telemetry Reference

A study guide to OpenTelemetry in Claude Code: configuration and managed lockdown, cardinality control, the metric and event catalog, redaction gates, distributed tracing, and using events as an audit source.

Verified against code.claude.com/docs/en/monitoring-usage.

---

## 1. The three signals

Claude Code exports **metrics** via the standard metrics protocol, **events** via the logs/events protocol, and optionally **distributed traces** via the traces protocol. Each is configured independently; you can enable one and not the others.

**Telemetry is opt-in and off by default.** Nothing is exported until `CLAUDE_CODE_ENABLE_TELEMETRY=1`.

```bash
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp        # otlp, prometheus, console, none
export OTEL_LOGS_EXPORTER=otlp           # otlp, console, none
export OTEL_EXPORTER_OTLP_PROTOCOL=grpc
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer your-token"
claude
```

**Claude Code has no default protocol.** You must set `OTEL_EXPORTER_OTLP_PROTOCOL` or the per-signal variant for each `otlp` exporter you enable.

**Default intervals: 60s for metrics, 5s for logs.** Shorten during setup, reset for production.

### Verifying

- **Metrics**: check for `claude_code.session.count`, emitted when a session starts
- **Logs only**: submit a prompt and check for `claude_code.user_prompt`
- **Nothing arriving**: run `claude --debug` and look for OTel export errors

---

## 2. The subprocess isolation rule

**Claude Code does not pass `OTEL_*` environment variables to any subprocess it spawns** — the Bash tool, hooks, MCP servers, and language servers.

The consequence: an OpenTelemetry-instrumented application you run through the Bash tool **does not inherit Claude Code's endpoint or headers**. Set those variables directly in the command if it needs to export its own telemetry.

---

## 3. Administrator configuration and destination locking

Deploy via the managed settings file:

```json
{
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector.example.com:4317",
    "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer example-token"
  }
}
```

### What managed settings remove

Setting an `OTEL_EXPORTER_OTLP_*` variable in managed settings makes Claude Code **remove conflicting developer-set variables at startup**, logging a warning visible with `--debug`. What it removes depends on which you set:

| You set | Claude Code removes |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | **Every** developer-set per-signal endpoint. You do not need to set the per-signal variables too |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | Every developer-set per-signal protocol |
| `OTEL_EXPORTER_OTLP_HEADERS`, `_CLIENT_KEY`, or `_CLIENT_CERTIFICATE` | The developer-set per-signal version **plus every developer-set endpoint, generic or per-signal**, since those credentials would otherwise reach a collector you did not choose |

**The exporter selectors are the gap.** `OTEL_METRICS_EXPORTER`, `OTEL_LOGS_EXPORTER`, and `OTEL_TRACES_EXPORTER` **follow normal per-key precedence**, so a developer can still disable a signal or switch it to the console exporter. **Set the selectors in managed settings too if you need them locked.**

**Per-signal variables you set in managed settings yourself are not removed**, so you can deliberately route one signal to a different collector.

**This removal changes where telemetry is delivered, not what Claude Code collects.**

Before v2.1.217, every variable followed per-key precedence independently, so a signal-specific endpoint in user settings or the shell **redirected that signal away from the managed collector**.

### mTLS

**Client certificate configuration depends on the protocol**, which is a genuine trap:

| Protocol | Client cert variables | Trust the collector CA with |
|---|---|---|
| `http/protobuf`, `http/json` | `CLAUDE_CODE_CLIENT_CERT`, `CLAUDE_CODE_CLIENT_KEY`, optional `CLAUDE_CODE_CLIENT_KEY_PASSPHRASE` | `NODE_EXTRA_CA_CERTS` |
| `grpc` | `OTEL_EXPORTER_OTLP_CLIENT_KEY`, `OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE`, or per-signal variants | `OTEL_EXPORTER_OTLP_CERTIFICATE` |

### Dynamic headers

For token refresh, `otelHeadersHelper` in settings points at a script printing JSON key-value pairs:

```json
{ "otelHeadersHelper": "/path/to/generate-otel-headers.sh" }
```

**Only applies to `http/protobuf` and `http/json`.** The `grpc` exporter uses only the static `OTEL_EXPORTER_OTLP_HEADERS`.

**Runs at startup and every 29 minutes** by default; tune with `CLAUDE_CODE_OTEL_HEADERS_HELPER_DEBOUNCE_MS`. Failures surface in `/status`, the debug log, and stderr in `-p` sessions.

---

## 4. Cardinality control

| Variable | Attribute | Default |
|---|---|---|
| `OTEL_METRICS_INCLUDE_SESSION_ID` | `session.id` | **true** |
| `OTEL_METRICS_INCLUDE_VERSION` | `app.version` | **false** |
| `OTEL_METRICS_INCLUDE_ACCOUNT_UUID` | `user.account_uuid`, `user.account_id` | **true** |
| `OTEL_METRICS_INCLUDE_ENTRYPOINT` | `app.entrypoint` | **false** |
| `OTEL_METRICS_INCLUDE_RESOURCE_ATTRIBUTES` | Keys from `OTEL_RESOURCE_ATTRIBUTES` | **true** |

### Multi-team attribution

```bash
export OTEL_RESOURCE_ATTRIBUTES="department=engineering,team.id=platform,cost_center=eng-123"
```

Claude Code attaches these **as attributes on every metric datapoint and event record**, in addition to the OTLP resource block, so most backends expose them as queryable labels.

**Custom keys never override standard attributes.** On collision, the built-in value wins.

**Each custom key becomes a label on every metric series**, so high-cardinality values cost storage. `OTEL_METRICS_INCLUDE_RESOURCE_ATTRIBUTES=false` sends them in the resource block only.

**The formatting rules are strict and bite immediately:**

- **No spaces in values.** `user.organizationName=My Company` is invalid
- Comma-separated `key=value` pairs only
- US-ASCII excluding control characters, whitespace, double quotes, commas, semicolons, backslashes
- **Quoting does not escape spaces.** `org.name="My Company"` yields the literal value including the quotes
- Percent-encode anything else: `org.name=John%27s%20Organization`

---

## 5. Standard attributes

| Attribute | Notes |
|---|---|
| `session.id` | Per session |
| `app.version` | Off by default |
| `app.entrypoint` | `cli`, `sdk-cli`, `sdk-ts`, `sdk-py`, `claude-vscode`. Off by default |
| `organization.id` | When authenticated |
| `user.account_uuid`, `user.account_id` | The `account_id` uses the tagged format matching Anthropic admin APIs |
| `user.id` | **Random anonymous identifier generated on first run, persisted in `~/.claude.json`.** No personal information, not derived from your account. **Deleting the file produces a new unrelated value** |
| `user.email` | When authenticated via OAuth |
| `terminal.type` | `iTerm.app`, `vscode`, `cursor`, `tmux` |

**On a Claude apps gateway session**, `user.id` becomes the **IdP subject** rather than an installation identifier, `user.email` is the signed-in email, `user.groups` carries IdP group membership, and each export carries `identity.source: gateway-oidc`. **The gateway identity is applied last**, so `user.*` and `identity.*` keys set through `OTEL_RESOURCE_ATTRIBUTES` are ignored there.

### Event-only attributes

**Never attached to metrics, because they would cause unbounded cardinality:**

- **`prompt.id`** — UUID v4 linking every event produced while processing one prompt
- `workspace.host_paths` — desktop app workspace directories
- `workflow.run_id` / `workflow.name` — workflow attribution (v2.1.202+)

---

## 6. Metrics

| Metric | Unit |
|---|---|
| `claude_code.session.count` | none |
| `claude_code.lines_of_code.count` | none |
| `claude_code.pull_request.count` | none |
| `claude_code.commit.count` | none |
| `claude_code.cost.usage` | USD |
| `claude_code.token.usage` | tokens |
| `claude_code.code_edit_tool.decision` | none |
| `claude_code.active_time.total` | s |

**When `prometheus` is the only exporter**, Claude Code omits the `USD`, `tokens`, and `s` units so the scrape stays valid Prometheus text format (v2.1.216+). Combined configurations like `otlp,prometheus` keep them. Before v2.1.216, OpenMetrics-only `# UNIT` lines caused some scrapers to reject the scrape.

### Notable per-metric attributes

**Session counter** carries `start_type`: `fresh`, `resume`, `continue`, or **`agents_view`**. The last identifies the `claude agents` dashboard process, **a user-launched local UI rather than a conversational session** — filter it out to keep dashboards honest.

**Cost and token counters** carry rich attribution: `model`, `query_source` (`main` / `subagent` / `auxiliary`), `speed`, `effort`, plus `agent.name`, `skill.name`, `plugin.name`, `marketplace.name`, `mcp_server.name`, `mcp_tool.name`.

**The redaction pattern in that attribution is consistent and worth memorizing**: built-in, bundled, and official-marketplace names appear verbatim; **third-party plugin names collapse to `"third-party"`, user-defined agents to `"custom"`, and user-configured MCP servers to `"custom"`** unless `OTEL_LOG_TOOL_DETAILS=1`.

**Code edit tool decision counter** carries `tool_name`, `decision`, `source`, and `language` (returning `"unknown"` for unrecognized extensions).

**Active time counter** tracks time actively using Claude Code, **excluding idle**, incremented during user interactions (`type: "user"`) and CLI processing (`type: "cli"`).

---

## 7. Events

### Correlation

**`prompt.id` is the workhorse.** Filtering events by one value returns the `user_prompt`, every `api_request`, and every `tool_result` produced while processing that prompt.

Two more correlation keys (v2.1.214+):

- **`message.uuid`** — matches the persisted transcript entry
- **`client_request_id`** — the client-generated `x-client-request-id`, **available even for failures like timeouts that never produced a server `request_id`**

**A warning the docs state explicitly:** the transcript entry format is internal and changes between versions, so **a pipeline joining on `message.uuid`, `request_id`, or `tool_use_id` can break on any release.** Treat those joins as version-specific rather than a stable contract.

### The catalog

| Event | Logged when |
|---|---|
| `user_prompt` | A prompt is submitted |
| `assistant_response` | An API request returns text content (v2.1.193+). **Text blocks only; thinking and tool-use blocks excluded** |
| `tool_result` | A tool completes. **Not emitted for rejected calls** |
| `tool_decision` | A permission decision is made |
| `api_request` | Each API request |
| `api_error` | A request fails |
| `api_refusal` | A response returns `stop_reason: "refusal"` |
| `api_retries_exhausted` | A request fails after more than one attempt |
| `api_request_body` / `api_response_body` | With `OTEL_LOG_RAW_API_BODIES` set |
| `permission_mode_changed` | Mode changes, with a `trigger` attribute |
| `auth` | `/login` or `/logout` completes |
| `mcp_server_connection` | An MCP server connects, disconnects, or fails |
| `internal_error` | An unexpected internal error. **Only the class name and errno code; never the message or stack** |
| `plugin_installed` | A plugin finishes installing |
| `plugin_loaded` | Once per enabled plugin at session start |
| `skill_activated` | A skill is invoked |
| `hook_registered` | Once per configured hook at session start |
| `hook_execution_start` / `hook_execution_complete` | Hooks begin and finish |
| `hook_plugin_metrics` | **Official-marketplace plugin hooks only** |
| `at_mention` | An `@`-mention resolves |
| `compaction` | Compaction completes |
| `feedback_survey` | A session quality survey is shown or answered |

### Details worth knowing

**`api_refusal` exists because refusals arrive on a successful response stream, so `api_error` does not fire for them.** Its `server_fallback_hop` attribute is `true` when the API's server-side fallback already retried on another model, meaning **the user never saw that particular refusal**. A single turn can emit both a `true` hop event and a later `false` final event when the fallback model also refuses. `has_category` covers `cyber`, `bio`, `frontier_llm`, and `reasoning_extraction`; the actual `category` value requires `OTEL_LOG_TOOL_DETAILS=1`.

**`tool_decision`'s `source` values** are the audit vocabulary:

| Source | Meaning |
|---|---|
| `config` | Decided automatically: settings, managed policy, CLI flags, the active mode, a session-scoped grant, or an inherently safe tool. **The event does not say which** |
| `hook` | A `PreToolUse` or `PermissionRequest` hook decided |
| `user_permanent` | "Yes, and don't ask again", saving a rule |
| `user_temporary` | One-time "Yes", or a "during this session" option |
| `user_abort` | Prompt dismissed without answering. Treated as reject |
| `user_reject` | "No". Treated as reject |

**A behavioral difference by surface**: in the interactive CLI, `user_permanent` and `user_temporary` are emitted **only for the choice itself**, with later matching calls emitting `config`. **In Agent SDK and `-p` sessions, both the choice and later matches emit the user source.**

**`plugin_loaded`** carries a `plugin_id_hash` — a deterministic hash of plugin name and marketplace, **sent only to your exporter** — letting you count distinct third-party plugins across a fleet without recording names. It also reports `safe_mode`, and in safe mode **reports configured inventory only**, since nothing actually loads.

**`compaction`** carries `precompute_reuse` on manual triggers, recording whether `/compact` reused a background-prepared summary: `hit`, or `miss_custom_instructions` / `miss_hook` / `miss_not_ready`.

---

## 8. Redaction gates

Content is redacted by default. Four independent switches:

| Variable | Reveals |
|---|---|
| `OTEL_LOG_USER_PROMPTS` | Prompt content on `user_prompt` and the `user_prompt` span attribute |
| `OTEL_LOG_ASSISTANT_RESPONSES` | Response text on `assistant_response` (v2.1.193+). **When unset it falls back to `OTEL_LOG_USER_PROMPTS`**, so set it to `0` explicitly to keep responses redacted while prompt logging is on |
| `OTEL_LOG_TOOL_DETAILS` | Bash commands, MCP server and tool names, skill names, workflow names, tool input, and command names on `user_prompt` |
| `OTEL_LOG_TOOL_CONTENT` | Tool input and output bodies in span events. **Requires tracing** |

**`OTEL_LOG_RAW_API_BODIES`** emits the full Messages API request and response JSON. **The bodies include the entire conversation history**, and the docs state that **enabling it implies consent to everything the other three would reveal.**

- `=1` — inline, truncated at the content limit
- `=file:<dir>` — untruncated on disk with a `body_ref` pointer in the event

**Extended-thinking content is redacted in both directions.**

**`CLAUDE_CODE_OTEL_CONTENT_MAX_LENGTH`** caps content-bearing attributes, **default 61440 UTF-16 code units (60 KB)**, sized for backends capping attribute values at 64 KB. When an OpenTelemetry SDK attribute limit is set lower, Claude Code truncates at the smaller value so the `[TRUNCATED ...]` marker stays within it.

**One exception to the gating**: for Claude Desktop's built-in servers, in sessions Desktop owns, `mcp_server_name` and `mcp_tool_name` emit on `tool_decision` and `tool_result` **even with `OTEL_LOG_TOOL_DETAILS` off** (v2.1.214+), because otherwise a rejected call would be unattributable.

---

## 9. Traces (beta)

Off by default. Requires **both** `CLAUDE_CODE_ENABLE_TELEMETRY=1` **and** `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`, plus `OTEL_TRACES_EXPORTER`. Traces reuse the common OTLP endpoint, protocol, headers, and mTLS configuration.

### Hierarchy

```text
claude_code.interaction
├── claude_code.llm_request
├── claude_code.hook                    (detailed beta tracing only)
└── claude_code.tool
    ├── claude_code.tool.blocked_on_user
    ├── claude_code.tool.execution
    └── (Agent tool) subagent llm_request / tool spans
```

**Each prompt starts an `interaction` root span.** Tool spans split into permission wait and execution, which makes "how long did we spend waiting on humans" directly queryable.

**`llm_request`, `tool.execution`, and `hook` set status `ERROR` on failure; the others always end `UNSET`.**

Spans carry OpenTelemetry **GenAI semantic convention aliases** alongside Claude Code's own names: `gen_ai.system` (always `anthropic`), `gen_ai.request.model`, `gen_ai.response.id`, `gen_ai.response.finish_reasons`, `gen_ai.tool.call.id`.

### Context propagation

**Outbound**: when tracing is active, **Bash and PowerShell subprocesses inherit a `TRACEPARENT` variable** with the active tool execution span's context, so scripts can parent their own spans under the same trace. Model requests and outbound HTTP MCP requests carry a `traceparent` header, and the API's `traceresponse` is recorded as a span link.

**The header is sent only when `ANTHROPIC_BASE_URL` is unset or points at the Anthropic API**, since some proxies reject unrecognized headers, and **the subprocess variable follows the same switch**. Set `CLAUDE_CODE_PROPAGATE_TRACEPARENT=1` to force it through a custom proxy. **Never sent to third-party providers.**

**Inbound**: in Agent SDK and `-p` sessions, Claude Code **reads `TRACEPARENT` and `TRACESTATE` from its own environment**, so its spans nest under the caller's trace. **Interactive sessions ignore inbound `TRACEPARENT`** to avoid inheriting ambient CI or container values.

**The inbound context also reaches events**: in SDK and `-p` sessions with `TRACEPARENT` set, each event log record carries `trace_id` and `span_id`, **even when the traces exporter is not configured.**

### Detailed beta tracing

The `claude_code.hook` span and several content-bearing attributes (`new_context`, `system_prompt_preview`, `user_system_prompt`, `tool_input`, `response.model_output`) require **`ENABLE_BETA_TRACING_DETAILED=1` and `BETA_TRACING_ENDPOINT`** in addition to trace exporter configuration, and **in interactive CLI sessions, your organization to be allowlisted**. SDK and `-p` sessions are not gated.

**These are not part of the stable span schema.** `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA` alone does not produce them.

---

## 10. Audit and SIEM

**OpenTelemetry events are the audit data source for Claude Code activity.** The OTLP logs exporter delivers to any SIEM with an OTLP receiver, or through a Collector.

### Identity attribution

**Claude Code does not act under a separate service account.** MCP calls, Bash commands, and file edits are attributed to the developer who started the session, via `user.email`, `user.account_uuid`, `user.account_id`, `organization.id`, `user.id`, and `session.id`.

**The gap**: with a direct API key, or on Bedrock, Agent Platform, or Foundry, **there is no Claude account in the session and only `user.id` and `session.id` are populated.** Attach identity yourself:

```bash
export OTEL_RESOURCE_ATTRIBUTES="enduser.id=jdoe@example.com,enduser.directory_id=S-1-5-21-..."
```

**Claude apps gateway sessions need none of this**, since the CLI stamps the IdP identity automatically.

### MCP auditing

With `OTEL_LOG_TOOL_DETAILS=1`, three events give full call detail: `mcp_server_connection` (server name, transport, scope, errors), `tool_result` (tool name, scope, parameters, arguments), and `tool_decision` (allowed or denied, and by whom).

**Without the flag**, `tool_result` and `tool_decision` redact `tool_name` to the literal **`"mcp_tool"`** for user-configured servers and omit argument content, and `mcp_server_connection` omits `server_name` and the error message. **It keeps `is_plugin`, `plugin_id_hash`, and `plugin.name`**, so plugin-provided servers stay distinguishable even on the default stream.

---

## 11. Interpreting the data

### Cost

**Cost metrics are approximations.** For official billing, use your provider's console.

Attribute spend via `skill.name`, `plugin.name`, and `agent.name` on the cost and token metrics.

**A correctness fix worth knowing**: as of v2.1.214, each streaming response counts toward cost and token metrics exactly once, including when a gateway streams usage progressively across frames. **Before v2.1.214, multi-frame usage inflated both metrics by roughly one extra full request per extra frame.**

### Per-model commit attribution

Commits carry no `model` attribute. Approximate by joining against token or cost metrics on `session.id`, and **filter the token or cost side to `query_source == "main"`** so auxiliary and subagent requests do not attribute the session's commits to a model that did not make them.

### Retry exhaustion

**Intermediate retries are not logged as separate events.** A single `api_error` is emitted only after Claude Code gives up, so the event itself is the terminal signal.

The `attempt` attribute records total attempts. `CLAUDE_CODE_MAX_RETRIES` defaults to 10, capped at 15; **as of v2.1.199, `CLAUDE_CODE_RETRY_WATCHDOG` raises the default and removes the cap.** On full exhaustion of a transient error, `attempt` equals **one more than the effective limit: 11 by default, never above 16** without the watchdog. **A lower value indicates a non-retryable error** such as a 400.

To distinguish a session that recovered from one that stalled, group by `session.id` and check for a later `api_request`.

---

## 12. Gotchas

1. **Telemetry is entirely opt-in.** Nothing exports without `CLAUDE_CODE_ENABLE_TELEMETRY=1`.
2. **There is no default OTLP protocol.** Set it explicitly or exports fail.
3. **`OTEL_*` variables are stripped from every subprocess**, including hooks and MCP servers.
4. **Exporter selectors are not locked by managed settings.** A developer can still switch a signal to console or disable it.
5. **Setting a managed credential removes developer-set endpoints too**, generic and per-signal.
6. **mTLS variables differ by protocol.** HTTP uses `CLAUDE_CODE_CLIENT_*`; gRPC uses `OTEL_EXPORTER_OTLP_CLIENT_*`.
7. **Dynamic headers work only on HTTP protocols**, never gRPC.
8. **`OTEL_RESOURCE_ATTRIBUTES` forbids spaces**, and quoting does not escape them — the quotes end up in the value.
9. **Custom resource keys never override standard attributes**, and each becomes a label on every metric series.
10. **`prompt.id` is deliberately excluded from metrics.** Event-level analysis only.
11. **`user.id` is anonymous and installation-scoped**, resets if `~/.claude.json` is deleted, except on gateway sessions where it is the IdP subject.
12. **`start_type: "agents_view"` is a UI process, not a conversation.** Filter it from adoption dashboards.
13. **Third-party plugin, user-defined agent, and user-configured MCP names are redacted by default** to `"third-party"` or `"custom"`.
14. **`OTEL_LOG_ASSISTANT_RESPONSES` falls back to `OTEL_LOG_USER_PROMPTS` when unset.** Set it to `0` to keep responses redacted.
15. **`OTEL_LOG_RAW_API_BODIES` includes the entire conversation history** and implies consent to every other gate.
16. **`OTEL_LOG_TOOL_CONTENT` requires tracing.**
17. **Content attributes truncate at 60 KB by default.**
18. **`assistant_response` excludes thinking and tool-use blocks.**
19. **`tool_result` is not emitted for rejected calls.** Use `tool_decision`.
20. **`source: "config"` does not say which rule matched.**
21. **`user_permanent` and `user_temporary` behave differently in the CLI versus SDK/`-p` sessions** for repeat calls.
22. **`api_refusal` exists because refusals arrive on a successful stream**, so `api_error` never fires for them.
23. **A `server_fallback_hop: true` refusal was never seen by the user.**
24. **`api_error` fires only after all retries.** Intermediate attempts are invisible.
25. **`attempt` of 11 means exhaustion; a lower value means non-retryable.**
26. **Cost metrics are approximations**, and were inflated by multi-frame streaming before v2.1.214.
27. **Commits carry no model attribute.** Join on `session.id` filtered to `query_source == "main"`.
28. **Traces need two variables**, and the `hook` span needs detailed beta tracing plus an org allowlist in interactive sessions.
29. **`traceparent` is not sent to third-party providers** or through a custom base URL without `CLAUDE_CODE_PROPAGATE_TRACEPARENT=1`.
30. **Interactive sessions ignore inbound `TRACEPARENT`** by design.
31. **Prometheus-only exports drop units** as of v2.1.216.
32. **With a direct API key or on third-party providers, only `user.id` and `session.id` identify the user.** Attach identity yourself.
33. **Transcript-join keys are version-specific**, not a stable contract.
34. **`internal_error` never contains the message or stack**, and is not emitted on Bedrock, Agent Platform, or Foundry, or with `DISABLE_ERROR_REPORTING` set.

---

## Reference links

- Monitoring: https://code.claude.com/docs/en/monitoring-usage
- OpenTelemetry exporter specification: https://github.com/open-telemetry/opentelemetry-specification/blob/main/specification/protocol/exporter.md
- Settings and precedence: https://code.claude.com/docs/en/settings
- Environment variables: https://code.claude.com/docs/en/env-vars
- Network configuration and mTLS: https://code.claude.com/docs/en/network-config
- Claude apps gateway: https://code.claude.com/docs/en/claude-apps-gateway
- Analytics: https://code.claude.com/docs/en/analytics
- Data usage and session quality surveys: https://code.claude.com/docs/en/data-usage
- Sessions and transcript storage: https://code.claude.com/docs/en/sessions
- Hooks: https://code.claude.com/docs/en/hooks
- Workflows: https://code.claude.com/docs/en/workflows
- Model configuration and effort: https://code.claude.com/docs/en/model-config
- Full docs index: https://code.claude.com/docs/llms.txt
