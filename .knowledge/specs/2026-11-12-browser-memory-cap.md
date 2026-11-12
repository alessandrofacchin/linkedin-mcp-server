---
title: Browser memory cap
type: spec
status: implemented
date: 2026-11-12
owner: Alessandro Facchin
---

# Browser memory cap

## Why

On 2026-11-12 the LinkedIn MCP's Chrome for Testing processes held about 40 GB
during a run of serial `search_people` calls. Calls were already serialized
(in-process lock plus profile lease), so serialization was not the missing
guard. The processes were gone before they could be inspected, so the cause is
unconfirmed. Alessandro asked for a hard ceiling: never above 10 GB.

## What it does

- `process_tree.browser_memory_bytes()` sums the resident memory of every process
  carrying `LINKEDIN_MCP_BROWSER_PROCESS_MARKER`, whatever its value: every
  browser any server of this account launched, not only this process's.
- Before each browser call (`SequentialToolExecutionMiddleware`), over the limit
  closes this server's browser and measures again. Still over: the call is
  refused with `browser_unavailable` and never starts.
- During the call, a watcher measures every 2 s. Over the limit: it closes the
  browser, and the call fails with `browser_unavailable`. A write already cut
  short with `outcome_unknown` keeps that code, so nobody retries a send that
  may have happened.
- Limit: `LINKEDIN_BROWSER_MAX_MEMORY_GB`, default 10.

## Trade-offs

- RSS counts pages shared between Chromium processes once per process, so the
  sum overstates what Activity Monitor shows. The cap trips early, never late.
- A scan that cannot answer (`ps` missing or timing out) lets the call run.
  Blocking every LinkedIn tool on a failed `ps` was judged worse.
- Closing this server's browser when another process holds the memory costs a
  reopen, and a reopen validates `/feed/`: one extra LinkedIn page load.
- The pre-call check runs before the abandoned-call check, so nothing awaits
  between that check and the tool body.
- Browsers whose owner died are measured but never killed: the server cannot
  prove it owns them. The refusal names the usage so a person can end them.
