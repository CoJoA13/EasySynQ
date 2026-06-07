import { Badge, Group, Loader, Stack, Table, Text, Title } from "@mantine/core";
import { useSearchParams } from "react-router-dom";
import { useDocuments } from "./useDocuments";
import { StateBadge } from "./StateBadge";
import { DocumentDrawer } from "./DocumentDrawer";

export function LibraryPage() {
  const { data, isLoading, isError } = useDocuments();
  const [params, setParams] = useSearchParams();
  const detailId = params.get("detail");
  // Only open the drawer once the document is actually in the loaded list — avoids an empty-shell
  // flash on a cold deep-link (/library?detail=<id>) before the query resolves.
  const selected = data?.find((d) => d.id === detailId) ?? null;

  const open = (id: string) =>
    setParams((p) => {
      p.set("detail", id);
      return p;
    });
  const close = () =>
    setParams((p) => {
      p.delete("detail");
      return p;
    });

  return (
    <Stack gap="md">
      <Title order={1}>Document Library</Title>
      {isLoading && <Loader aria-label="Loading documents" />}
      {isError && <Text c="red">Could not load documents.</Text>}
      {data && (
        <Table highlightOnHover stickyHeader aria-label="Documents">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Identifier</Table.Th>
              <Table.Th>Title</Table.Th>
              <Table.Th>State</Table.Th>
              <Table.Th>Clause</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {data.map((d) => (
              <Table.Tr
                key={d.id}
                style={{ cursor: "pointer" }}
                onClick={() => open(d.id)}
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    open(d.id);
                  }
                }}
              >
                <Table.Td>
                  <Text ff="monospace" size="sm">
                    {d.identifier}
                  </Text>
                </Table.Td>
                <Table.Td>{d.title}</Table.Td>
                <Table.Td>
                  <StateBadge state={d.current_state} />
                </Table.Td>
                <Table.Td>
                  <Group gap={4}>
                    {(d.clause_refs ?? []).map((c) => (
                      <Badge key={c} variant="outline" color="var(--es-accent)">
                        {c}
                      </Badge>
                    ))}
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      <DocumentDrawer doc={selected} opened={selected !== null} onClose={close} />
    </Stack>
  );
}
