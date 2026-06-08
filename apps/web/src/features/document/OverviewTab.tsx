import { Button, Stack } from "@mantine/core";
import type { DocumentSummary } from "../../lib/types";
import { ControlMetadata } from "./ControlMetadata";
import { useControlledCopyDownload } from "./download";

// The Overview tab: the control-metadata definition list (DP-5 identity in durable form) + the
// controlled-copy download (Effective-only; document.read — every reader holds it). The artifact
// header itself renders above the tabs (DocumentDrawer), so it is not repeated here. The metadata
// table + download were extracted (S-web-4) into ControlMetadata + useControlledCopyDownload so the
// standalone Document page reuses them; this tab's rendered output is unchanged.
export function OverviewTab({
  doc,
  typeName,
  ownerName,
}: {
  doc: DocumentSummary;
  typeName?: string;
  ownerName?: string;
}) {
  const { open, downloading } = useControlledCopyDownload(doc.id);

  return (
    <Stack gap="sm">
      <ControlMetadata doc={doc} typeName={typeName} ownerName={ownerName} />
      {doc.current_effective_version_id && (
        <Button
          variant="light"
          size="sm"
          loading={downloading}
          onClick={() => void open()}
          style={{ alignSelf: "flex-start" }}
        >
          ⤓ Download controlled copy
        </Button>
      )}
    </Stack>
  );
}
