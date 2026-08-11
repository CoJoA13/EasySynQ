import { VisuallyHidden } from "@mantine/core";
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ApiError } from "./api";
import { MutationErrorState } from "./states";

export interface MutationFeedbackInput {
  key: string;
  title: string;
  error: unknown;
  retry?: () => Promise<void>;
  retryLabel?: string;
  dismissLabel: string;
  successMessage?: string;
}

type MutationFeedbackEntry = Omit<MutationFeedbackInput, "error" | "retry"> & {
  message: string;
  retry?: () => Promise<void>;
  retrying: boolean;
};

type MutationFeedbackContextValue = {
  entries: MutationFeedbackEntry[];
  announcement: string;
  report: (input: MutationFeedbackInput) => void;
  dismiss: (key: string) => void;
  retryEntry: (key: string) => Promise<void>;
};

const MutationFeedbackContext = createContext<MutationFeedbackContextValue | null>(null);

export function isRetryableMutationError(error: unknown): boolean {
  if (error instanceof TypeError) return true;
  return (
    error instanceof ApiError &&
    (error.status === 408 || error.status === 429 || (error.status >= 500 && error.status <= 599))
  );
}

function safeMessage(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : "The request didn't complete. Please try again.";
}

export function MutationFeedbackProvider({ children }: { children: ReactNode }) {
  const [entries, setEntries] = useState<MutationFeedbackEntry[]>([]);
  const [announcement, setAnnouncement] = useState("");
  const entriesRef = useRef<MutationFeedbackEntry[]>([]);
  const inFlightKeys = useRef(new Set<string>());

  const updateEntries = useCallback(
    (update: (current: MutationFeedbackEntry[]) => MutationFeedbackEntry[]) => {
      const next = update(entriesRef.current);
      entriesRef.current = next;
      setEntries(next);
    },
    [],
  );

  const dismiss = useCallback(
    (key: string) => {
      updateEntries((current) => current.filter((entry) => entry.key !== key));
    },
    [updateEntries],
  );

  const report = useCallback(
    (input: MutationFeedbackInput) => {
      const entry: MutationFeedbackEntry = {
        key: input.key,
        title: input.title,
        message: safeMessage(input.error),
        retry: input.retry && isRetryableMutationError(input.error) ? input.retry : undefined,
        retryLabel: input.retryLabel,
        dismissLabel: input.dismissLabel,
        successMessage: input.successMessage,
        retrying: inFlightKeys.current.has(input.key),
      };

      updateEntries((current) => {
        const index = current.findIndex((existing) => existing.key === input.key);
        if (index < 0) return [...current, entry];
        return [...current.slice(0, index), entry, ...current.slice(index + 1)];
      });
    },
    [updateEntries],
  );

  const retryEntry = useCallback(
    async (key: string) => {
      if (inFlightKeys.current.has(key)) return;

      const entry = entriesRef.current.find((candidate) => candidate.key === key);
      if (!entry?.retry) return;

      inFlightKeys.current.add(key);
      updateEntries((current) =>
        current.map((candidate) =>
          candidate.key === key ? { ...candidate, retrying: true } : candidate,
        ),
      );

      try {
        await entry.retry();
        dismiss(key);
        setAnnouncement(entry.successMessage ?? "");
      } catch (error) {
        updateEntries((current) =>
          current.map((candidate) =>
            candidate.key === key
              ? {
                  ...candidate,
                  message: safeMessage(error),
                  retry: isRetryableMutationError(error) ? candidate.retry : undefined,
                  retrying: false,
                }
              : candidate,
          ),
        );
      } finally {
        inFlightKeys.current.delete(key);
      }
    },
    [dismiss, updateEntries],
  );

  const value = useMemo(
    () => ({ entries, announcement, report, dismiss, retryEntry }),
    [announcement, dismiss, entries, report, retryEntry],
  );

  return (
    <MutationFeedbackContext.Provider value={value}>{children}</MutationFeedbackContext.Provider>
  );
}

export function useMutationFeedback(): Pick<MutationFeedbackContextValue, "report" | "dismiss"> {
  const context = useContext(MutationFeedbackContext);
  if (!context) throw new Error("useMutationFeedback must be used within MutationFeedbackProvider");
  return { report: context.report, dismiss: context.dismiss };
}

export function MutationFeedbackOutlet() {
  const context = useContext(MutationFeedbackContext);
  if (!context)
    throw new Error("MutationFeedbackOutlet must be used within MutationFeedbackProvider");

  return (
    <>
      {context.entries.map((entry) => (
        <MutationErrorState
          key={entry.key}
          title={entry.title}
          message={entry.message}
          onRetry={entry.retry ? () => void context.retryEntry(entry.key) : undefined}
          retrying={entry.retrying}
          onDismiss={() => context.dismiss(entry.key)}
          retryLabel={entry.retryLabel}
          dismissLabel={entry.dismissLabel}
        />
      ))}
      <VisuallyHidden role="status" aria-live="polite" aria-atomic="true">
        {context.announcement}
      </VisuallyHidden>
    </>
  );
}
