// The 4 notification event classes, in the server-enum order the digest_modes object uses, with the
// presentation copy for the /settings/notifications matrix (S-notify-3b). Pure data — kept out of the
// page JSX so it is unit-testable. The class set is fixed in v1 (services/notifications/classes.py).

export type NotificationClass = "action_required" | "awareness" | "critical" | "admin_ops";

export interface ClassMeta {
  key: NotificationClass;
  label: string;
  helper: string;
  /** admin_ops has no email template until slice 5 — its cadence governs no email today. */
  inAppOnly?: boolean;
}

export const CLASS_META: ClassMeta[] = [
  {
    key: "action_required",
    label: "Things you must act on",
    helper: "Tasks, reviews, approvals and acknowledgements routed to you.",
  },
  {
    key: "awareness",
    label: "Awareness",
    helper: "Approvals, releases and audit milestones in your scope.",
  },
  {
    key: "critical",
    label: "Critical",
    helper: "Overdues and integrity alarms — time-sensitive.",
  },
  {
    key: "admin_ops",
    label: "Admin & operations",
    helper: "Backup and email-delivery failures.",
    inAppOnly: true,
  },
];
