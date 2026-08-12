# 0001 — Centralize effective URL-view classification

**Date:** 2026-08-11

**Status:** Accepted

## Context

EasySynQ uses query parameters for materially different page views, drawers, tabs, diff modes, search,
facets, sort, and pagination. Route chrome currently sees only the pathname, while route-error recovery
uses the complete raw location. The two boundaries therefore disagree: `/tasks?type=DOC_ACK` changes the
product view without changing title or focus, while an ordinary filter edit resets route recovery.

The application needs one safe interpretation of recognized URL state without moving feature-owned
controls into a second global store or exposing opaque query values in accessible copy.

## Decision

Introduce a pure typed classifier that maps pathname plus recognized query parameters to fixed title
copy, a chrome identity, a recovery identity, focus ownership, and an optional safe announcement.

Route chrome and the shell route-error boundary will consume that shared result. Feature pages retain
ownership of parsing, validation, history writes, controlled inputs, data fetching, and dialogs. Unknown
parameters remain preserved and ignored by global classification. Opaque detail identities may appear
only in internal recovery keys, never in visible or accessible copy.

## Consequences

Material query views receive deliberate title, focus, and announcement behavior. Meaningful detail and
subview changes reset stale route failures, while filters, search, sort, and pagination do not. New
dependencies, routes, providers, and global feature state are unnecessary.

The classifier becomes a maintained inventory. A new query-selected product view can render correctly
inside its feature while still receiving stale global chrome if its classification is omitted. Tests and
the payoff trigger below keep that maintenance obligation explicit.

## Alternatives

### Page-owned chrome registration

Feature components could register their current title and focus policy. This keeps local knowledge close
to the feature, but creates ordering and unmount races and leaves route recovery with a separate mapping.

### Promote query views to path routes

Material views could move to distinct route paths. This simplifies those particular titles but requires
bookmark and redirect compatibility and still leaves drawers, tabs, modes, and ordinary query state to
classify.

## Payoff trigger

Revisit the centralized inventory when two independent feature teams need to add material query views in
parallel, or when the classifier must import feature data to decide a view. At that point, replace the
static inventory with typed feature-contributed descriptors while preserving one combined chrome and
recovery decision.
