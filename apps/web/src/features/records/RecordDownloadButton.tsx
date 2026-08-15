import { Alert, Button, Stack, Text } from "@mantine/core";
import { useEffect, useState } from "react";
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

  useEffect(() => {
    setLoading(false);
    setError(null);
    setRenditionPending(false);
  }, [endpoint]);

  async function download() {
    setLoading(true);
    setError(null);
    setRenditionPending(false);
    try {
      const result = await api.get<RecordDownload>(endpoint);
      window.open(result.download_url, "_blank", "noopener,noreferrer");
    } catch (caught) {
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
      setLoading(false);
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
