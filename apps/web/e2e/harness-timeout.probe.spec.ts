import { test } from "@playwright/test";

test("waits long enough to exercise parent timeout handling", async () => {
  await new Promise((resolve) => setTimeout(resolve, 60_000));
});
