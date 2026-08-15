import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../test/msw/server";
import { apiGet } from "./api";

test("apiGet rejects a pre-aborted request before a response completes", async () => {
  let responseCompleted = false;
  server.use(
    http.get("/api/v1/records", () => {
      responseCompleted = true;
      return HttpResponse.json({ data: [] });
    }),
  );
  const controller = new AbortController();
  controller.abort();

  await expect(
    apiGet("/api/v1/records", "token", { signal: controller.signal }),
  ).rejects.toMatchObject({
    name: "AbortError",
  });
  expect(responseCompleted).toBe(false);
});
