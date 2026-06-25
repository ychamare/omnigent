# Manual QA plan — opencode-native gap closure (PR #1303)

Validates every change in PR #1303 against a real `opencode serve`. Each area
has **preconditions → steps → expected**. Items marked **[live-verified]** were
already confirmed against opencode 1.17.7 during development; re-run them as a
regression smoke. Items marked **[needs web]** can only be confirmed end-to-end
with the running Omnigent web UI.

## 0. Setup (once)

1. `omni setup` → OpenCode section: add a provider, pick a default model
   (confirm the model actually used matches the selection, not `big-pickle`).
2. Have a workspace with the Omnigent web UI reachable.
3. Keep two terminals handy: the Omnigent server logs and (optionally) an
   attached opencode TUI for the bidirectional/race tests.
4. Create an `opencode-native` session from the web UI and send one trivial
   prompt ("say hi") — confirm the assistant reply mirrors into the web chat
   (baseline streaming/forwarder sanity).

---

## 1. Compaction (P0)  [live-verified: wire]

**Auto-compaction (the common path)**
- Steps: drive a session near its context window (paste a large file, or loop
  several long turns) until opencode auto-compacts.
- Expected: web shows a **compaction marker** (in-progress → completed); the
  conversation continues afterward with reduced context. Server logs show
  `external_compaction_status` posted `in_progress` then `completed` off
  `session.next.compaction.started` / `.ended`.

**Explicit `/compact` from the web**
- Steps: click the web **Compact** action on an opencode-native session.
- Expected: a real summarization runs (runner calls opencode v1 `/summarize`
  with the session's resolved model) and the compaction marker completes — **not**
  a fake/no-op success. Regression check: confirm it is no longer instant-fake.
- Negative: on opencode 1.17.x the v2 `/compact` endpoint returns 503; confirm
  the runner used `/summarize` and did **not** surface a 503 to the user.

---

## 2a. MCP — Omnigent builtin relay  [needs web: model must call a sys_* tool]

This is the real "connects to Omnigent MCP" — opencode's model calling Omnigent
builtins (`sys_session_*`, `sys_agent_*`, `load_skill`, `web_fetch`,
`list_comments`, policy tools).
- Steps: in an opencode-native session, ask the model to do something that needs
  a builtin — e.g. "list my other sessions" (`sys_session_list`) or "load the
  X skill" (`load_skill`).
- Expected:
  - opencode's `opencode.json` `mcp` block has an `omnigent` `{type:"local"}`
    entry whose command is `… -m omnigent.claude_native_bridge serve-mcp
    --bridge-dir <bridge>`; the bridge dir holds `bridge.json` (token) +
    `tool_relay.json` (the relay tool list + URL).
  - The model can call the builtin and gets a real result (proxied through the
    Omnigent server, so policy applies — a builtin call shows up at the TOOL_CALL
    engine like any other tool; ensure your policy ALLOWs infra tools so they
    don't spuriously prompt).
  - Tear-down: deleting the session closes the relay (no orphaned localhost
    HTTP server / leftover `tool_relay.json`).

## 2b. MCP — agent's own servers  [live-verified: opencode loads the config]

- Preconditions: an agent spec with `mcp_servers` (one stdio, one http if
  available; an http server against Databricks to exercise the bearer token).
- Steps: launch an opencode-native session for that agent; ask the model to use
  a tool from the MCP server.
- Expected:
  - opencode's per-session `opencode.json` contains the agent servers in the
    `mcp` block (stdio→`local`, http→`remote` with the bearer header) **alongside**
    the `omnigent` relay entry, **and** `permission:{"*":"ask"}`.
  - The MCP tools are visible/callable by the model.
  - Because `permission:ask` is set, the tool call routes through the Omnigent
    TOOL_CALL **policy engine** (see §7) rather than running silently.

---

## 3. Cost tracking (P1)  [needs web: badge/ring rendering]

- Steps: send several turns in an opencode-native session.
- Expected:
  - Web **cost badge** increases per assistant turn; the **context ring**
    reflects occupancy; a cost-budget (if set) is enforced.
  - Server logs show `external_session_usage` with `cumulative_cost_usd`,
    cumulative input/output/cache tokens, `context_tokens`, `context_window`,
    and `model`, derived from per-message `cost`/`tokens`.
- Edge: two identical-usage turns should not double-post (de-dup via the usage
  signature) — watch for a single update per distinct message.

---

## 4. Resume (cross-host history)  [live-verified: noReply seeding]

- Steps: take a session with real history, then resume it where opencode lost
  the server-side session (restart the runner / resume on another host).
- Expected:
  - The Omnigent transcript is rehydrated as a **`noReply` context message**
    (a rendered text preamble of prior turns) — history is present, and the
    seed does **not** trigger a spurious model turn.
  - The next user prompt continues with that context.
- Regression: confirm resume no longer silently starts empty.

---

## 5. Fork (P1)  [needs web: fork action]

- Steps: from a session with history, use the web **Fork** action.
- Expected: the new session shows the copied transcript (reuses the resume
  text-preamble path — opencode-native is now in the fork-history set). The fork
  continues from that context.

---

## 6. In-harness session-cmd sync  [needs web + TUI]

**TUI → Omnigent (model mirror):**
- Steps: attach the opencode TUI; type `/model` and switch the model.
- Expected: the web session reflects the new model (`session.next.model.switched`
  → `external_model_change`).

**Omnigent → opencode (model switch):**
- Steps: change the model from the Omnigent web UI (model pill) on an
  opencode-native session, then send a web turn.
- Expected: bridge state `model_override` updates; the NEXT web-injected prompt
  uses the new model (opencode model is per-prompt, so it applies forward, not
  retroactively). A null/blank model clears the override.

**Omnigent → opencode (clear):**
- Steps: trigger `/clear` from Omnigent on an opencode-native session.
- Expected: a brand-new opencode session is created and the terminal relaunches
  on it (old forwarder/server cancelled); prior context is gone. opencode has no
  reset endpoint, so this is a fresh-session relaunch — verify the new session
  mirrors correctly and the old `external_session_id` is not resumed.

Compact/fork/resume are covered by §1/§4/§5.

---

## 7. Policies + tool-approval elicitation  [live-verified: permission round-trip]

- Preconditions: a policy that yields **ASK** for a specific tool (e.g. a `Bash`
  pattern), plus one that yields **DENY**.
- Steps: prompt the model to call each gated tool.
- Expected:
  - **ASK** → a web **approval card** appears; approving lets the call proceed,
    denying blocks it. (The human decision happens upstream in the policy
    evaluator; the forwarder relays the verdict via `reply_permission`.)
  - **DENY** → the call is blocked and a policy-denied error returns to the model
    (no card).
  - **ALLOW** → proceeds silently.
  - Fail-closed: if the policy evaluator errors or an `ask` reaches the forwarder
    unresolved, the request is **rejected**, never auto-approved.
- TUI coexistence: if the TUI is attached, answering the approval there should
  resolve the web card too (terminal-resolved race guard — first-answer-wins).

### 7a. Cost-budget enforcement  [needs web: budget + live turns]

opencode has no pre-tool hook (unlike claude-native), so the cost budget is
enforced **reactively** through the same policy engine: `permission:"ask"` makes
every tool call emit `permission.asked` → the forwarder POSTs a `PHASE_TOOL_CALL`
to `/policies/evaluate` → the cost-budget gate reads the session cost (from the
`external_session_usage` cost tracking, `cumulative_cost_usd` →
`total_cost_usd`). This is the codex-native model.
- Preconditions: set a **small per-session cost budget** on an opencode-native
  session (low enough to trip within a couple of turns).
- Steps: run turns until cumulative cost crosses the budget, then have the model
  attempt another tool call.
- Expected:
  - On the crossing, the next gated tool call surfaces the **cost-budget
    approval card** (ASK) and **blocks** opencode's tool until resolved — or, for
    a hard cap, **denies** it. (opencode genuinely waits on the permission reply.)
  - The cost the gate sees matches the web cost badge (both from
    `external_session_usage`).
  - Known limitation to confirm, not flag as a bug: enforcement can lag the
    in-flight turn by one message (the turn's cost posts on completion), same as
    claude/codex.

---

## 8. question.asked interactive input (foundation only)  [needs web: round-trip]

This PR lands only the client foundation (`reply_question` / `reject_question`),
so most of this is **regression/foundation** QA plus the manual round-trip
needed to **promote the follow-up**.

**Foundation (regression)**
- The client methods are unit-tested; no user-facing behavior changes yet. A
  model `question` tool call is **not** yet surfaced to the web by this PR.

**Round-trip to promote the follow-up (manual, blocks shipping the web loop)**
- Steps: get the model to call its `question` tool (multiple-choice). Capture the
  `question.asked` payload from server/opencode logs.
- Single-question check: confirm the AskUserQuestion web card renders the
  question + options (`_parse_questions_with_options` already speaks this shape),
  the user's choice maps to `[[label]]`, and `POST /question/{id}/reply` resolves
  it (→ `question.replied` → `session.idle`).
- **Multi-question check (the risky bit):** with 2+ questions, verify the web
  `ElicitationResult.content` (`{field:value}` map) maps back to opencode's
  **ordered** `answers:[[label],[label]]` correctly — confirm question/answer
  alignment, not just that a reply was accepted.
- TUI race: with the TUI attached, answering in the TUI must resolve/withdraw
  the web card (and vice-versa) — no double-answer.
- Only after these pass should the forwarder handler + server form-hook land.

---

## 9. Reasoning (P1)  [needs web: reasoning block render]

- Preconditions: a model that emits reasoning/thinking (e.g. a thinking-enabled
  model).
- Steps: send a prompt that triggers visible reasoning.
- Expected:
  - A **reasoning block** paints in the web chat as the model thinks
    (`external_output_reasoning_delta`, streamed as suffixes).
  - The block contains the full reasoning text, not duplicated/garbled (suffix
    accumulation — a repeated identical snapshot posts nothing new).
  - Reasoning is transient (codex contract): it is **not** persisted, so it is
    gone on web reload — acceptable, but confirm the final assistant message
    still persists.

## 10. Images  [needs web: image bubble render]

- Steps: (a) user attaches/pastes an image into an opencode turn; (b) if a model
  emits an image, exercise that too.
- Expected:
  - An image `file` part renders as an image bubble in the web chat
    (`input_image` for user, `output_image` for assistant; `image_url` carries
    the data URI / URL).
  - A non-image `file` part (e.g. a PDF) shows a short `[attachment: <name>]`
    text reference rather than vanishing.
  - Deduped: a file part that updates across snapshots posts once.

---

## Cross-cutting regression

- Backwards-compat: a vanilla opencode-native session with **no** MCP, **no**
  policies, default model still behaves exactly as before (streaming, interrupt
  via abort, idle/error lifecycle).
- Interrupt: cancel a running turn mid-stream → opencode aborts, web reflects it.
- No server-schema/wire changes beyond adding opencode to the text-preamble fork
  set — confirm other harnesses (codex-native especially) are unaffected.
