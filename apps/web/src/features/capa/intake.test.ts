import { expect, test } from "vitest";
import { DISPOSITION_LABEL, DISPOSITIONS, NCR_SOURCE_LABEL, NCR_SOURCES } from "./intake";

test("every NCR disposition has a label (and there are exactly the 6 ISO 8.7 tokens)", () => {
  expect(DISPOSITIONS).toEqual(["use_as_is", "rework", "scrap", "return", "concession", "regrade"]);
  for (const d of DISPOSITIONS) expect(DISPOSITION_LABEL[d]).toBeTruthy();
});

test("every NCR source has a label", () => {
  expect(NCR_SOURCES).toEqual(["audit", "process", "complaint", "internal"]);
  for (const s of NCR_SOURCES) expect(NCR_SOURCE_LABEL[s]).toBeTruthy();
});
