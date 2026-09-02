import { useMantineColorScheme } from "@mantine/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef } from "react";
import { useApi } from "../../lib/api";
import { useMutationFeedback } from "../../lib/mutationFeedback";
import { type Me, useMe } from "./useMe";

export type ColorSchemePreference = Me["color_scheme"];

export const COLOR_SCHEME_PREFERENCES: readonly ColorSchemePreference[] = [
  "LIGHT",
  "DARK",
  "AUTO",
] as const;

/** The wire enum is upper-case; Mantine's own scheme values are lower-case. */
const TO_MANTINE = { LIGHT: "light", DARK: "dark", AUTO: "auto" } as const;
const FROM_MANTINE = { light: "LIGHT", dark: "DARK", auto: "AUTO" } as const;

export interface ColorSchemePreferenceState {
  /**
   * The preference actually in effect, and the right value for a control to display in EVERY state.
   *
   * Deliberately NOT the account value. Before `/me` resolves the account value is undefined but the
   * cached one is already painted; after a failed write the account never took the new value while
   * the page did. A control bound to the account would show AUTO over a light page in both cases —
   * which is how the mismatch was found, by the failed-write test.
   */
  value: ColorSchemePreference;
  /** The stored account value, or `undefined` until `/me` resolves. */
  preference: ColorSchemePreference | undefined;
  select: (next: ColorSchemePreference) => void;
  saving: boolean;
}

/**
 * R69: reconcile the interface colour scheme between its two stores.
 *
 * The ACCOUNT is the authority and `localStorage` is a pre-auth cache, because neither alone is
 * sufficient. The SPA keeps its tokens in memory only, so every reload starts logged-out and
 * re-authenticates; during that window there is no `/me` to read, and an account-only preference
 * would paint the wrong scheme on every single load. A browser-only preference, conversely, does not
 * follow the user to a second machine. Mantine's own manager already writes the cache — this hook
 * supplies the writer it never had, plus the reconcile.
 *
 * ⚠ The reconcile is keyed on the ACCOUNT value changing, not on a mismatch between the two stores.
 * A mismatch test would fight the user: the moment they pick DARK, the still-cached `/me` says AUTO,
 * the effect sees a mismatch and reverts them. Tracking the last account value actually applied
 * means a `/me` refetch that returns an unchanged value is a no-op, and only a genuine change
 * elsewhere — another device, an admin action — moves the local scheme.
 */
export function useColorSchemePreference(): ColorSchemePreferenceState {
  const { data: me } = useMe();
  const { colorScheme, setColorScheme } = useMantineColorScheme();
  const api = useApi();
  const queryClient = useQueryClient();
  const { report, dismiss } = useMutationFeedback();

  const preference = me?.color_scheme;
  const lastAppliedAccountValue = useRef<ColorSchemePreference | null>(null);
  const userChoseThisSession = useRef(false);
  // The user's CURRENT intent, so a retry cannot resurrect a superseded choice. `select` captures
  // its argument, so a banner raised for DARK would keep offering to write DARK even after the user
  // moved on to LIGHT and that write succeeded.
  const desired = useRef<ColorSchemePreference | null>(null);

  useEffect(() => {
    // An explicit choice ends the automatic reconcile for the rest of the session. Without this the
    // background sync can overwrite a deliberate action, and it is not a narrow window: `/me` may
    // still be in flight when the control is first clicked, in which case the account value lands
    // AFTER the click and silently undoes it. That was observed, not theorised — it is what the
    // failed-write test caught. The user's own action is the most recent evidence of intent, so it
    // outranks a value fetched in the background; a reload reconciles from the account again.
    if (userChoseThisSession.current) return;
    if (!preference) return;
    if (lastAppliedAccountValue.current === preference) return;
    lastAppliedAccountValue.current = preference;
    setColorScheme(TO_MANTINE[preference]);
  }, [preference, setColorScheme]);

  const mutation = useMutation({
    mutationFn: (next: ColorSchemePreference) =>
      api.send<Me>("PATCH", "/api/v1/me/preferences", { color_scheme: next }),
    onSuccess: (updated) => {
      // A successful write makes any standing failure banner false. Without this it persists for the
      // rest of the session, still offering to "try again" something that has already succeeded.
      dismiss("color-scheme-preference");
      // Advance the cached `/me` so the reconcile above sees the new account value as already
      // applied. Without this the next refetch returns the OLD value, which is a genuine change
      // relative to the ref, and the user's choice is undone a few seconds after they made it.
      queryClient.setQueryData<Me>(["me"], (old) => (old ? { ...old, ...updated } : updated));
    },
  });

  const select = useCallback(
    (next: ColorSchemePreference) => {
      // Apply locally first, and keep it applied even if the write fails. The local scheme is a
      // legitimate browser-level preference on its own, so snapping the page back would punish the
      // user for a server problem; the report tells them it did not reach their account.
      userChoseThisSession.current = true;
      desired.current = next;
      lastAppliedAccountValue.current = next;
      setColorScheme(TO_MANTINE[next]);
      mutation.mutate(next, {
        onError: (error) => {
          // Deliberately do NOT reset the ref here. Doing so was the first draft, and it defeated
          // the very thing this branch exists for: with the ref cleared, the reconcile effect sees
          // the unchanged account value as a fresh change and snaps the page back, so the user's
          // choice vanished the instant the save failed. Leaving the ref means the choice survives
          // as a browser-level preference for the session; the account is still the authority, so a
          // reload reconciles it away, and the report below is what tells them it did not save.
          report({
            key: "color-scheme-preference",
            title: "Interface theme not saved",
            error,
            retry: async () => {
              // Re-read the current intent rather than replaying the captured `next`.
              await mutation.mutateAsync(desired.current ?? next);
            },
            dismissLabel: "Dismiss",
            successMessage: "Interface theme saved.",
          });
        },
      });
    },
    [mutation, report, setColorScheme],
  );

  return { value: FROM_MANTINE[colorScheme], preference, select, saving: mutation.isPending };
}
