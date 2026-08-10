import { Button, Paper, Stack, Text, Title } from "@mantine/core";
import { useEffect, useRef } from "react";
import type { JSX } from "react";
import { Link } from "react-router-dom";

export function NotFoundPage(): JSX.Element {
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => headingRef.current?.focus(), []);

  return (
    <Paper
      component="section"
      aria-labelledby="not-found-heading"
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
          <Title id="not-found-heading" ref={headingRef} order={1} size="h2" tabIndex={-1}>
            Page not found
          </Title>
          <Text c="var(--es-text-2)">The page you requested isn't available in EasySynQ.</Text>
        </Stack>
        <Stack gap="xs">
          <Button component={Link} to="/" color="indigo" style={{ minHeight: 44 }}>
            Go to dashboard
          </Button>
          <Button
            component={Link}
            to="/library"
            variant="light"
            color="indigo"
            style={{ minHeight: 44 }}
          >
            Open document library
          </Button>
        </Stack>
      </Stack>
    </Paper>
  );
}
