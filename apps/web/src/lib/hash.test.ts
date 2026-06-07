import { expect, it } from "vitest";
import { sha256Hex } from "./hash";

it("hashes bytes to the canonical SHA-256 hex (crypto.subtle works in the test env)", async () => {
  // SHA-256("abc") — a NIST test vector.
  const blob = new Blob(["abc"]);
  expect(await sha256Hex(blob)).toBe(
    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
  );
});

it("hashes empty input", async () => {
  expect(await sha256Hex(new Blob([]))).toBe(
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  );
});

it("hashes a File (the authoring upload path)", async () => {
  const f = new File(["abc"], "a.txt", { type: "text/plain" });
  expect(await sha256Hex(f)).toBe(
    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
  );
});
