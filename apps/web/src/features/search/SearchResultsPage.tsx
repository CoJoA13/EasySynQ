import { Container, Loader, Stack, Text, Title } from "@mantine/core";
import { useSearchParams } from "react-router-dom";
import { useSearch } from "./hooks";
import { SearchResultRow } from "./SearchResultRow";

export function SearchResultsPage() {
  const [params] = useSearchParams();
  const q = params.get("q") ?? "";
  const term = q.trim();
  const { data, isLoading } = useSearch(q);

  return (
    <Container size="lg" py="md">
      <Title order={2} mb="md">
        Search
      </Title>
      {term.length === 0 ? (
        <Text c="dimmed">Type a query to search documents.</Text>
      ) : isLoading ? (
        <Loader />
      ) : (
        <Stack gap="xs">
          <Text c="dimmed" size="sm">
            Searches title, identifier &amp; clause refs — Effective documents only.
          </Text>
          {data && data.results.length === 0 ? (
            <Text>No matching documents.</Text>
          ) : (
            data?.results.map((hit) => <SearchResultRow key={hit.id} hit={hit} />)
          )}
          {data && data.hidden_by_scope > 0 && (
            <Text c="dimmed" size="sm">
              {data.hidden_by_scope} hidden by your access scope.
            </Text>
          )}
        </Stack>
      )}
    </Container>
  );
}
