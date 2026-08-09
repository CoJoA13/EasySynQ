import { Button, Paper, Stack, Text, Title } from "@mantine/core";
import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";

export interface RouteErrorPageProps {
  onRetry: () => void;
  onReload?: () => void;
}

export function RouteErrorPage({
  onRetry,
  onReload = () => window.location.reload(),
}: RouteErrorPageProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);

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
            EasySynQ encountered a problem while displaying this page. Your shared application data
            has not been cleared.
          </Text>
        </Stack>
        <Stack gap="xs">
          <Button color="indigo" style={{ minHeight: 44 }} onClick={onRetry}>
            Try this page again
          </Button>
          <Button component={Link} to="/" variant="light" color="indigo" style={{ minHeight: 44 }}>
            Go to dashboard
          </Button>
          <Button variant="subtle" color="gray" style={{ minHeight: 44 }} onClick={onReload}>
            Reload EasySynQ
          </Button>
        </Stack>
      </Stack>
    </Paper>
  );
}
