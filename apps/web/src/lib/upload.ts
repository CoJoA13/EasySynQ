// S-web-3: PUT raw file bytes to a presigned MinIO URL. This is the ONE call that bypasses useApi:
// it is cross-origin and carries NO bearer token (the presigned signature IS the auth — an extra
// Authorization header would break the S3 signature). The Content-Type must match the one declared
// to versions:init-upload (MinIO records it; check-in prefers it).
export async function putToPresigned(url: string, file: Blob, contentType: string): Promise<void> {
  // Send an ArrayBuffer (a portable BodyInit) rather than streaming the Blob — fine for
  // document-sized files, and avoids a Blob.stream() dependency some runtimes lack.
  const body = await file.arrayBuffer();
  const resp = await fetch(url, {
    method: "PUT",
    body,
    headers: { "Content-Type": contentType },
  });
  if (!resp.ok) {
    throw new Error(`Upload failed (HTTP ${resp.status})`);
  }
}
