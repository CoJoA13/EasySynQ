import { Mark, Text } from "@mantine/core";
import { Fragment } from "react";

// PostgreSQL ts_headline wraps matched terms in literal <b>…</b>. We split on those exact tokens and
// render every segment as a React TEXT node — interpreting ONLY <b>/</b> as highlight boundaries.
// Any other "<" (e.g. a "<script>" in a title) renders as a literal character, so the snippet can
// never inject HTML. No dangerouslySetInnerHTML.
export function Snippet({ text }: { text: string }) {
  if (!text) return null;
  const parts = text.split(/(<b>|<\/b>)/);
  let on = false;
  return (
    <Text span size="sm" c="dimmed">
      {parts.map((p, i) => {
        if (p === "<b>") {
          on = true;
          return null;
        }
        if (p === "</b>") {
          on = false;
          return null;
        }
        if (p === "") return null;
        return on ? <Mark key={i}>{p}</Mark> : <Fragment key={i}>{p}</Fragment>;
      })}
    </Text>
  );
}
