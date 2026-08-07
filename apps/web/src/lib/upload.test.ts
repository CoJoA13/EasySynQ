import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { server } from "../test/msw/server";
import { putToPresigned } from "./upload";

it("PUTs the raw bytes with the content-type and NO bearer (the S3 signature is the auth)", async () => {
  let authHeader: string | null = "unset";
  let checksumHeaders: string[] = [];
  let contentType: string | null = null;
  let body = "";
  server.use(
    http.put("https://minio.test/staging/x", async ({ request }) => {
      authHeader = request.headers.get("authorization");
      checksumHeaders = [...request.headers.keys()].filter((name) =>
        name.startsWith("x-amz-checksum-"),
      );
      contentType = request.headers.get("content-type");
      body = await request.text();
      return new HttpResponse(null, {
        status: 200,
        headers: { "x-amz-version-id": "v-browser-1" },
      });
    }),
  );
  await expect(
    putToPresigned(
      "https://minio.test/staging/x",
      new Blob(["hello"], { type: "text/plain" }),
      "text/plain",
    ),
  ).resolves.toEqual({ versionId: "v-browser-1" });
  expect(authHeader).toBeNull(); // never attach the EasySynQ bearer to the presigned PUT
  expect(checksumHeaders).toEqual([]); // never invent unsigned checksum headers
  expect(contentType).toBe("text/plain");
  expect(body).toBe("hello");
});

it.each([
  ["missing", undefined],
  ["blank", "   "],
  ["literal null", "null"],
  ["too long", "v".repeat(1025)],
])("rejects a successful PUT with a %s version identity", async (_case, versionId) => {
  server.use(
    http.put(
      "https://minio.test/staging/x",
      () =>
        new HttpResponse(null, {
          status: 200,
          headers: versionId === undefined ? undefined : { "x-amz-version-id": versionId },
        }),
    ),
  );

  await expect(
    putToPresigned("https://minio.test/staging/x", new Blob(["x"]), "application/octet-stream"),
  ).rejects.toThrow(/version/i);
});

it("throws on a non-ok upload", async () => {
  server.use(http.put(/^https:\/\/minio\.test\//, () => new HttpResponse(null, { status: 403 })));
  await expect(
    putToPresigned("https://minio.test/x", new Blob(["x"]), "application/octet-stream"),
  ).rejects.toThrow(/Upload failed/);
});
