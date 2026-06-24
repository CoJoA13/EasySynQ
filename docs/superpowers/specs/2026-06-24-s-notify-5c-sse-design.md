# S-notify-5c — SSE for the notification bell (design)

> Notification family (doc 10 §9, R53/R54), slice 5c — the **last** Notification-family slice. The third
> of the three slice-5 subsystems (5a awareness events → 5b Health panel + Config tab → **5c SSE**),
> owner-confirmed split + sequencing. Replaces the slice-2 60s bell poll with server-push so the unread
> badge updates in near-real-time. **BE + FE + 1 Caddy line. NO migration (head stays 0066). NO new
> permission key (authenticated-self, catalog stays 102). NO WORM touch.**

- **Date:** 2026-06-24
- **Depends on:** S-notify-1 (the `notification` table + the `recipient_user_id`/`created_at`/`read_at`
  columns + the `ix_notification_recipient_unread` index), S-notify-fe (the slice-2 bell: `useNotificationCount`
  60s poll, the `["notifications"]` query-key prefix, `NotificationBell`, the `lib/auth` in-memory bearer +
  `lib/api` fetch-with-bearer), the existing async Redis client (`redis_client.py`), the existing Celery
  Beat schedule (`tasks/app.py`) + the `tasks/__init__.py` registration discipline, and Caddy's already-reserved
  `/events` route token (`infra/compose/caddy/Caddyfile`).
- **Migration head:** `0066` → **`0066` (unchanged)**.
- **Validated:** 2026-06-24 — a 5-lens code-anchor + adversarial-refute workflow (15 agents) verified every
  code anchor and found **0 critical / 3 major / 5 minor / 7 nit (0 refuted)**, all folded below. The three
  majors: **(1)** the original `created_at`-watermark de-dup silently+permanently MISSED notifications
  enqueued inside a long/SERIALIZABLE caller txn (`created_at = func.now() = transaction_timestamp()` = the
  txn *start*, so a row can predate its own commit by ≫GUARD; the ack sweep mints an org's whole DOC_ACK
  batch in one multi-second txn) → **redesigned to de-dup on the immutable `notification.id` in Redis** over
  a generous look-back, immune to the txn-start clock (§4); **(2)** mounting the stream hook fires an
  unhandled `/notifications/stream` fetch that breaks the 4 existing bell/shell test suites under MSW
  `onUnhandledRequest:'error'` → a **base MSW handler + an injectable open** (§8); **(3)** "reset backoff on
  open" + the on-connect nudge → a ~1s reconnect+refetch **storm** on accept-then-close → **reset only after
  a healthy-duration + a minimum reconnect floor** (§7). The watermark-window arithmetic, first-run/Redis-flush
  re-init, auth/channel isolation (R32 nudge-only), the DB-session-leak fix, and every FE streaming primitive
  were confirmed sound.

---

## 1. Goal & context

The notification bell badge (`features/notifications/hooks.ts::useNotificationCount`) learns about new
notifications by **polling** `GET /notifications?unread_only=true&limit=100` every `60_000` ms. So a
`task.assigned`, a `doc.released` awareness fan-out, or a `system.email_delivery_failed` admin alert can sit
up to **60 seconds** before the badge moves. doc 10 §9 always intended server-push (SSE) for awareness; 5c
delivers it.

The hard part is **cross-process**: notification rows are INSERTed by the **worker/beat** process
(`services/notifications/dispatch.py` — `_enqueue_one` for tasks via a SAVEPOINT, `enqueue_awareness_one`
for awareness fan-out, `emit_system_notification` for admin alerts), but the SSE stream is served by the
**api** process — and the api runs under **gunicorn with 2 `UvicornWorker` processes per container** (1
container in the S profile, 2 in M), so a connected user's stream may live on a *different* worker than any
shared in-process state. A cross-process bus is therefore **required**, not optional. Redis is already in
the stack (D4) and the api process already has an async client (`redis_client.py`,
`redis.asyncio.from_url`), so the bus is **Redis pub/sub — no new component**.

## 2. Scope

**In scope:**

**Backend — the publisher (Celery Beat, no migration):**
- `services/notifications/pubsub.py` — pure-ish helpers: `channel_for_user(user_id) -> str` (the per-user
  Redis channel), `dedup_key(notification_id) -> str` (the per-notification de-dup key),
  `publish_user_nudge(redis, user_id)` (one `PUBLISH`), and `sweep_and_publish(session, redis) -> int` (the
  look-back scan + id-de-dup; returns # of distinct users nudged). §4.
- The Celery task `easysynq.notifications.pubsub_sweep` is **co-located in the existing
  `tasks/notifications.py`** (alongside `outbox_drain`/`digest_sweep`/`timer_sweep`/`awareness_fanout` — no
  new task module, so no new `tasks/__init__.py` import line to forget; the existing notifications import
  already registers it). It builds its **own** per-run async engine (`create_async_engine(settings.database_url)`
  + `async_sessionmaker(expire_on_commit=False)` + `engine.dispose()` in a `finally` — the established
  worker fresh-engine idiom, NOT the api-process `get_sessionmaker()` global; mirrors `_run_awareness_fanout`)
  + a short-lived `redis_client(decode_responses=True)` (`aclose`'d in a `finally`), and is wired into
  `tasks/app.py` `beat_schedule` as `notifications-pubsub-sweep` at
  `float(_settings.notify_stream_sweep_interval_seconds)` (default **10 s**). A registration test asserts
  `"easysynq.notifications.pubsub_sweep" in app.tasks` (the load-bearing rule: an unregistered task name is
  `.delay`'d into the void).

**Backend — the subscriber (the SSE endpoint, no migration):**
- `api/notifications.py` — a new route `GET /notifications/stream` (full path `/api/v1/notifications/stream`,
  same router/prefix, tag `notifications`) returning a `StreamingResponse(..., media_type="text/event-stream")`.
- `api/_sse.py` — a thin SSE helper: the pure frame formatter `sse_event(event, data) -> str` and the
  heartbeat/disconnect-aware async generator that subscribes to the caller's Redis channel and yields
  `notify` frames + `: ping` heartbeats. §5.
- `auth/dependencies.py` — refactor `get_current_user`'s body into a reusable `resolve_current_user(request,
  jwks, session) -> AppUser` so the SSE route can authenticate with a **short-lived** session that closes
  **before** streaming begins (the no-DB-session-held-for-the-connection crux, §5). The existing
  `get_current_user` dependency becomes a thin wrapper over it — byte-identical behaviour, regression-backed
  by the existing auth tests.
- `config.py` — `notify_stream_sweep_interval_seconds: int = 10` (env `NOTIFY_STREAM_SWEEP_INTERVAL_SECONDS`),
  `float()`-wrapped at the `beat_schedule` use-site — matching the existing **int-typed** interval settings
  (`mirror_scan_interval_seconds: int = 3600`, `blob_verify_interval_seconds`, `restore_test_interval_seconds`)
  which `tasks/app.py` already `float()`-wraps.
- `openapi.yaml` — document the `GET /notifications/stream` path (`200` → `text/event-stream`; `401`).

**Frontend — the consumer (the fetch-stream reader):**
- `features/notifications/stream.ts` — a pure `parseSseFrame(frame) -> {event, data}` + `openNotificationStream(
  token, onNudge, signal)` (a `fetch` + `ReadableStream` reader carrying `Authorization: Bearer <token>`).
- `features/notifications/hooks.ts` — `useNotificationStream()` (opens the stream, reconnects with capped
  backoff, `AbortController` cleanup, `qc.invalidateQueries({queryKey:["notifications"]})` on each `notify`);
  and `useNotificationCount`'s `refetchInterval: 60_000 → 300_000` (the 5-min backstop).
- `features/notifications/NotificationBell.tsx` — mount `useNotificationStream()` (renders once, persists
  across navigation, under `AuthProvider` + `QueryClientProvider`).

**Infra:**
- `infra/compose/caddy/Caddyfile` — a dedicated SSE matcher `@sse path /api/v1/notifications/stream` →
  `reverse_proxy api:8000 { flush_interval -1 }`, placed **before** `@api`; drop the now-dead `/events`
  token from `@api` (no FastAPI route ever served bare `/events` — everything is under `/api/v1`). §6.

**Out of scope (named residuals, §11):**
- **No full-payload push** — the SSE event is a content-free nudge; the authoritative count/list come from
  the existing `GET /notifications` reads (owner: nudge-only, R32-safe).
- **No removal of the backstop poll** — kept as a long-interval (5-min) safety net (owner: keep a backstop).
- **No publish-on-insert / after-commit hook** — the Beat sweep is the publisher (owner: Beat sweep, not an
  insert-site hook; zero changes to the load-bearing SAVEPOINT outbox path).
- **No `pushed_at` stamp column** — the de-dup state is per-notification Redis keys (owner: migration-free).
- **No SSE for the admin Health panel, the task inbox, or any register** — the bell unread-count only.
- The slice-4 timer-sweep claim-threshold filter (the `remind_2_sent_at IS NULL` tautology / 5a's unused
  `_pending_event_ids(now)`) — unrelated subsystem, **not** folded in.

## 3. Architecture

```
 worker/beat                          Redis                              api (gunicorn, 2 UvicornWorkers)            browser
 ───────────                          ─────                              ────────────────────────────────            ───────
 INSERT notification (unchanged)
        │
 Beat notifications-pubsub-sweep (~10s)
   sweep_and_publish(session, redis):
     SELECT id, recipient_user_id
       WHERE read_at IS NULL AND created_at > db_now-LOOKBACK
     for each id first-seen (Redis SET NX EX):  ──PUBLISH notify:user:{uid}──▶  (fan-out to every subscriber across BOTH workers)
                                                                                │
                                                                   GET /api/v1/notifications/stream
                                                                   (Depends short-lived auth → caller.id;
                                                                    session CLOSED before streaming)
                                                                   event_gen():  SUBSCRIBE notify:user:{caller.id}
                                                                     • yield initial `event: notify`  (on-connect sync)
                                                                     • on Redis msg → yield `event: notify`  ──────────▶  fetch ReadableStream reader
                                                                     • every ~20s idle → yield `: ping`                    parseSseFrame → "notify"
                                                                     • request.is_disconnected() → unsubscribe+close              │
                                                                                                                    qc.invalidateQueries(["notifications"])
                                                                                                                                  │
                                                                                                          GET /notifications?unread_only → authoritative count → badge
 backstop: useNotificationCount refetchInterval 300_000  +  React-Query refetchOnWindowFocus/Reconnect (default-on for the count query)
```

The nudge is **content-free** — it only says "your notifications changed; re-fetch." The count stays
single-sourced in the existing read path (no count drift; nothing sensitive on the wire — R32). The sweep is
**read-only** (no row mutation, no `FOR UPDATE`/advisory lock; the de-dup state lives in Redis) — the
simplest of all the notification Beat tasks.

## 4. Backend — the publisher (Beat sweep → Redis)

**`services/notifications/pubsub.py`:**

```python
LOOKBACK_SECONDS = 120        # the re-scan window. Must EXCEED the longest plausible caller txn, because
                              # created_at = func.now() = transaction_timestamp() (txn START), so a row
                              # enqueued inside a long/SERIALIZABLE caller txn carries a created_at far
                              # behind its eventual COMMIT (see "Why de-dup on id, not a watermark"). 120s
                              # is hugely generous at D1 single-org scale; the 5-min backstop is the net.
DEDUP_TTL_SECONDS = 240       # 2 × LOOKBACK — a per-notification de-dup key outlives the scan window, then
                              # expires (the id has aged out of the window → it can't be re-scanned/re-nudged).

def channel_for_user(user_id) -> str:
    return f"notify:user:{user_id}"

def dedup_key(notification_id) -> str:
    return f"notify:pushed:{notification_id}"

async def publish_user_nudge(redis, user_id) -> None:
    await redis.publish(channel_for_user(user_id), "1")   # payload is irrelevant — a content-free nudge

async def sweep_and_publish(session, redis) -> int:
    db_now = (await session.execute(select(func.now()))).scalar_one()
    rows = (await session.execute(
        select(Notification.id, Notification.recipient_user_id)
        .where(Notification.read_at.is_(None),
               Notification.created_at > db_now - timedelta(seconds=LOOKBACK_SECONDS))
    )).all()
    users: set = set()
    for nid, uid in rows:
        # SET key 1 NX EX → truthy ONLY the first time this notification id is seen → nudge that user once.
        if await redis.set(dedup_key(nid), "1", nx=True, ex=DEDUP_TTL_SECONDS):
            users.add(uid)
    for uid in users:
        await publish_user_nudge(redis, uid)
    return len(users)
```

**Why de-dup on the notification id, not a `created_at` watermark (the validated fix):**
- `Notification.created_at` is `server_default=func.now()`, and in PostgreSQL `now() = transaction_timestamp()`
  — the **start** instant of the INSERTing transaction, identical for every row in that txn. Task
  notifications are INSERTed via a SAVEPOINT *inside* the caller's transaction
  (`dispatch.py::enqueue_task_notifications`), and several callers are long/SERIALIZABLE/retried — the ack
  sweep (`ack/sweep.py`) mints an org's entire DOC_ACK batch in **one** transaction with a single late
  commit, and `_cutover` (release) is SERIALIZABLE. So a row can carry a `created_at` many seconds **before**
  the commit that makes it visible.
- A monotone watermark on `created_at` therefore **silently and permanently misses** such a row: by the time
  the row's txn commits, the watermark has advanced past its (txn-start) `created_at`, so it never falls in a
  future window — regressing the highest-volume awareness source from 60 s to the 5-min backstop (the
  validation MAJOR finding, confirmed independently by two lenses).
- Keying de-dup on the **immutable `notification.id`** is immune to the clock: the id is assigned at INSERT,
  and `SET … NX` nudges each row exactly once the first time it becomes *visible to the sweep*, regardless of
  its `created_at`. `LOOKBACK` only bounds *how far back to look* for late-committing rows; 120 s vastly
  exceeds any realistic caller txn at D1 single-org scale, and the 5-min backstop catches the pathological
  `>LOOKBACK` txn.
- **Over-publish is harmless; under-publish self-heals.** A Redis flush (`redis:7` is ephemeral —
  `--save "" --appendonly no`) drops the de-dup keys → each row still in the LOOKBACK window is re-nudged
  once (one redundant refetch/user), and the dropped SSE subscriptions force an FE reconnect whose
  on-connect nudge re-syncs. The only true miss is a caller txn outliving `LOOKBACK` — bounded by the
  backstop. **Standing-unread users are NOT re-nudged each tick** (id de-dup), so there is no per-tick nudge
  storm — the property the watermark was meant to give, now provided by the id key.
- **Read-only + concurrency-safe.** The sweep mutates **no** DB rows (the de-dup state is Redis only) → no
  `FOR UPDATE`/advisory lock (unlike the mutating drain/digest/timer sweeps). Beat is single-instance, so at
  most one sweep runs per tick; even an overlapping slow-tick sweep is safe because `SET … NX` is atomic —
  two sweeps cannot both "first-see" the same id.

**Cost note.** The per-tick query scans the recent rows (`read_at IS NULL AND created_at > now()-LOOKBACK`);
at D1 single-org scale that window is tiny, so an index-less scan every 10 s is acceptable. A dedicated
`created_at`/partial index — or the `pushed_at` column named in §11 — is a trivial later optimisation if a
large deployment ever needs it.

**The worker task** (`pubsub_sweep`, co-located in `tasks/notifications.py`) builds its **own** per-run
async engine (`create_async_engine(settings.database_url)` + `async_sessionmaker(expire_on_commit=False)` +
`engine.dispose()` in a `finally` — the established worker **fresh-engine idiom**, mirroring
`_run_awareness_fanout`; **never** the api-process `get_sessionmaker()` global, which in the worker process
leaks a never-disposed engine — the worker-session rule in `.claude/rules`) + a short-lived
`redis_client(decode_responses=True)` (`aclose`'d in a `finally`), `asyncio.run(...)`s `sweep_and_publish`,
and returns the count. **Best-effort:** any Redis/DB error is logged and swallowed (a publisher failure must
never crash the worker or block the next tick); a transient Redis outage simply re-covers the (id-de-duped)
window next tick.

**Beat wiring (`tasks/app.py`):**
```python
# S-notify-5c: the SSE pubsub sweep — PUBLISH a per-user nudge to Redis for each freshly-committed
# notification so the api SSE endpoint can push the bell update in near-real-time (default 10 s).
"notifications-pubsub-sweep": {
    "task": "easysynq.notifications.pubsub_sweep",
    "schedule": float(_settings.notify_stream_sweep_interval_seconds),
},
```

## 5. Backend — the subscriber (the SSE endpoint)

**The DB-session crux (the #1 leak hazard).** FastAPI finalises a `yield` dependency (`get_session`'s
`async with`) only **after the response is fully sent** — and a `StreamingResponse` is "sent" only when its
body generator is exhausted, i.e. when the SSE connection *closes*. So a route that streams while holding a
`Depends(get_session)` session would **pin one DB connection idle for the entire connection lifetime** — and
the engine (`db/session.py`, `psycopg3` + SQLAlchemy's default `QueuePool` = 5 + 10 overflow = **15
connections per worker process**, ×2 workers) would be exhausted by a few dozen concurrent open streams.
Therefore the SSE route **must not use `Depends(get_session)`**, and the streaming generator **must touch no
DB session**
(nudge-only makes this trivial — it needs only Redis). Auth runs up-front with a **short-lived** session
that closes before streaming:

```python
async def _stream_caller(request: Request, jwks: JWKSCache = Depends(get_jwks_cache)) -> AppUser:
    async with get_sessionmaker()() as session:        # opened + CLOSED inside the dependency
        return await resolve_current_user(request, jwks, session)   # the get_current_user body, refactored

@router.get("/notifications/stream")
async def notification_stream(
    request: Request,
    caller: AppUser = Depends(_stream_caller),
) -> StreamingResponse:
    user_id = caller.id                                # capture the loaded PK into a local (detached-safe)
    return StreamingResponse(
        event_stream(request, user_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

`resolve_current_user` is `get_current_user`'s body extracted verbatim (validate bearer → JIT-provision →
INVITED→ACTIVE → inactive/revocation checks → return `AppUser`); `get_current_user` becomes
`return await resolve_current_user(request, jwks, session)`. Behaviour is byte-identical (the existing auth
tests are the regression backstop). `caller.id` is already loaded (queried/`refresh`ed), so reading it after
the session closes triggers no lazy load; capturing it into a `uuid.UUID` local is belt-and-suspenders.

**`api/_sse.py`:**
```python
def sse_event(event: str, data: str = "") -> str:
    return f"event: {event}\ndata: {data}\n\n"

HEARTBEAT_SECONDS = 20

async def event_stream(request, user_id):
    redis = redis_client(decode_responses=True)
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(channel_for_user(user_id))
        yield sse_event("notify")                         # on-connect sync (covers a missed-while-down gap)
        while True:
            if await request.is_disconnected():
                break
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=HEARTBEAT_SECONDS)
            if msg is not None:
                yield sse_event("notify")
            else:
                yield ": ping\n\n"                        # heartbeat comment (keeps proxy + client live)
    except asyncio.CancelledError:
        raise                                             # client gone — let it propagate, finally cleans up
    finally:
        try:
            await pubsub.unsubscribe(channel_for_user(user_id))
            await pubsub.aclose()
        finally:
            await redis.aclose()
```

- **Heartbeat.** A `: ping` comment every ≤20 s keeps Caddy's idle reverse-proxy connection open, lets the
  client detect a dead stream, and exercises the write path so a half-open TCP connection surfaces. 20 s is
  comfortably under any default idle timeout.
- **Disconnect.** For an *abrupt* drop (the client vanishes without a TCP RST), `request.is_disconnected()`
  can be unreliable — so the **heartbeat write is the load-bearing detector**: the next `yield ": ping"` (≤20 s
  later) fails to write to the dead peer, which raises into the generator and runs the `finally`. The
  `is_disconnected()` check + the `get_message` timeout handle the graceful-close case and bound the linger
  to ≤ one heartbeat interval (one idle Redis pubsub connection for that span — bounded-harmless).
  `CancelledError` (the client closed mid-`get_message`) propagates so the `finally` still runs. The §8
  leak-teardown test must cover **both** the `is_disconnected()=True` path **and** the heartbeat-write-fails
  path.
- **Never raises into the response.** Any unexpected error inside the loop ends the stream (the FE
  reconnects); it never 500s a half-sent body. The whole generator is defensive.
- **The gunicorn `--timeout 60` interaction (verify-in-smoke).** `UvicornWorker` is async and periodically
  notifies the gunicorn arbiter independent of request handling, so a long-lived SSE request is **not**
  expected to trip the 60 s worker timeout (which, for async workers, is a *worker-unresponsive* timeout,
  not a per-request timeout). If a given build *does* recycle the worker, the FE's capped-backoff reconnect
  makes it a brief blip, not a failure. Confirmed in live-smoke (hold a stream > 60 s idle, assert it stays
  open and a subsequent nudge arrives).

**Auth posture / latch.** The route is **authenticated-self** (`_stream_caller` → the bearer), exactly the
`GET /notifications` posture — **no permission key**. It stays under the `/api/v1/*` setup-latch (it is not
added to `_LATCH_EXEMPT_EXACT`): an authenticated user on an OPERATIONAL system passes the latch, and the
bell only renders in the operational shell anyway. **No token in any URL** — the bearer rides the
`Authorization` header on the fetch (the safety boundary + project rule; native `EventSource` can't set
headers, which is exactly why the FE uses a fetch/`ReadableStream` reader, §7).

## 6. Infra — Caddy

The Caddyfile already routes a bare `/events` token to `api:8000` (line 29, `@api path /api/* /healthz
/readyz /events`) — a reservation no FastAPI route ever claimed (the app mounts everything under `/api/v1`),
so it is currently dead. 5c mounts the stream at `/api/v1/notifications/stream` (consistent with the
notifications router, covered by `@api`'s `/api/*`, and behind the latch) and gives it a **dedicated**
reverse-proxy block with `flush_interval -1` so Caddy flushes each frame immediately instead of buffering:

```caddy
# The notification SSE stream needs immediate flushing (no response buffering) for a long-lived
# connection — its own reverse_proxy with flush_interval -1 (doc 10 §9, S-notify-5c). MUST precede @api
# (a handle block is first-match-wins; this path is a subset of /api/*).
@sse path /api/v1/notifications/stream
handle @sse {
	reverse_proxy api:8000 {
		flush_interval -1
	}
}

# API surface and health probes go to the FastAPI service.
@api path /api/* /healthz /readyz
handle @api {
	reverse_proxy api:8000
}
```

`flush_interval -1` is scoped to the SSE path only (normal JSON responses keep default buffering). The dead
`/events` token is removed from `@api`. No other infra change: the uvicorn command, the redis service, and
the worker/beat services are untouched (the sweep rides the existing beat container; the Redis bus is the
existing instance).

## 7. Frontend — the fetch-stream consumer

**`features/notifications/stream.ts` (pure + injectable, the testable seam):**
```ts
export function parseSseFrame(frame: string): { event: string; data: string } {
  let event = "message";
  const data: string[] = [];
  for (const line of frame.split(/\r?\n/)) {            // CRLF- or LF-delimited lines
    if (line.startsWith(":")) continue;                 // comment / heartbeat
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trim());
  }
  return { event, data: data.join("\n") };
}

// Opens the stream, carrying the in-memory bearer (native EventSource can't set headers). Resolves when
// the server closes the stream cleanly; rejects on a network/HTTP error; aborts via `signal`.
export async function openNotificationStream(token: string, onNudge: () => void, signal: AbortSignal) {
  const resp = await fetch("/api/v1/notifications/stream", {
    headers: { Authorization: `Bearer ${token}`, Accept: "text/event-stream" },
    signal,
  });
  if (!resp.ok || !resp.body) throw new Error(`stream ${resp.status}`);
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += decoder.decode(value, { stream: true });
    const sep = /\r?\n\r?\n/g;          // SSE frame boundary, CRLF- or LF-delimited (server emits LF)
    let last = 0;
    let m: RegExpExecArray | null;
    while ((m = sep.exec(buf)) !== null) {
      const { event } = parseSseFrame(buf.slice(last, m.index));
      last = m.index + m[0].length;
      if (event === "notify") onNudge();
    }
    buf = buf.slice(last);              // keep the partial trailing frame across reads
  }
}
```

**`useNotificationStream(openImpl = openNotificationStream)` (in `hooks.ts`), mounted in `NotificationBell`:**
- `const { token } = useAuth(); const qc = useQueryClient();` (the `openImpl` default-param is the injectable
  seam — `NotificationBell` calls `useNotificationStream()`; the focused hook test passes a fake `openImpl`).
- `useEffect` keyed on `[token, qc, openImpl]`: when `token` is present, run a reconnect loop guarded by an
  `AbortController`. Each iteration: record `openedAt = Date.now()`; `await openImpl(token, () =>
  qc.invalidateQueries({queryKey:["notifications"]}), ac.signal)`; on return/throw, `if (ac.signal.aborted)
  return`; else compute the delay — **reset `backoff = MIN_RECONNECT_MS` only if the stream stayed open ≥
  `HEALTHY_MS`**, otherwise `backoff = min(backoff * 2, MAX_BACKOFF_MS)`; then `await sleepWithSignal(backoff,
  ac.signal)` and `if (ac.signal.aborted) return` **before** reopening.
- **Storm fix (the validation MAJOR).** Resetting backoff on *open* is wrong — an accept-then-instant-close
  server (worker recycle / a build that trips the 60 s timeout) would reset to ~1 s and spin a reconnect loop,
  and since the server sends an on-connect `event: notify` each time, that becomes a refetch storm too.
  Resetting only after a **healthy duration** + a hard **`MIN_RECONNECT_MS` floor** bounds a flapping server to
  one reconnect (and one on-connect invalidate) every ≥3 s climbing to every 30 s — React Query coalesces the
  back-to-back invalidations. Constants: `MIN_RECONNECT_MS = 3_000`, `MAX_BACKOFF_MS = 30_000`,
  `HEALTHY_MS = 30_000`.
- **`sleepWithSignal(ms, signal)`** resolves early on `signal`'s `abort` event (NOT a bare `setTimeout`), so an
  unmount / token-change *during the backoff window* cancels the pending reconnect — no zombie loop waking 30 s
  later to open an orphan stream (the StrictMode double-mount + mid-backoff-unmount case). Cleanup
  `() => { stop = true; ac.abort(); }` on unmount / token change; `ac.abort()` also rejects the in-flight
  `reader.read()`. Never throws to the UI — the bell stays functional on the backstop poll if the stream can't
  open.
- A `notify` nudge invalidates the `["notifications"]` prefix → the count + the recent/all lists refetch
  together (the exact mark-read invalidation pattern).

**`useNotificationCount`:** `refetchInterval: 60_000 → 300_000` (a 5-min backstop). Everything else
unchanged — `refetchOnWindowFocus`/`refetchOnReconnect` stay default-on for the count query (the prefs query
disables them, the count query does not), so a tab refocus or network reconnect self-heals the badge on top
of SSE. **Degraded-mode note:** if SSE is globally broken in some deployment (proxy misconfig), the bell
falls back to the 5-min cadence — slower than today's 60 s, but the bell is not a critical-path surface and
a globally-dead stream is a misconfiguration caught in smoke, not a normal state. (A faster degraded cadence
or a server kill-switch flag is a named v1.x follow-up, §11.)

**Mount:** `NotificationBell` calls `useNotificationStream()` (it already renders once at app startup and
persists across navigation, under `AuthProvider` + `QueryClientProvider`). One stream per tab.

## 8. Testing

**Backend** (`apps/api/tests`):
- **Unit** `tests/unit/test_sse.py`: `sse_event` formatting (`notify` frame, heartbeat comment shape);
  `channel_for_user`/`dedup_key`; the `event_stream` generator driven with a **fake Redis pubsub** — assert
  (1) the first yielded chunk is the on-connect `event: notify`, (2) a queued pubsub message yields a
  `notify` frame, (3) a `get_message` timeout yields the `: ping` heartbeat, (4) **both** teardown paths run
  the `finally`'s `unsubscribe`+`aclose` (a leak-teardown assertion): `request.is_disconnected()→True`
  (graceful) **and** the generator being `aclose()`d / cancelled mid-loop (the abrupt-drop / heartbeat-write-
  fails path — drive `agen.aclose()` and assert the fake pubsub was unsubscribed + closed).
- **Unit** `tests/unit/test_notification_pubsub.py`: `sweep_and_publish` over a **fake session + fake Redis**
  — assert (1) each in-window notification id is nudged **once** (the `SET … NX` returns truthy first time,
  falsey after → no second nudge on the next sweep — the no-re-nudge-standing-unread property); (2) two ids
  for the same user collapse to **one** `PUBLISH`; (3) a row older than `LOOKBACK` (or `read_at` set) is
  excluded; (4) on a fake-Redis flush (the `SET … NX` returns truthy again) the in-window rows re-nudge once
  (over-publish-is-harmless); (5) a row whose **`created_at` is well behind `db_now`** (the txn-start-clock /
  batch case) is **still nudged** as long as it is within `LOOKBACK` — the regression test that the design
  fix actually closes the watermark miss. (DB-clock `func.now()` injected via the fake session's scalar.)
- **Integration** `tests/integration/test_notification_stream.py`: (a) **the sweep end-to-end** — INSERT a
  notification, run `pubsub_sweep` against the **testcontainer Redis**, and assert a subscriber on
  `notify:user:{uid}` receives the nudge (and a second org's user does **not**). (b) **the endpoint auth** —
  `GET /notifications/stream` **without** a bearer → 401; **with** a valid bearer → a 200
  `text/event-stream` whose first frame is `event: notify` (read one frame, then disconnect; assert the
  response did not hold a DB session — i.e. the request completes and a follow-up DB-using request on the
  same pool succeeds). **Delta-based / run-scoped** assertions + FK-ordered cleanup of any org/user/rows the
  test creates (the S-notify-4 `test_restore`-leak lesson).
- **Auth-refactor regression:** the existing `get_current_user` tests (JIT-provision, INVITED→ACTIVE,
  inactive 403, revocation 401) are unchanged and prove `resolve_current_user` parity.

**Frontend** (`apps/web`, vitest + MSW + jest-axe):
- **⚠ BASE MSW HANDLER FIRST (the validation MAJOR — else 4 existing suites break).** `test/setup.ts` runs
  `server.listen({onUnhandledRequest:"error"})` and `TEST_AUTH` carries a truthy token, so the moment
  `NotificationBell` mounts `useNotificationStream`, it fires `fetch("/api/v1/notifications/stream")` — which
  **every** existing suite that renders the bell/shell hits (`NotificationBell.test.tsx`, `AppShell.test.tsx`,
  `TopBar.test.tsx`, `App.test.tsx`). Add a **base handler** in `test/msw/handlers.ts` for `GET
  /api/v1/notifications/stream` that returns a **closeable** `text/event-stream` `Response` — emit the
  on-connect `event: notify\n\n` frame **then close** (a finite `ReadableStream`). The closeable response is
  load-bearing: the serial single-fork pool (`vite.config` `maxWorkers:1`) would hang/leak on a never-closing
  stream. The reconnect after close is bounded by `MIN_RECONNECT_MS` and aborted on unmount, so it leaves no
  open stream/timer in the shared fork.
- `features/notifications/stream.test.ts`: `parseSseFrame` + the frame-splitter table tests — a `notify`
  frame, a `: ping` comment (→ `message`/ignored), a multi-`data:` frame, and a **real CRLF frame**
  (`event: notify\r\n\r\n`) fed through the `/\r?\n\r?\n/` splitter (feed actual `\r\n\r\n`, so the test
  exercises the **splitter**, not just per-line `.trim()` — the validation CRLF finding), plus a frame split
  **across two reads** (partial buffer carried over).
- `features/notifications/useNotificationStream.test.tsx`: pass a **fake `openImpl`** (the injectable seam) —
  (a) it emits one `notify` → assert `qc.invalidateQueries({queryKey:["notifications"]})` is called once;
  (b) **the storm guard** — a fake `openImpl` that resolves *immediately* (accept-then-close) is **not**
  re-invoked faster than `MIN_RECONNECT_MS` (use fake timers; assert the reopen count over a window is
  bounded, and backoff grows since each open is < `HEALTHY_MS`); (c) **the mid-backoff abort** — unmount
  *during* the backoff `sleepWithSignal` window and assert **no** further `openImpl` call and no post-unmount
  `invalidate` (the StrictMode/zombie-loop case). When a test needs the real reader, stub `fetch` to return a
  `Response` whose `ReadableStream` emits the **exact server frame** (pinned to `sse_event`'s output, never
  hand-typed).
- `features/notifications/NotificationBell.test.tsx` (extend): the bell still renders the three-state badge;
  `useNotificationStream` is mounted (assert via the injected open spy); the `refetchInterval` change doesn't
  alter the rendered badge behaviour. (Relies on the base stream handler above.)
- **Traps:** `import { expect, it } from "vitest"`; **no production transition/timing hack to force a test
  green** (the S-notify-fe lesson — the storm/abort behaviour is real product behaviour, tested via fake
  timers + the injectable `openImpl`, never by weakening the component); the fetch-stream mock returns a real
  `ReadableStream` (not a hand-rolled object that drifts from the browser contract); `ReadableStream`/
  `TextDecoder`/`AbortController` are present in the jsdom/Node 22 env (probed by the validation, not
  assumed).

**Gates:** `/check-api` (ruff/mypy-strict/unit), `/check-contracts` (redocly on the openapi addition),
`/check-web` (eslint/tsc/build/full vitest), `/check-migrations` (no-op — no migration — but harmless).
Pre-PR: `diff-critic` + `web-test-trap-reviewer` on the branch diff; a **live-smoke** that holds a real
stream and watches a real `task.assigned` move the badge in ≤~10 s without a manual refetch, plus the
> 60 s-idle hold (§5) and the multi-worker fan-out check (§10).

## 9. Contract / no-migration checklist

- [ ] `openapi.yaml`: add the `GET /notifications/stream` path (tag `notifications`, `200` →
      `text/event-stream` with a `type: string` schema + a description of the `event: notify` / `: ping`
      frames, `401` → `ProblemResponse`), redocly-lint clean.
- [ ] **No** migration (head stays `0066`); **no** ORM/model change → `alembic check` unaffected.
- [ ] **No** new permission key (catalog stays 102); **no** role/grant seed (authenticated-self).
- [ ] `services/notifications/pubsub.py` + `api/_sse.py` import only `db.models` / `redis_client` /
      SQLAlchemy / stdlib (no cross-layer `api ← services` inversion; the route imports the service, never
      the reverse).
- [ ] `pubsub_sweep` is **co-located in `tasks/notifications.py`** (no new task module / no new
      `tasks/__init__.py` import line); the registration test asserts `"easysynq.notifications.pubsub_sweep"
      in app.tasks`.
- [ ] `tasks/app.py` beat_schedule gains `notifications-pubsub-sweep` (`float(_settings.notify_stream_sweep_interval_seconds)`).
- [ ] `config.py` gains `notify_stream_sweep_interval_seconds: int = 10` (`.env.example` may document
      `NOTIFY_STREAM_SWEEP_INTERVAL_SECONDS` as a net-new entry — note the sibling interval settings are
      undocumented there, so this is additive, not a precedent).
- [ ] **FE base MSW handler** for `GET /api/v1/notifications/stream` (closeable stream) added to
      `test/msw/handlers.ts` BEFORE the 4 existing bell/shell suites run (the validation MAJOR).
- [ ] Caddyfile: the `@sse` block precedes `@api`; `/events` dropped from `@api`.

## 10. Spec-validation outcomes (2026-06-24, 5 lenses / 15 agents)

Each target was code-anchor-verified + adversarially refuted; the verdict is folded above.

1. **Sweep de-dup correctness** — ❌→✅ **the original `created_at` watermark was MAJOR-broken** (it missed
   notifications enqueued in a long/SERIALIZABLE caller txn, because `created_at = transaction_timestamp()` =
   txn start, not commit). **Redesigned to id-de-dup in Redis** over a generous `LOOKBACK` (§4) — immune to
   the txn-start clock; the window arithmetic, first-run/Redis-flush re-init, and no-re-nudge-standing-unread
   property are confirmed sound on the new design.
2. **DB-session-leak** — ✅ confirmed real and correctly fixed: `get_session` is a yield-dependency held until
   the `StreamingResponse` body exhausts, so the SSE route uses a short-lived `_stream_caller` session that
   closes before streaming and the generator touches no DB; `expire_on_commit=False` makes the post-close
   `caller.id` read lazy-load-free.
3. **Resource teardown** — ✅ sound; sharpened: the **heartbeat write** (not `is_disconnected()`) is the real
   detector for an abrupt drop, and the leak-teardown test must cover both paths (§5/§8).
4. **Multi-worker fan-out** — ✅ Redis pub/sub fans a single `PUBLISH` to every worker's subscription; no step
   assumes in-process state. (The 2-UvicornWorker fact is exactly why the bus is required.)
5. **Auth / latch / no-token-in-URL** — ✅ channel `notify:user:{caller.id}` is server-derived from the bearer
   (no client-supplied id → no cross-user subscription); nudge is content-free (R32); authenticated-self (no
   key, catalog 102); behind the latch; bearer on the `Authorization` header only; the `resolve_current_user`
   refactor preserves the inactive-403/revocation-401/JIT checks.
6. **Contract/scope** — ✅ no migration / key / WORM / role grant; openapi `text/event-stream` is redocly-valid;
   nudge-only + backstop-kept, no creep. Nits folded: `int` interval (not float), drop the hop-by-hop
   `Connection` header, co-locate the task, `psycopg3` (not asyncpg) pool wording.

## 11. Named residuals (not faked; out of scope for 5c)

- **Full-payload push** — pushing the new notification's title/deep-link over SSE so the popover updates
  without a refetch (owner: nudge-only; would add a second count source + an R32 surface).
- **Drop the backstop poll entirely** (pure SSE) and/or a **faster degraded cadence** / a server
  **`sse_enabled` kill-switch** flag for deployments whose proxy can't do SSE (owner: keep the 5-min
  backstop; a flag is a trivial later add).
- **A `pushed_at` stamp column** (+ a partial index) for exact `WHERE pushed_at IS NULL` de-dup — eliminating
  the `LOOKBACK` scan + the Redis de-dup keys, at the cost of a 1-column migration and turning the sweep into
  a mutating (lock-disciplined) sweep (owner: migration-free for 5c; this is the clean optimisation if a large
  deployment ever finds the look-back scan or the dedup-key churn costly).
- **A `created_at` / partial index** on `notification` to make the look-back scan index-served (migration;
  unnecessary at D1 single-org scale).
- **Push for other surfaces** — the admin Health panel (5b), the `/tasks` inbox count, register summaries —
  all still poll/refetch; 5c is the bell unread-count only.
- **A `last-event-id` / replay buffer** — SSE `Last-Event-ID` resumption (we rely on the on-connect nudge +
  backstop instead of a server-side per-user event log).
- **The slice-4 timer-sweep claim-threshold filter** (the `remind_2_sent_at IS NULL` tautology / unused
  `_pending_event_ids(now)`) — unrelated subsystem; a focused escalation-sweep follow-up.
