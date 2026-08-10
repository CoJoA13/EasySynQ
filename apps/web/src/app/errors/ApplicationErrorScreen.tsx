import { Button, Center, Image, Paper, Stack, Text, Title } from "@mantine/core";
import { useEffect, useRef } from "react";

export interface ApplicationErrorScreenProps {
  onReload?: () => void;
}

export function ApplicationErrorScreen({
  onReload = () => window.location.reload(),
}: ApplicationErrorScreenProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    document.title = "EasySynQ — Unavailable";
    headingRef.current?.focus();
  }, []);

  return (
    <Center mih="100dvh" px="lg" py="lg" bg="var(--es-bg)">
      <Paper
        component="main"
        w="100%"
        maw={440}
        miw={0}
        p={{ base: "xl", sm: 48 }}
        radius="md"
        withBorder
        shadow="xs"
        bg="var(--es-surface)"
        aria-live="assertive"
      >
        <Stack align="stretch" gap="xl">
          <Image src="/easysynq-mark.svg" alt="EasySynQ" w={64} h={64} fit="contain" mx="auto" />
          <Stack align="center" gap="sm">
            <Title ref={headingRef} order={1} size="h2" ta="center" tabIndex={-1}>
              EasySynQ couldn't be displayed
            </Title>
            <Text c="var(--es-text-2)" ta="center">
              Reload EasySynQ to start again. If the problem continues, contact your EasySynQ
              administrator.
            </Text>
          </Stack>
          <Stack gap="xs">
            <Button fullWidth color="indigo" style={{ minHeight: 44 }} onClick={onReload}>
              Reload EasySynQ
            </Button>
            <Button
              component="a"
              href="/"
              fullWidth
              variant="subtle"
              color="gray"
              style={{ minHeight: 44 }}
            >
              Go to dashboard
            </Button>
          </Stack>
        </Stack>
      </Paper>
    </Center>
  );
}
