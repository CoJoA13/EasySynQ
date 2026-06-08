import { Loader, Modal, Stack, Text, TextInput, UnstyledButton } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { useState } from "react";
import type { KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useSuggest } from "./hooks";

// A hand-rolled ⌘K command palette (no @mantine/spotlight dependency). The Mantine Modal supplies
// dialog semantics + focus-trap + Esc. Live /suggest rows give jump-to-doc; a fixed footer action
// opens the full /search results page. Keyboard: ↑/↓ move the active option (combobox +
// aria-activedescendant), Enter activates it. Selecting closes the palette and clears the query.
export function CommandPalette({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const navigate = useNavigate();
  const term = q.trim();
  const [debouncedQ] = useDebouncedValue(q, 150);
  const { data, isFetching } = useSuggest(debouncedQ);
  const suggestions = data?.suggestions ?? [];
  // Option indices: 0..n-1 = suggestions, n = the "Search …" footer action.
  const optionCount = suggestions.length + 1;

  function close() {
    setQ("");
    setActive(0);
    onClose();
  }
  function goDoc(id: string) {
    close();
    navigate(`/documents/${id}`);
  }
  function goSearch() {
    if (term.length === 0) return;
    close();
    navigate(`/search?q=${encodeURIComponent(term)}`);
  }
  function activate(i: number) {
    if (i < suggestions.length) goDoc(suggestions[i]!.id);
    else goSearch();
  }
  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, optionCount - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      activate(active);
    }
  }
  const optionStyle = (selected: boolean) => ({
    padding: "8px 12px",
    width: "100%",
    textAlign: "left" as const,
    borderRadius: "var(--es-radius-sm)",
    background: selected ? "var(--es-surface-3)" : undefined,
  });

  return (
    <Modal opened={opened} onClose={close} title="Search documents" size="lg" closeButtonProps={{ "aria-label": "Close search palette" }}>
      <TextInput
        data-autofocus
        placeholder="Search by identifier or title…"
        aria-label="Search query"
        role="combobox"
        aria-expanded={optionCount > 0}
        aria-controls="cmdk-listbox"
        aria-activedescendant={`cmdk-opt-${active}`}
        value={q}
        onChange={(e) => {
          setQ(e.currentTarget.value);
          setActive(0);
        }}
        onKeyDown={onKeyDown}
        rightSection={isFetching ? <Loader size="xs" /> : null}
      />
      <Stack gap={2} mt="xs" id="cmdk-listbox" role="listbox" aria-label="Search results">
        {suggestions.map((s, i) => (
          <UnstyledButton
            key={s.id}
            id={`cmdk-opt-${i}`}
            role="option"
            aria-selected={active === i}
            onMouseEnter={() => setActive(i)}
            onClick={() => goDoc(s.id)}
            style={optionStyle(active === i)}
          >
            <Text span ff="monospace" size="sm" c="dimmed" mr="sm">
              {s.identifier}
            </Text>
            <Text span>{s.title}</Text>
          </UnstyledButton>
        ))}
        <UnstyledButton
          id={`cmdk-opt-${suggestions.length}`}
          role="option"
          aria-selected={active === suggestions.length}
          aria-disabled={term.length === 0}
          onMouseEnter={() => setActive(suggestions.length)}
          onClick={goSearch}
          style={optionStyle(active === suggestions.length)}
        >
          {term.length === 0 ? (
            <Text span c="dimmed">
              Type to search documents
            </Text>
          ) : (
            <Text span>Search “{term}” →</Text>
          )}
        </UnstyledButton>
      </Stack>
    </Modal>
  );
}
