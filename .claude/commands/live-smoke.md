---
description: Pre-merge live smoke of the current branch via Chrome MCP — rebuild web, grant overrides, pre-create data, then drive the feature in the real app (the owner does the Keycloak login)
allowed-tools: Bash, Read, Glob, Grep
---

Run a **pre-merge live smoke** of the current branch against the running dev stack (Docker Desktop, app at http://localhost, org **AHT**). The owner does the Keycloak login (`demo` / `Demo-Password-1`); you drive via the **Claude-in-Chrome MCP**. This is the recurring ritual whose mechanics are non-obvious — follow them exactly.

## 1. Rebuild the changed image(s)
The web image is a **baked `vite preview` build** with no source mount — a front-end change isn't live until rebuilt. Rebuild only what changed:

```
docker compose --env-file .env -f infra/compose/compose.yml -f infra/compose/compose.s.yml up -d --build web
```

Add `api worker beat` to the rebuild **iff this branch touched the backend** (`apps/api/**` / a migration). Confirm the stack is up first: `docker ps --format '{{.Names}}\t{{.Status}}' | grep easysynq`. If `keycloak` was recreated, the demo user is wiped → `just demo-user` (and `just seed-personas` for the SoD trio).

## 2. Grant overrides + see the data
The `demo` login holds NO content keys; grant SYSTEM overrides to **all org-AHT users** (dodges the re-created-JIT-row trap) and print the docs/DCRs the smoke needs. Edit `scripts/grant-overrides.py`'s `KEYS`/`ORG` for the slice under test, then:

```
MSYS_NO_PATHCONV=1 docker compose --env-file .env -f infra/compose/compose.yml -f infra/compose/compose.s.yml exec -T worker sh -c "cd /app; uv run python -" < scripts/grant-overrides.py
```

`MSYS_NO_PATHCONV=1` shields the container-internal `/app` path from Git-Bash mangling (the `exit 127` trap). For a write/seed beyond authz overrides, pipe a service-layer snippet the same way (call the real services so WORM/SoD invariants hold — never raw SQL into the vault). ⚠ A second SoD principal is needed for any author≠releaser flow (e.g. a DCR/objective implement); a candidate-pool role-holder (not a SYSTEM override) is needed for any `/tasks` approval.

## 3. Drive via Chrome MCP — the mechanics that bite
- **Connect:** `list_connected_browsers` → `tabs_context_mcp{createIfEmpty:true}` → `navigate` to `http://localhost`. Use **http://localhost ONLY** (PKCE needs a secure context). A valid Keycloak SSO session re-auths silently; otherwise ask the owner to log in.
- ⚠ **Client-side nav only.** A full `navigate` to a deep route reloads → silent SSO → lands at **Home** (the deep-link is lost). Move between routes by clicking nav/links **in the page**.
- ⚠ **`computer left_click ref=…` is unreliable off some pages** (nav links, board cards, a Mantine SegmentedControl's hidden radio). When a click no-ops, drive via `javascript_tool`: `document.querySelector('a[href="/x"]').click()` for client-side nav; `[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='X').click()` for a button; `radio.click()` for a SegmentedControl option; set a controlled input via the native setter + an `input` event.
- ⚠ **Mantine Drawer/Modal portals are invisible to `find` / `get_page_text` and often `read_page`.** Verify portal content with a **screenshot** — and if screenshots intermittently time out, fall back to `javascript_tool` reading `document.querySelector('[role="dialog"]').innerText` or counting `[role="dialog"]`.
- ⚠ **The CapaBoardPage card-click does NOT open its drawer through Chrome MCP** (computer-click, JS `.click()`, and invoking its React `onClick` all no-op) — verify CAPA-drawer affordances another way, or rely on the unit tests. The **MR detail page** (`/management-reviews/:id`, a full route) works for inline affordances.
- **Verify the backend, not just the UI:** confirm a write landed via the register/list text (`get_page_text` sees the main page) or a `scripts/grant-overrides.py`-style read; for a created resource, confirm its real fields (state, source link).

## 4. Report
List what was driven and the observed result per flow (created id, state transition, deep-link). Call out anything verified-live vs. covered-by-tests-only (e.g. a flow blocked by the tooling notes above). Leave dev artifacts in place (harmless; cleared by `just down -v`). Recommend the squash-merge only after the core flows pass.
