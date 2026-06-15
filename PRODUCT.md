# Product

## Register

product

## Users

EasySynQ is operated by a small, role-segmented quality team inside **one organisation, on that
organisation's own server** — self-hosted, browser-accessed, air-gap-friendly, data never leaving
their infrastructure. Eight personas, each in a distinct compliance workflow:

- **Avery — System Administrator.** Runs the server, users, backups, restore, config. Sits *outside*
  the QMS (holds no `document.*`). Context: infrastructure, not quality content.
- **Mara — Quality Manager.** Owns the QMS end-to-end: objectives, management review, the audit
  programme, CAPA oversight, the compliance checklist. The power user; lives in the app daily.
- **Diego — Process Owner.** Accountable for a slice of the process map; reviews documents and
  objectives in their area.
- **Priya — Author.** Drafts and revises controlled documents. Wants authoring to feel like writing,
  not wrestling a system.
- **Ken — Approver.** Reviews and approves/releases; the separation-of-duties counterparty. Works
  from a task inbox, in short focused sessions.
- **Ingrid — Internal Auditor.** Plans and conducts audits, raises findings, drives them to CAPA.
- **Sam — Employee.** Reads effective documents, acknowledges what they're assigned. Light, occasional
  use; must never feel like "the QMS is for someone else."
- **Olsen — External Auditor.** Read-only, time-boxed, scrutinising. Needs to verify — at a glance and
  on demand — that every controlled document is current, signed, and traceable.

The job to be done: **run an ISO 9001:2015 quality system without document drift, and be able to prove
it on any given day.** Authoring, review, approval, acknowledgement, nonconformity & CAPA, internal
audits, quality objectives, and management review — each is a task on its own screen, performed by a
specific role under deny-by-default permissions.

## Product Purpose

EasySynQ is a **self-hosted, browser-based ISO 9001:2015 Quality Management System** that **inverts
authority so document drift becomes an enforced invariant.** A managed controlled vault (PostgreSQL +
object storage) owns the master copy of every controlled document and record; the on-disk filesystem
is only a read-only mirror, regenerated from *Released* versions. Authority flows vault → mirror,
never the reverse — so the single hardest problem in a real QMS (the uncontrolled copy, the stale PDF
on someone's desktop) is designed out rather than policed.

The interface flows the way ISO 9001 itself flows — the clause spine, the process map, the PDCA cycle
— so the system reads as the standard made operable, not as a database with forms bolted on.

Success looks like: the organisation passes its surveillance audit with **zero document-control
findings**; day-to-day quality work feels *calm and legible* rather than bureaucratic; and an external
auditor can confirm the state of any document — effective version, signatures, lineage — in seconds,
without a guided tour.

## Brand Personality

**Calm, precise, trustworthy.** Modern and quietly confident, progressively disclosed, never
overwhelming. Audit-grade seriousness without enterprise heaviness.

- **Voice:** plain, exact, reassuring. States facts (who signed, when, what is effective, what is
  blocking) without alarm or jargon theatre. Never breezy, never bureaucratic.
- **Emotional goals:** *confidence* (you are compliant and can prove it), *calm* (a 9001
  implementation that doesn't feel like a second job), *control* (you always know the system's state
  and your next action).
- The product earns trust by being **legible under scrutiny** — the same screen reassures the employee
  and satisfies the auditor.

## Anti-references

- **Legacy enterprise QMS** (SAP / SharePoint / Documentum / MasterControl): cluttered toolbars, dated
  chrome, grey-on-grey density, modal-on-modal flows, configuration that leaks into the UI. This is the
  incumbent EasySynQ exists to replace — do not echo it.
- **Playful consumer SaaS:** bright gradients, mascots, emoji, rounded-everything, marketing
  illustration. Undermines the audit-grade seriousness the domain requires.
- **Generic Bootstrap admin template:** undifferentiated CRUD admin with default components, no point
  of view, every screen an identical icon-heading-text card grid.

(A restrained dark "cockpit" is *not* an anti-reference — the app ships a calm dark mode — but neon-on-
black data-viz overload is still off-tone.)

## Design Principles

1. **The system flows the way the standard flows.** Information architecture mirrors the ISO 9001
   clause spine / process map / PDCA cycle. The QMS should feel like the standard made operable, not a
   schema with forms. Structure carries meaning before any pixel does.
2. **Calm under compliance.** Restraint is the default. One calm accent, layered neutral surfaces,
   generous rhythm. Density is earned by the task (a register, an audit log), never imposed. The
   opposite of the legacy-enterprise wall of controls.
3. **Legibility is the feature.** Trust is earned by making state plainly visible — effective version,
   signatures, lifecycle state, what is blocking and why. The employee and the external auditor read
   the *same* surface and both understand it. Auditability is something you can see, not a report you
   run.
4. **Progressive disclosure, one task per screen.** Each surface has a primary task; depth is revealed
   on demand (drawers, tabs, redlines, expandable timelines), never dumped up front. Reach for inline
   and progressive affordances before a modal.
5. **Status you can't misread.** Lifecycle state and RAG (red/amber/green) are never carried by colour
   alone — shape, icon, and label reinforce every status. In a compliance system, an ambiguous
   indicator is a defect.

## Accessibility & Inclusion

- **Target: WCAG 2.2 AA**, enforced in CI (`jest-axe` smoke per page, `eslint-plugin-jsx-a11y`). AA
  contrast for all body/large text and placeholders; visible non-obscured focus (skip-link + Focus
  Not Obscured already in the shell); full keyboard operability including the command palette.
- **Colour-safe RAG.** Red/amber/green status — central to PDCA, objectives, drift, audit findings,
  and the compliance checklist — must never be conveyed by colour alone. Pair every RAG signal with an
  icon, shape, and/or text label so it survives colour-blindness and greyscale print/export.
- **Reduced motion is not optional.** Every transition needs a `prefers-reduced-motion: reduce`
  alternative (crossfade or instant). Motion conveys state, never decoration.
- **Air-gap-safe by design.** System font stack only (no web fonts), so the UI renders identically on
  a disconnected, self-hosted box — an inclusion property as much as an operational one.
