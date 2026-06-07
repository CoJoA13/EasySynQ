import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { server } from "../test/msw/server";
import { putToPresigned } from "./upload";

it("PUTs the raw bytes with the content-type and NO bearer (the S3 signature is the auth)", async () => {
  let authHeader: string | null = "unset";
  let contentType: string | null = null;
  let body = "";
  server.use(
    http.put("https://minio.test/staging/x", async ({ request }) => {
      authHeader = request.headers.get("authorization");
      contentType = request.headers.get("content-type");
      body = await request.text();
      return new HttpResponse(null, { status: 200 });
    }),
  );
  await putToPresigned(
    "https://minio.test/staging/x",
    new Blob(["hello"], { type: "text/plain" }),
    "text/plain",
  );
  expect(authHeader).toBeNull(); // never attach the EasySynQ bearer to the presigned PUT
  expect(contentType).toBe("text/plain");
  expect(body).toBe("hello");
});

it("throws on a non-ok upload", async () => {
  server.use(http.put(/^https:\/\/minio\.test\//, () => new HttpResponse(null, { status: 403 })));
  await expect(
    putToPresigned("https://minio.test/x", new Blob(["x"]), "application/octet-stream"),
  ).rejects.toThrow(/Upload failed/);
});
