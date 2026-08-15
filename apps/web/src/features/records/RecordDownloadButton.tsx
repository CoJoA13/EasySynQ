import { Alert, Button, Stack, Text } from "@mantine/core";
import { useEffect, useRef, useState } from "react";
import { ApiError, useApi } from "../../lib/api";

interface RecordDownload {
  download_url: string;
  sha256: string;
  content_type: string | null;
}

export interface RecordDownloadButtonProps {
  label: string;
  endpoint: string;
  pendingIsNormal?: boolean;
}

function downloadError(error: unknown): string {
  if (error instanceof ApiError && error.status === 403) {
    return "This download is unavailable to you.";
  }
  if (error instanceof ApiError && error.status === 404) {
    return "The requested evidence could not be found.";
  }
  return "Couldn't prepare this download. Try again.";
}

export function RecordDownloadButton({
  label,
  endpoint,
  pendingIsNormal = false,
}: RecordDownloadButtonProps) {
  const api = useApi();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [renditionPending, setRenditionPending] = useState(false);
  const activation = useRef(0);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => {
    activation.current += 1;
    controller.current?.abort();
    controller.current = null;
    setLoading(false);
    setError(null);
    setRenditionPending(false);
    return () => {
      activation.current += 1;
      controller.current?.abort();
      controller.current = null;
    };
  }, [endpoint]);

  async function download() {
    const currentActivation = activation.current + 1;
    activation.current = currentActivation;
    controller.current?.abort();
    const currentController = new AbortController();
    controller.current = currentController;
    setLoading(true);
    setError(null);
    setRenditionPending(false);
    try {
      const result = await api.get<RecordDownload>(endpoint, {
        signal: currentController.signal,
      });
      if (activation.current !== currentActivation) return;
      window.open(result.download_url, "_blank", "noopener,noreferrer");
    } catch (caught) {
      if (activation.current !== currentActivation) return;
      if (
        pendingIsNormal &&
        caught instanceof ApiError &&
        caught.status === 409 &&
        caught.code === "rendition_pending"
      ) {
        setRenditionPending(true);
      } else {
        setError(downloadError(caught));
      }
    } finally {
      if (activation.current === currentActivation) {
        controller.current = null;
        setLoading(false);
      }
    }
  }

  return (
    <Stack gap="xs" align="flex-start">
      <Button
        type="button"
        variant="light"
        loading={loading}
        disabled={loading}
        onClick={() => void download()}
      >
        {label}
      </Button>
      {error && (
        <Alert color="red" role="alert" py="xs">
          {error}
        </Alert>
      )}
      {renditionPending && (
        <Text size="sm" c="dimmed" role="status">
          Structured PDF is not ready yet
        </Text>
      )}
    </Stack>
  );
}
