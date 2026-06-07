// S-web-3: client-side SHA-256 (hex) of a file's bytes — the content-addressed object key the vault
// trusts as the version's identity (the API never re-hashes; D1 — bytes flow client↔MinIO directly).
// `crypto.subtle` requires a secure context (HTTPS or localhost), already satisfied on this stack.
export async function sha256Hex(blob: Blob): Promise<string> {
  const buf = await blob.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", buf);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
