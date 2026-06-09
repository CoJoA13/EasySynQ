import { Button, Checkbox, Popover, Radio, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { useMerge } from "./hooks";

// The mockup §8b "Merge ▾" control. Given ≥2 selected files, pick the effective member (Radio over
// the selection, default the first) + the reconstruct-revision-chain opt-in (default OFF — R10), then
// submit a merge intent. Merge is server-authoritative: useMerge invalidates + refetches; we NEVER
// reshape the cache here. ONE Idempotency-Key per merge (crypto.randomUUID()). Disabled (with a hint)
// under 2 selected files.
export function MergeMenu({
  runId,
  selectedFileIds,
  onDone,
}: {
  runId: string;
  selectedFileIds: string[];
  onDone: () => void;
}) {
  const [opened, setOpened] = useState(false);
  // Default the effective member to the first selected id (degrade to "" under noUncheckedIndexedAccess).
  const [effective, setEffective] = useState<string>(selectedFileIds[0] ?? "");
  const [reconstruct, setReconstruct] = useState(false);
  const merge = useMerge(runId);
  const tooFew = selectedFileIds.length < 2;

  function submit() {
    const effective_file_id = effective || (selectedFileIds[0] ?? "");
    merge.mutate(
      {
        body: {
          file_ids: selectedFileIds,
          effective_file_id,
          reconstruct_revision_chain: reconstruct,
        },
        idempotencyKey: crypto.randomUUID(),
      },
      {
        onSuccess: () => {
          setOpened(false);
          onDone();
        },
      },
    );
  }

  return (
    <Stack gap={4}>
      <Popover opened={opened} onChange={setOpened} position="bottom-start" withArrow trapFocus>
        <Popover.Target>
          <Button
            size="xs"
            variant="default"
            disabled={tooFew}
            onClick={() => setOpened((o) => !o)}
          >
            Merge
          </Button>
        </Popover.Target>
        <Popover.Dropdown>
          <Stack gap="sm" w={320}>
            <Radio.Group
              label="Effective member"
              description="The version that stays Effective; the rest are superseded."
              value={effective}
              onChange={setEffective}
            >
              <Stack gap={4} mt={4}>
                {selectedFileIds.map((id) => (
                  <Radio key={id} value={id} label={id} aria-label={`Effective: ${id}`} />
                ))}
              </Stack>
            </Radio.Group>
            <Checkbox
              label="Reconstruct revision chain"
              description="Opt-in (off by default). Materializes the prior versions as a revision history."
              checked={reconstruct}
              onChange={(e) => setReconstruct(e.currentTarget.checked)}
            />
            <Button onClick={submit} loading={merge.isPending}>
              Merge into one family
            </Button>
          </Stack>
        </Popover.Dropdown>
      </Popover>
      {tooFew && (
        <Text size="xs" c="dimmed">
          Select 2 or more files to merge.
        </Text>
      )}
    </Stack>
  );
}
