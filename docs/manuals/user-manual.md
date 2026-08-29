# EasySynQ User Manual

## 1. About this manual

This manual describes the current browser application. What you see depends on:

- your assigned roles and per-user overrides;
- the scope of those grants (system, process, folder, or document);
- explicit DENY rules, which always win; and
- the current state of the item.

An absent button is normally intentional: EasySynQ hides actions you cannot legally take. A calm
empty register may also mean your scope contains no matching rows. Ask a QMS Owner or System
Administrator to verify the grant before treating it as a data-loss problem.

EasySynQ is single-organization and browser-based. It is an ISO 9001:2015 QMS; full Part 11
electronic signatures, multi-standard packs, and in-app rich document authoring are not part of the
current release.

## 2. Sign in and orient yourself

Open the URL supplied by IT and sign in through Keycloak. EasySynQ keeps the access token in memory,
so a browser reload may briefly redirect through Keycloak again; an existing SSO session normally
makes that seamless.

The top bar contains:

- **Search (⌘K)** — quick document suggestions and full search (`Ctrl-K` on Windows/Linux,
  `Command-K` on macOS);
- **Tasks** — work assigned to you;
- the **notification bell** — unread events and deep links; and
- **Account** — Administration when authorized, Notification settings, and Sign out.

On a small screen, use **Toggle navigation** to open the left rail.

## 3. Navigation

The left rail groups the QMS by Plan–Do–Check–Act:

| Phase | Current sections |
|---|---|
| Home | PDCA health summary and your task rail |
| PLAN | Objectives; Risk & opportunity register; Context; Interested parties |
| DO | Library; Records; Review and approve; Import |
| CHECK | Compliance; Internal audit; Management reviews; Drift; Document register |
| ACT | Nonconformity and CAPA; Improvement; Change requests |

Each PDCA group header shows its clause range (for example "PLAN · Cl 4–6"). The clause spine
itself lives inside the Library: its clause filter buttons refine the document list in place.

The DO group's **Records** section is the read-only evidence operations console: a filterable
record list, per-record detail, and evidence download. Retention and disposition actions remain
administrative API/worker flows.

Status is never communicated by color alone. The common glyph vocabulary is:

- `✓` OK / on track;
- `◔` needs attention;
- `✕` action required / failed;
- `●` in progress / informational;
- `○` neutral / no data; and
- `★` released / milestone.

## 4. Find and read the governing document

### Quick search

1. Select **Search (⌘K)**, press `Ctrl-K` on Windows/Linux, press `Command-K` on macOS, or press `/`
   while focus is not in a text field.
2. Type an identifier or title.
3. Use Up/Down and Enter, or select a suggestion.
4. Choose **Search “…”** for the full results page.

Full search covers authorized **Effective** documents by identifier, title, legacy identifier, and
area code. Results display clause references but do not currently search by clause number; use the
Library clause filter for that. The results page tells you when additional hits were hidden by your
access scope.

### Library

Use **Library** when you need all authorized document states or richer filtering:

- filter by state, type, owner, clause, or effectivity;
- browse through the clause spine;
- switch between Comfortable and Compact rows; and
- select an identifier to open its detail drawer.

Open the full document page for:

- the governing revision and effective date;
- mapped clauses, review date, and acknowledgement coverage;
- the controlled rendition;
- control metadata;
- version history and comparisons;
- approvals;
- where-used relationships; and
- acknowledgement assignments.

Choose **Open controlled copy** to open the watermarked governing PDF in a new tab. The access is
logged. If the PDF is still rendering, EasySynQ may explicitly state that it opened the source file.

Treat downloaded or printed files as controlled copies only according to their watermark and verify
token. The vault remains authoritative.

## 5. Document lifecycle

The canonical lifecycle is:

`Draft → InReview → Approved → Effective → UnderRevision → Superseded → Obsolete`

Only an Effective version governs. Releasing a newly Approved revision makes it Effective and, when
one exists, supersedes the prior Effective version atomically.

Authoring, approval, and release are distinct acts:

- an author cannot approve their own version;
- a releaser cannot release a version they authored where separation of duties forbids it; and
- some Quality Policy, Objective, or Management Review artifacts require Top Management
  authorization before release.

The server enforces those boundaries even if stale browser state appears to offer an action.

## 6. Create a controlled document

You need `document.create`; scoped authors may also need to select an allowed process.

1. Open **Library** and select **New document**.
2. **Metadata**
   - enter the title;
   - choose a document type and classification;
   - optionally set an area code, dotted folder path, and one or more processes; and
   - select **Create & continue**.
3. **Upload**
   - check out the new document;
   - select the first source file;
   - enter the change reason/summary and significance; and
   - check it in as Rev A.
4. **Clauses** — map at least one ISO clause.
5. **Submit** — review the summary and submit it for approval.

The vault allocates the identifier. Important: the Draft is created at the end of Metadata so later
steps have an ID. Cancelling after that point leaves an empty/incomplete Draft; the current UI has no
discard action. Resume it from the Library or ask the content owner how it should be handled.

Submitting ends the author's part of the journey. EasySynQ displays **Awaiting review** and prevents
self-approval.

## 7. Revise an Effective document

1. Open the document.
2. Select **Start revision** when the action is available.
3. Check it out. If another user holds the lock, the panel identifies the lock holder; only authorized
   users see a break-lock action.
4. Upload the revised source.
5. Enter a meaningful change reason and choose Major or Minor significance.
6. Check in the immutable new version.
7. Review clause mappings and submit for review.

Check-out protects the mutable working copy. Every check-in creates a new immutable version; it does
not overwrite history. Do not bypass the vault by editing the filesystem mirror.

## 8. Review, approve, release, and acknowledge

### My Tasks

Open **Review and approve** or the Tasks icon. The queue shows pending work by subject, action,
stage, state, and due date. Search and sort the queue to triage it.

The same queue can contain document approvals, periodic reviews, CAPA decisions, DCR approvals,
objective commitments, management-review work, improvement authorizations, leadership release
authorization, and read acknowledgements.

### Make a decision

1. Open the task.
2. Review the subject context and, for a document, the version comparison/redline.
3. Choose the offered outcome.
4. Add a comment. It is required for **Request changes** and **Reject**.
5. For a signing outcome, check the “Signing as …” confirmation.
6. Select **Submit decision**.

The current signature is a single-factor logged confirmation and produces an append-only
`signature_event`. It is not a full 21 CFR Part 11 electronic signature.

### Release an Approved document

An authorized releaser opens the document's **Approvals** tab and selects **Release**, then confirms
**Release document**. Leadership artifacts may first show a Top Management authorization gate. A
successful first release moves the Approved revision to Effective as the document's first governing
revision. A later release moves the Approved revision to Effective and supersedes the prior governing
revision atomically.

### Read and acknowledge

An acknowledgement is evidence that you read and understood the assigned governing version; it is
not a signature.

1. Open the acknowledgement task.
2. Read the document context/controlled copy.
3. Select **I have read & understood**.

If the assigned revision has been superseded or the obligation lapsed, EasySynQ refuses the stale
acknowledgement and directs you to current work.

## 9. PLAN registers

### Objectives

Use **Objectives** to create and maintain quality objectives, commitments, plans, measurement
history, due dates, and attainment. The detail page shows the target direction and RAG status:
On track, Needs attention, Action required, or Not yet measured.

Depending on your authority, you can:

- create an objective;
- edit its commitment and thresholds;
- add plans;
- record measurements;
- submit/revise its controlled commitment; and
- request or complete leadership authorization before release.

### Risk & opportunity, Context, and Interested parties

These registers use a governed head/revision lifecycle. Authorized stewards can add/edit entries
while the current head is editable, publish a register revision, route/review it, and release the
approved revision. Use the drawers for detail and provenance rather than relying on a board card
alone.

Process-scoped readers may see only their permitted rows. A published or released revision is
historical evidence; start the next revision rather than rewriting it.

## 10. CHECK work

### Compliance

The Compliance Checklist scores mandatory ISO documented-information coverage as Covered, Partial,
or Gap and also surfaces overdue review information. It is an evidence/coverage aid, not an
automatic certification judgment.

### Controlled Document Register

Use **Document register** for the controlled-document report. Apply supported filters and read the
provenance banner: it identifies generation time and scope, including exclusions. A filtered report
is not an org-wide assertion.

### Internal audit

The **Audits** tab lists internal audits and supports creating one when authorized. The
**Program** tab manages audit programs and plans.

A typical flow is:

1. create/select a program and plan;
2. create an audit from a plan;
3. advance the audit through its legal states;
4. log findings as NC, Observation, or OFI;
5. follow an NC's automatically linked CAPA where applicable;
6. correct a finding by creating a traceable successor; and
7. close only after blocking NC/CAPA work satisfies the close gate.

### Management reviews

Create a review, compile the required ISO 9.3.2 inputs, record outputs, and route the review through
its lifecycle. Outputs can become actions, CAPAs, DCRs, or improvement initiatives where the
corresponding action is offered. Assigned preparation and action work also appears in My Tasks.

### Drift

**Drift status** shows the last mirror and blob-integrity scans. **Superseded copies** is the recall
list for stale controlled copies. Integrity findings require operator/QMS follow-up; do not dismiss
them as ordinary application errors.

## 11. ACT work

### Complaints, NCRs, and CAPA

The Nonconformity and CAPA section has Board, Complaints, and NCR tabs.

- **Complaints** — log customer feedback and spawn a CAPA when required.
- **NCRs** — raise nonconforming output and record its one-time disposition.
- **CAPA Board** — raise or open a CAPA, then complete correction, root cause, action plan, action
  execution, effectiveness verification, and close/loop-back controls.

Evidence links and approvals are permission/state dependent. A “Not effective” verification returns
the CAPA to root-cause work rather than closing it.

### Improvement

Create an improvement initiative manually or from an OFI/management-review output. Move it through
Open, In progress, Completed, and Closed, record the realized benefit, and request management
authorization where required. Cancellation remains a separate, audited outcome.

### Change requests

Use **Change requests** for Revise, Create, or Retire proposals. Record reason class, significance,
impact, affected processes/documents, and source relationships. The governed path supports assess,
route for approval, implement, and close/cancel actions. Use the DCR diff page to inspect the
proposed change rather than approving from the register row alone.

## 12. Import an existing QMS

Import is permission-gated and reads only from the source root mounted by IT.

1. Open **Import** and select **New import**.
2. Enter/select the configured source root and options such as OCR.
3. Wait while scanning, extracting, and classifying settle.
4. In the review cockpit, human-confirm kind and disposition; correct identifiers, types, clauses,
   and process mappings; and resolve duplicate/version-family proposals.
5. Review the pre-commit checklist.
6. An authorized committer commits the accepted baseline.

The default is current-version-only. Revision-chain reconstruction is not implemented and is
refused rather than manufacturing false history. A PartiallyCommitted run requires careful review
before resume; see the administrator manual and residual ledger.

## 13. Notifications

The bell is always immediate in-app. Open **Account → Notification settings** to control email:

- organization email delivery must first be enabled by an administrator;
- set each class to Immediate, Daily, or Off;
- choose the daily digest hour and timezone; and
- optionally set quiet hours.

“Off” means no email for that class, not no in-app event. Critical/escalation messages may pierce
quiet hours when the organization policy enables that behavior.

## 14. Troubleshooting

| Symptom | What to do |
|---|---|
| A button is missing | Check item state, role, scope, and explicit DENY. Ask the grant owner; do not assume UI failure. |
| A register is empty | Clear filters, then confirm your process/folder/document scope. |
| Search does not show a Draft | Full search intentionally returns Effective documents only; use Library. |
| Upload/download fails | Confirm workstation access to `https://<host>:9443` and trust the internal CA. |
| Login loops | Verify you are using the configured FQDN, clear stale browser state, and contact IT after a hostname/issuer change. |
| A task says already decided | Another tab/user may have completed it. Return to My Tasks and refresh. |
| Approval is denied for separation of duties | Use a different authorized approver/releaser; do not self-grant to bypass the control. |
| Controlled PDF is unavailable | Rendering may still be queued or the document has no Effective version. |
| Email is missing | Check in-app notifications first, then your email preference and the organization email switch. |
| Integrity/drift alarm appears | Notify the QMS Owner and IT; preserve evidence and follow the integrity runbook. |

## 15. Current limitations

- Search is PostgreSQL FTS; OpenSearch-only facets/highlighting and generic audit-log search are not
  present.
- The application edits metadata/workflows and accepts uploaded source files; it is not a rich
  collaborative document editor.
- Custom-role editing is not available in the current Roles UI.
- Retention Policy and Evidence Pack management have shipped API/worker flows but no dedicated
  browser management screens. Records have a read-only browser console (DO → Records); record
  capture and disposition still happen inside supported workflows and API/worker flows.
- Full Part 11 re-authentication/cryptographic signatures are not implemented.
- Import revision-chain reconstruction is intentionally refused.
- Approved future-effective versions have no current reschedule/rescind action, and open revision
  drafts have no discard action. Confirm dates before approval and resume or govern abandoned drafts
  explicitly.
- Some deliberately accepted hardening residuals remain open in
  [`open-residuals.md`](../open-residuals.md) (the sole current ledger; `slice-history.md` is
  historical evidence); their absence from the GitHub issue queue does not mean they are complete.
