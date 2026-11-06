---
title: Pinned fork with pacing, send fixes and connection removal — plan
type: plan
status: draft
date: 2026-11-05
spec: ../specs/2026-11-05-fork-hardening-and-connection-removal.md
---

# Pinned fork with pacing, send fixes and connection removal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the LinkedIn MCP from a frozen, reviewed fork that paces every page load, keeps the two message-send fixes, notices upstream releases without applying them, and can remove chosen connections behind explicit approval.

**Architecture:** Ordinary commits on `feat/fork-hardening` in the fork. Pacing is one module called from the single navigation wrapper every LinkedIn page load already passes through. Connection removal is upstream PR #1294's commit `4644976`, re-homed onto three small local modules (errors, settings, audit) instead of the profile-editing packages it was stacked on. The running server is a separate detached checkout of a tag, started with `uv run --frozen`.

**Tech Stack:** Python 3.13, FastMCP, patchright (Playwright fork), uv, pytest, ruff, ty.

**Spec:** `.knowledge/specs/2026-11-05-fork-hardening-and-connection-removal.md` — read it first; this plan argues from it.

## Global Constraints

- Worktree: `~/.claude/worktrees/linkedin-mcp-server-fork-hardening`, branch `feat/fork-hardening`. Never edit `~/Code/linkedin-mcp-server` directly.
- Before every commit: `uv run ruff check . --fix`, `uv run ruff format .`, `uv run ty check`, then the task's tests. Full `uv run pytest -n 4` before the PR (AGENTS.md: at most `-n 4`).
- Every `uv` command uses `--frozen` after Task 1, so nothing re-resolves. Never run `uv lock` or `uv sync` without it except in Task 1.
- Tests write only to temporary directories (AGENTS.md). Anything that defaults to `~/.linkedin-mcp/` must be redirectable in tests.
- AGENTS.md mutation rule: before committing a test, break the behaviour it covers and watch it fail.
- Conventional commits `type(scope): Subject`; end messages with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- No LinkedIn calls during Tasks 1–7. Live use starts only in Task 9, after Alessandro has merged the PR.
- Pacing env names and defaults, verbatim: `LINKEDIN_NAV_MIN_GAP=20`, `LINKEDIN_NAV_JITTER=25`, `LINKEDIN_NAV_MAX_PER_HOUR=80`, `LINKEDIN_NAV_MAX_PER_DAY=240`; log `~/.linkedin-mcp/nav-times.json`; exempt paths `/login`, `/checkpoint`, `/uas/`, `/authwall`; only hosts ending in `linkedin.com`.
- Removal limits, verbatim from the port: `MAX_REMOVALS_PER_CALL = 5`; write gate `MCP_LINKEDIN_WRITE_ENABLED=true`; `confirm=true` required unless `dryRun=true`; en labels only.

## Review Focus

1. **The hourly cap hits mid-batch** (another tool used loads between the check and the batch): a removal that already clicked confirm must still record an audit entry and report the person as `UNVERIFIED`, never as `FAILED` with nothing logged. Test in Task 7.
2. **The same person twice in one call, or as a URL and a slug**: removed once, reported once. Test in Task 7.
3. **A corrupt or missing `nav-times.json`**: pacing treats it as empty and rewrites it; no tool call fails because of it. Test in Task 2.
4. **Pacing must not apply to non-LinkedIn hosts or the login/checkpoint flow**: a re-login during a removal must not be blocked by the cap. Test in Task 2.
5. **Upstream check offline or PyPI returning garbage**: no notice, no error, tool result unchanged. Test in Task 4.

---

### Task 1: Lock patchright to 1.64.4

**Files:**
- Modify: `uv.lock`, and `pyproject.toml` only if its patchright floor excludes 1.64.4

- [ ] **Step 1:** Run `uv lock --upgrade-package patchright==1.64.4`. Expected: lock resolves.
- [ ] **Step 2:** Run `git diff --stat uv.lock` and `git diff uv.lock | grep '^[-+]name\|^[-+]version'`. Expected: `patchright` 1.63.0 → 1.64.4, plus only packages patchright itself requires (likely none or `pyee`/`greenlet`). If anything else moved, revert and stop: report the list to the controller.
- [ ] **Step 3:** Run `uv run --frozen pytest -n 4 tests/test_browser_downgrade.py tests/test_dependency_floor.py`. Expected: PASS.
- [ ] **Step 4:** Commit `build(deps): Pin patchright 1.64.4 to match the live browser profile`. Save the full `git diff HEAD~1 -- uv.lock` to the PR description later (Task 8).

### Task 2: Navigation pacing

**Files:**
- Create: `linkedin_mcp_server/core/nav_pacing.py`
- Modify: `linkedin_mcp_server/core/proxy_errors.py` (`goto_reporting_proxy_errors`, line ~290), `linkedin_mcp_server/core/__init__.py` (export), `linkedin_mcp_server/error_handler.py` (surface the budget error's message)
- Test: `tests/test_nav_pacing.py`

**Interfaces:**
- Produces:
  - `class NavigationBudgetExceeded(RuntimeError)` with attribute `retry_after_seconds: int`
  - `NAV_LOG_PATH: Path` (module global, default `~/.linkedin-mcp/nav-times.json`; tests monkeypatch it)
  - `async def pace_navigation(url: str) -> None` — waits for the gap or raises `NavigationBudgetExceeded`
  - `def remaining_loads(now: float | None = None) -> int` — page loads left in the current rolling hour
  - `def loads_needed_fit(n: int) -> bool` — `remaining_loads() >= n`

- [ ] **Step 1: Write failing tests** in `tests/test_nav_pacing.py` (monkeypatch `NAV_LOG_PATH` to `tmp_path`, `asyncio.sleep` to a recorder, `random.uniform` to return the upper bound):
  - `test_first_load_does_not_wait` — empty log → no sleep, one timestamp written.
  - `test_second_load_waits_min_gap_plus_jitter` — one load at `t`, now `t+5` → sleeps `20 + 25 - 5 = 40` s.
  - `test_cap_raises_with_retry_after` — 30 timestamps in the last hour → `NavigationBudgetExceeded`, `retry_after_seconds` = seconds until the oldest expires.
  - `test_old_entries_expire` — 30 timestamps older than 3600 s → no raise.
  - `test_exempt_paths_and_foreign_hosts_skip` — `https://www.linkedin.com/login`, `/checkpoint/x`, `/uas/login`, `/authwall`, `https://example.com/` → no sleep, nothing written.
  - `test_corrupt_log_is_treated_as_empty` — log contains `not json` → no raise, file rewritten as a JSON list.
  - `test_env_overrides` — `LINKEDIN_NAV_MAX_PER_HOUR=2` with 2 recent → raises.
  - `test_remaining_loads` — 7 recent entries, default cap → `23`.
  - `test_goto_wrapper_paces_before_navigating` — patch `pace_navigation` with an `AsyncMock`, call `goto_reporting_proxy_errors(page, url)` with a fake page → mock awaited with `url` before `page.goto`.
- [ ] **Step 2:** `uv run --frozen pytest tests/test_nav_pacing.py -v` → FAIL (module missing).
- [ ] **Step 3:** Implement `nav_pacing.py` from `~/.claude/scripts/linkedin-mcp/patch_nav_throttle.py` (`HELPER` block), as a module, with `remaining_loads`; call `await pace_navigation(url)` as the first line of `goto_reporting_proxy_errors`. In `error_handler.py`, map `NavigationBudgetExceeded` to a tool error whose text is the exception message (follow how `RateLimitError` is mapped there).
- [ ] **Step 4:** Run the test file, then `uv run --frozen pytest -n 4 tests/linkedin/test_navigation.py tests/test_error_handler.py` → PASS.
- [ ] **Step 5:** Commit `feat(navigation): Pace LinkedIn page loads with an hourly budget`.

### Task 3: Profile compose-button resolution

**Files:**
- Modify: `linkedin_mcp_server/linkedin/message_sender.py` — `_PROFILE_MESSAGE_TARGET_JS` (starts line ~40)
- Test: `tests/test_profile_message_target_dom.py` (browser-DOM test, uses the `dom_page` fixture as in `tests/test_message_recipient_dom.py`)

- [ ] **Step 1: Write failing DOM tests** that `page.set_content(...)` then `page.evaluate(_PROFILE_MESSAGE_TARGET_JS)`:
  - `test_open_to_work_profile_resolves` — name `h1` in a `section` whose wrapper also holds an "Open to work" frame section, one compose anchor `/messaging/compose/?recipient=ACoAAB1` → status not `unresolved`, recipient `ACoAAB1`.
  - `test_two_anchors_same_recipient_resolve` — two visible anchors, both `recipient=ACoAAB1` (one as `profileUrn=urn:li:fsd_profile:ACoAAB1`) → resolves.
  - `test_two_anchors_different_recipients_unresolved` — `ACoAAB1` and `ACoAAB2` → `{status: 'unresolved'}` with a `reason` naming both.
  - `test_anchor_in_aside_ignored` — a second anchor inside `<aside>` for `ACoAAB2` → resolves to `ACoAAB1`.
- [ ] **Step 2:** Run → FAIL on the first three.
- [ ] **Step 3:** Apply `patch_open_to_work_send.py` then `patch_open_to_work_send_v2.py` (in `~/.claude/scripts/linkedin-mcp/`) to the JS by hand; the result is v2's `NEW_JS` + `NEW_CHECK`.
- [ ] **Step 4:** Run the new file plus `tests/linkedin/test_message_sender.py tests/test_message_recipient_dom.py` → PASS.
- [ ] **Step 5:** Commit `fix(messaging): Resolve the compose button on open-to-work profiles`.

### Task 4: Upstream release notice for the fork

**Files:**
- Modify: `linkedin_mcp_server/update_check.py`
- Test: `tests/test_update_check_fork.py`

**Interfaces:**
- Produces: `FORK_BASE_VERSION: str` (module constant, `"4.29.0"`); `pending_update_notice() -> str | None` keeps its name and signature.

- [ ] **Step 1: Write failing tests:**
  - `test_source_install_still_checks` — `_is_source_install` patched to `True`, env clean → `_check_disabled()` is `False`.
  - `test_notice_when_upstream_newer` — `_latest_known = "4.29.1"` → exactly `"Upstream mcp-server-linkedin 4.29.1 is available; this fork is pinned at 4.29.0. Review the upstream changes before updating; nothing has been installed."`
  - `test_no_notice_when_equal_or_older` — `"4.29.0"`, `"4.28.3"` → `None`.
  - `test_notice_never_mentions_install_commands` — notice text contains none of `uvx`, `@latest`, `pip install`, `docker pull`.
  - `test_fetch_failure_is_silent` — `_fetch_latest_from_pypi` returns `None`, no cache → `refresh_latest_version()` does not raise, `pending_update_notice()` is `None`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Remove the `_is_source_install()` early-out from `_check_disabled` (keep the env and CI switches); compare against `FORK_BASE_VERSION` instead of `__version__`; any newer release counts (drop the "two patches" rule); replace `_update_action()` usage with the fixed sentence above. Cache file and once-a-day TTL unchanged.
- [ ] **Step 4:** Run the new file plus `tests/test_daemon_forwarder.py` → PASS.
- [ ] **Step 5:** Commit `feat(update-check): Tell a pinned fork when upstream releases`.

### Task 5: Local errors, write switch and audit log for connections

**Files:**
- Create: `linkedin_mcp_server/connections/__init__.py`, `errors.py`, `settings.py`, `audit.py`
- Test: `tests/test_connections_support.py`

**Interfaces:**
- Produces:
  - `class ConnectionsErrorCode(StrEnum)`: `AUTHENTICATION_REQUIRED`, `SELECTOR_NOT_FOUND`, `CONFIRMATION_REQUIRED`, `WRITES_DISABLED`, `VERIFICATION_FAILED`, `BUDGET_EXCEEDED`
  - `class ConnectionsError(Exception)`: `__init__(self, code: ConnectionsErrorCode, message: str | None = None, **details: Any)`; `to_result() -> dict` returning `{"status": "ERROR", "error": str(code), "message": ..., "details": {...}}` (details only when non-empty) — same shape as upstream `ProfileEditError.to_result` on `pr-1294`. Default messages for `WRITES_DISABLED`: `"Writes are disabled. Start the server with MCP_LINKEDIN_WRITE_ENABLED=true."`
  - `def writes_enabled() -> bool` — `os.environ.get("MCP_LINKEDIN_WRITE_ENABLED", "").strip().lower() == "true"`
  - `class AuditLog`: `__init__(self, path: Path)`; `append(self, **fields: Any) -> None` writes one JSON line with an `at` UTC ISO timestamp, creates parent dirs, file mode `0o600`; `DEFAULT_AUDIT_PATH = ~/.linkedin-mcp/audit/connections.jsonl`
- [ ] **Step 1: Write failing tests:** `to_result` shape with and without details; `writes_enabled` for `true`, `TRUE`, `1` (→ False), unset; `AuditLog.append` twice → two parseable lines, mode `0o600`, in `tmp_path`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(connections): Add errors, write switch and audit log`.

### Task 6: Port the connection remover

**Files:**
- Create: `linkedin_mcp_server/linkedin/connection_removal.py` from `git show 4644976:linkedin_mcp_server/linkedin/connection_removal.py`
- Test: `tests/linkedin/test_connection_removal.py`

**Interfaces:**
- Consumes: Task 5 errors.
- Produces: `class ConnectionRemover(session: PageSession, navigator: PageNavigator)` with `async list_connections() -> dict`, `async remove(username: str, *, dry_run: bool, before_confirm: Callable[[str], None] | None = None) -> dict`; module constant `CONNECTIONS_URL = "https://www.linkedin.com/mynetwork/invite-connect/connections/"` (inline `profile_url` too). `remove` returns `status` in `NOT_CONNECTED | WOULD_REMOVE | REMOVED`, raises `ConnectionsError` otherwise.

- [ ] **Step 1: Write failing tests** with a mocked `PageSession`/`PageNavigator` (pattern: `tests/linkedin/test_connection_actions.py`):
  - `test_before_confirm_called_once_before_confirm_click` — order recorded: menu item click, `before_confirm(slug)`, confirm click.
  - `test_dry_run_never_clicks_remove` — `dry_run=True` → no menu-item click, `WOULD_REMOVE`.
  - `test_non_english_ui_refused` — `document.documentElement.lang == "it"` → `ConnectionsError(SELECTOR_NOT_FOUND)` before any click.
  - `test_slug_normalisation` — `"https://www.linkedin.com/in/Foo-Bar/"` and `"foo-bar"` → same slug `foo-bar`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Port the file: replace `ProfileEditError`/`ProfileEditErrorCode` with Task 5's types, `sel.CONNECTIONS_URL`/`sel.profile_url` with the local constant/function, add the `before_confirm` hook immediately before `await confirm.click()`. Read every line; anything that is not in the upstream commit is a review finding.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(connections): Port the connection remover from upstream #1294`.

### Task 7: `remove_connections` and `list_my_connections` tools

**Files:**
- Create: `linkedin_mcp_server/tools/connection_removal.py` from `git show 4644976:linkedin_mcp_server/tools/connection_removal.py`
- Modify: `linkedin_mcp_server/dependencies.py` (add `get_ready_connection_remover`, as in `4644976`), `linkedin_mcp_server/server.py` (register), `tests/fixtures/tool-contract/tools.json` and `tests/fixtures/policy-traces/v1/facade-contract.json` (as `4644976` does), `tests/linkedin/test_facade_contracts.py`, `tests/linkedin/test_policy_traces.py`
- Test: `tests/test_connection_removal_tools.py` (port from `4644976`, adapt imports)

**Interfaces:**
- Consumes: Task 2 `remaining_loads`, `NavigationBudgetExceeded`; Task 5 `ConnectionsError`, `writes_enabled`, `AuditLog`, `DEFAULT_AUDIT_PATH`; Task 6 `ConnectionRemover`.
- Produces: tools `list_my_connections()` and `remove_connections(usernames: list[str], confirm: bool = False, dryRun: bool = False)`; result `{"dryRun", "results": [{"username", "status", "verified", ...}], "allDone"}`; statuses add `UNVERIFIED` and `NOT_ATTEMPTED`.

- [ ] **Step 1: Write failing tests** (keep the four upstream ones; add):
  - `test_refuses_when_budget_short` — `remaining_loads` patched to `3`, real call for 2 people (needs 4) → `BUDGET_EXCEEDED` with `details.canTakeNow == 1`, browser never acquired.
  - `test_dry_run_budget_is_one_per_person` — `remaining_loads` = 2, `dryRun=True` with 2 people → proceeds.
  - `test_attempt_audited_before_confirm` — remover's `before_confirm` callback writes an `attempted` audit line; on success a `REMOVED` line follows (two lines for one person).
  - `test_cap_after_confirm_reports_unverified` — remover raises `NavigationBudgetExceeded` on the verify reload → result `UNVERIFIED`, audit has the `attempted` line, loop stops, later people `NOT_ATTEMPTED`.
  - `test_duplicates_collapsed` — `["foo", "https://www.linkedin.com/in/foo/"]` → remover called once, one result.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Port the tool; swap the profile-edit store/settings/errors for Task 5's; dedupe `usernames` by normalised slug first; compute needed loads (`1` per person for dry run, `2` for real) and refuse with `BUDGET_EXCEEDED` before `get_ready_connection_remover` when `remaining_loads()` is short; drop the port's `PACING_SECONDS` delay (navigation pacing replaces it); pass `before_confirm` that appends `{"tool": "remove_connections", "username": slug, "result": "attempted"}`; catch `NavigationBudgetExceeded` after an attempt as `UNVERIFIED`.
- [ ] **Step 4:** Run the file plus `tests/linkedin/test_facade_contracts.py tests/linkedin/test_policy_traces.py`, then the full suite `uv run --frozen pytest -n 4` → PASS. If changelog-fragment checks fail, add fragments under `changelog.d/` in the repo's format.
- [ ] **Step 5:** Commit `feat(connections): Add approval-gated connection removal tools`.

### Task 8: Draft PR on the fork

- [ ] **Step 1:** `git push -u origin feat/fork-hardening`.
- [ ] **Step 2:** `gh pr create --draft --repo alessandrofacchin/linkedin-mcp-server --base main` with a short body (What / How / Review focus / Deploy / Validation), the full `uv.lock` diff from Task 1 in a collapsed block, and the Review Focus list above.
- [ ] **Step 3:** Run an adversarial review of the whole branch (fresh reviewer, focus: anything ported from `4644976` that differs from upstream; any network call not to `linkedin.com` or `pypi.org`). Fix findings, push.
- [ ] **Step 4:** Mark ready and hand the PR link to Alessandro. **Stop.** He reviews and merges; agents do not.

### Task 9: Deploy the pinned server and first removals (after Alessandro merges)

- [ ] **Step 1:** In `~/Code/linkedin-mcp-server`: `git fetch origin && git tag fork-v4.29.0.1 origin/main && git push origin fork-v4.29.0.1`; `git worktree add --detach ~/.linkedin-mcp/fork fork-v4.29.0.1`.
- [ ] **Step 2:** `uv --directory ~/.linkedin-mcp/fork sync --frozen --no-dev`. Expected: installs, no resolution.
- [ ] **Step 3:** Show Alessandro the exact config change and get his go. Then set the `linkedin` server in `~/.claude.json` and in Claude desktop's config to `command: /Users/alessandrofacchin/.local/bin/uv`, `args: ["run", "--frozen", "--no-dev", "--directory", "/Users/alessandrofacchin/.linkedin-mcp/fork", "mcp-server-linkedin"]`, env `UV_HTTP_TIMEOUT=300`, `MCP_LINKEDIN_WRITE_ENABLED=true`, `TOOL_TIMEOUT=660`. Raise the client-side MCP tool timeout to at least 660 s where the client supports it (Claude Code: check its docs for the setting; desktop: note if it has none).
- [ ] **Step 4:** Ask Alessandro to quit Claude desktop and restart Claude Code; confirm no `mcp-server-linkedin@latest` process remains (`ps aux | grep mcp-server-linkedin`). Then call `get_session_status` → logged in, browser started without a downgrade refusal.
- [ ] **Step 5:** `remove_connections(dryRun=true)` for the 22 `remove` rows of `~/Desktop/recent-connections-triage.csv`, in batches as the budget allows. Report each `WOULD_REMOVE` / `NOT_CONNECTED` / error.
- [ ] **Step 6:** For each batch of up to 5: show Alessandro the names, get his explicit go for that batch, call with `confirm=true`, report results. Stop at the first `FAILED` or `UNVERIFIED`.
- [ ] **Step 7:** Mark the removed rows in the CSV (`removed on <date>`), and move `~/.claude/scripts/linkedin-mcp/` to `~/.claude/scripts/linkedin-mcp/retired/` with a one-line README saying they are superseded by the fork.
