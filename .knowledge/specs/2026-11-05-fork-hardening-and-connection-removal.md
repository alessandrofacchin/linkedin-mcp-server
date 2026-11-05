---
title: Pinned fork with pacing, send fixes and connection removal
type: spec
status: draft
date: 2026-11-05
owner: Alessandro Facchin
related:
  - upstream: https://github.com/stickerdaniel/linkedin-mcp-server
  - upstream-pr: https://github.com/stickerdaniel/linkedin-mcp-server/pull/1294
---

# Pinned fork with pacing, send fixes and connection removal

## Why

Alessandro runs this MCP against his personal LinkedIn account. Until now it ran
as `uvx mcp-server-linkedin@latest`, with three local fixes applied by scripts
that edit the uv cache copy in place (`~/.claude/scripts/linkedin-mcp/`). On
2026-11-05 `@latest` resolved a fresh, unpatched copy; the pacing fix was gone,
a bulk search ran at full speed, and LinkedIn paused search on the account.

He wants two things, in his words: the fork stays **frozen**, with **just a
notice when he uses the MCP** that upstream has moved, because he does not want
to **risk any malware injection**. Nothing he has not seen should run against
his session, and that includes Python dependencies, not just this repository.

He also wants to **remove connections** through the MCP: the immediate job is 22
people marked `remove` in `~/Desktop/recent-connections-triage.csv`, with more to
come as that triage continues.

## Goals

1. The MCP runs from this fork at a pinned commit, with dependencies installed
   exactly as `uv.lock` records them. Nothing re-resolves at launch.
2. The three cache patches become ordinary commits, each with a test.
3. A notice in tool results when upstream has a newer release than the fork's
   base, telling him to review; never downloading or applying anything.
4. An approval-gated `remove_connections` tool (plus read-only
   `list_my_connections` if it comes with the port at no extra cost).
5. Claude Code (`~/.claude.json`) and Claude desktop
   (`~/Library/Application Support/Claude/claude_desktop_config.json`) both point
   at the fork.

## Non-goals

- Any profile-editing code from upstream PRs #1209 or #1293. #1294 is stacked
  on them; only its `feat(connections)` commit (`4644976`) is ported.
- Contributing back upstream. Possible later, not part of this.
- Automating the triage itself (searches, CSV). Separate work.

## Anti-goals (failure even if it works)

- Any path by which code or a dependency version he has not reviewed can run:
  `@latest`, `uvx --from git+…` (uvx ignores `uv.lock`), `uv sync` without
  `--frozen`/`--locked`, a background self-update.
- A removal that runs without his explicit approval of the exact list, or that
  removes someone not on it.
- Burst traffic. The account was already flagged once today.
- Losing the logged-in session (browser profile at `~/.linkedin-mcp/profile`).

## What exists

- Fork: `alessandrofacchin/linkedin-mcp-server` (public, forked 2026-11-05),
  clone at `~/Code/linkedin-mcp-server`, `upstream` remote set. Base:
  upstream `main` at `8c3bb5a`, release `v4.29.0`. No `dev` branch on the fork:
  PRs target the fork's `main`.
- Cache patches to port (read them; they are short):
  - `patch_nav_throttle.py`: paces every `linkedin.com` page load in
    `core/proxy_errors.py` (`_goto_within_budget` call site). Min gap
    `LINKEDIN_NAV_MIN_GAP` (default 20 s) plus random jitter
    `LINKEDIN_NAV_JITTER` (default 25 s); hard cap `LINKEDIN_NAV_MAX_PER_HOUR`
    (default 30) that raises instead of waiting; login/checkpoint paths exempt;
    timestamps persisted in `~/.linkedin-mcp/nav-times.json` so the budget is
    shared across processes.
  - `patch_open_to_work_send.py` then `patch_open_to_work_send_v2.py` (applied in
    that order): in `linkedin/message_sender.py`, the profile compose-button
    resolver refuses profiles with an "Open to work" frame or several compose
    anchors for the same person. The fix resolves by the first visible heading's
    section and accepts several anchors when they all name one recipient.
    Checked 2026-11-05: upstream `main` still has the original code, so both are
    still needed.
- Update notice: `linkedin_mcp_server/update_check.py` already appends a once-a-day
  notice to tool results for PyPI installs, but switches itself off for source
  installs (`_is_source_install`), and its wording assumes `uvx …@latest`.
- Removal commit `4644976` (on `upstream pull/1294/head`, fetched locally as
  `pr-1294`): `linkedin/connection_removal.py` (317 lines),
  `tools/connection_removal.py` (183), tests, tool-contract fixtures. Gated by
  `MCP_LINKEDIN_WRITE_ENABLED=true` and `confirm=true`; `dryRun=true`; at most 5
  people per call; stops at the first failure; verifies each removal by
  reloading; writes an audit entry per outcome. Live-tested by its author on 28
  removals, en UI only. Its only imports from the stacked PRs:
  `profile_selectors.CONNECTIONS_URL`, `profile_selectors.profile_url`,
  `ProfileEditError`/`ProfileEditErrorCode`, `settings.writes_enabled`,
  `settings.edits_root`, `ProfileEditStore.audit`.

## Decided

- **Frozen fork, notice only** (his decision). Updating is something he
  triggers; the agent then fetches upstream, shows the diff, and merges only
  after he has read it.
- **The notice** (my reading of his ask): reuse `update_check.py`, enabled for
  this fork. Compare upstream's latest release against the version the fork is
  based on; when newer, add one line such as "Upstream v4.31.0 is available;
  this fork is pinned at v4.29.0. Review before updating." No install command
  in the text. Network check stays at most once a day, cached; if offline, no
  notice and no error.
- **Removal port** (my call): bring `4644976` across and replace the five
  borrowed pieces with small local equivalents scoped to connections (a
  connections error type, a write-enabled setting read from
  `MCP_LINKEDIN_WRITE_ENABLED`, an append-only audit log under
  `~/.linkedin-mcp/`, the two selectors). Keep its gates, limits and
  verification as they are. Every line of the port is reviewed, not just
  copied, since it comes from an outside contributor.
- **Removal pacing**: removal page loads go through the same navigation pacing
  as everything else (goal 2), so the per-hour budget covers them too.
- **How it runs** (my call): `uv run --frozen` (or `--locked`) from a checkout
  pinned to a tagged commit on the fork, kept separate from the clone used for
  development, so editing or switching branches never changes what the running
  server executes. Write access enabled for this server via
  `MCP_LINKEDIN_WRITE_ENABLED=true` in both client configs; `confirm=true` per
  call is still required.
- **Browser driver version** (his decision, 2026-11-05): upstream's `uv.lock`
  pins `patchright 1.63.0`, but his profile at `~/.linkedin-mcp/profile` was
  last written by Chromium `156.0.8078.4` (`chromium-1248`, installed by
  patchright 1.64.1; the server has been running 1.64.4). `browser_downgrade`
  refuses an older browser on that profile. The fork's lock is bumped to
  `patchright==1.64.4`, the version already trusted with his session, via
  `uv lock --upgrade-package patchright==1.64.4`. Only patchright and whatever
  it strictly requires may change; the lockfile diff is shown to him before the
  server runs from the fork. This is the first commit.
- **Delivery**: one draft PR on the fork; per repo `AGENTS.md`: conventional
  commits, `uv run ruff check`, `uv run ruff format`, `uv run ty check`,
  `uv run pytest`. Alessandro merges it; agents do not.
- **First real use**: dry run on all 22, then real removals in batches of up to
  5, each batch shown to him and approved before it runs. The 22 are final: the
  cold-DM rule can only turn a person into a remove, never save one, so the
  pending DM check does not affect them.
- **UI language** (answered by the session): his LinkedIn UI is English; every
  page read on 2026-11-05 ("Message", "mutual connections", "Connected on")
  came back in English. The port's en-only labels are enough; other locales
  stay refused.
- **Removal vs pacing** (my call; the port's 5-per-call limit and the shared
  pacing cannot both hold under the default 180 s tool timeout):
  - `remove_connections` reserves its page loads up front: it computes the loads
    the batch needs (dry run: 1 per person; removal: 2 per person, find and
    verify) and refuses the whole call, before touching the browser, if the
    hourly budget cannot cover them. The verify reload can then never be the
    load that hits the cap.
  - It carries its own tool timeout, long enough for a full batch at maximum
    jitter plus margin (about 10 minutes); the client-side timeout in both
    configs is raised to match.
  - The audit entry for a person is written before the confirm click (as
    "attempted") and updated after verification, so a cancellation mid-step
    still leaves a record.
  - Batches shrink below 5 automatically when the remaining budget only covers
    fewer people; the tool says how many it can take this hour.

## Left to the builder

- Exact module layout and names for the local replacements.
- Whether `list_my_connections` comes along (only if no extra borrowed code).
- Test shapes, within `AGENTS.md`'s testing rules (mutate the behaviour and
  watch the test fail; browser-DOM tests where extractor JS needs real DOM).
- Whether tool-contract fixtures are regenerated or hand-edited.
- Where the pinned deploy checkout lives and how a release is tagged on the
  fork.
- What happens to `~/.claude/scripts/linkedin-mcp/` once ported (keeping them
  as historical reference is fine; they must no longer be run).
