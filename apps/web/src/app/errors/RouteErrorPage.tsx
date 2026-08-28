import { Button, Paper, Stack, Text, Title } from "@mantine/core";
import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { useRouteErrorChromeOwnership } from "../../lib/routeChrome";

export interface RouteErrorPageProps {
  onRetry: () => void;
  onReload?: () => void;
  error?: unknown;
}

/**
 * U15 follow-on: a failed dynamic import (the stale-chunk-after-deploy case, very real for an
 * app served from a baked build) cannot be recovered by remounting — React.lazy memoizes the
 * rejected payload — so "Try this page again" would be a permanent no-op. Detect it and lead
 * with the reload instead.
 */
export function isChunkLoadError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error ?? "");
  return /dynamically imported module|Importing a module script failed|Loading chunk|ChunkLoadError/i.test(
    message,
  );
}

export function RouteErrorPage({
  onRetry,
  onReload = () => window.location.reload(),
  error,
}: RouteErrorPageProps) {
  const staleChunk = isChunkLoadError(error);
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteErrorChromeOwnership();

  useEffect(() => {
    const previousTitle = document.title;
    document.title = "EasySynQ — Page unavailable";
    headingRef.current?.focus();
    return () => {
      document.title = previousTitle;
    };
  }, []);

  return (
    <Paper
      component="section"
      role="region"
      aria-live="assertive"
      aria-labelledby="route-error-heading"
      w="100%"
      maw={560}
      miw={0}
      mx="auto"
      mt="xl"
      p={{ base: "xl", sm: 40 }}
      radius="md"
      withBorder
      bg="var(--es-surface)"
    >
      <Stack gap="lg">
        <Stack gap="sm">
          <Title id="route-error-heading" ref={headingRef} order={1} size="h2" tabIndex={-1}>
            This page couldn't be displayed
          </Title>
          <Text c="var(--es-text-2)">
            {staleChunk
              ? "This page could not be downloaded — EasySynQ was probably updated while your tab was open. Reloading picks up the new version. Your shared application data has not been cleared."
              : "EasySynQ encountered a problem while displaying this page. Your shared application data has not been cleared."}
          </Text>
        </Stack>
        <Stack gap="xs">
          {/* A stale chunk can only be fixed by a reload, so it leads; retrying the render
              would re-read the same memoized rejection forever. */}
          {staleChunk ? null : (
            <Button color="indigo" style={{ minHeight: 44 }} onClick={onRetry}>
              Try this page again
            </Button>
          )}
          <Button
            color={staleChunk ? "indigo" : "gray"}
            variant={staleChunk ? "filled" : "subtle"}
            style={{ minHeight: 44 }}
            onClick={onReload}
          >
            Reload EasySynQ
          </Button>
          <Button component={Link} to="/" variant="light" color="indigo" style={{ minHeight: 44 }}>
            Go to dashboard
          </Button>
        </Stack>
      </Stack>
    </Paper>
  );
}
