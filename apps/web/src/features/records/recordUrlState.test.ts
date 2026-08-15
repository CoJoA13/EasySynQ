import { describe, expect, it } from "vitest";
import {
  buildRecordsQuery,
  clearRecordCursor,
  parseRecordUrlState,
  pushRecordCursor,
  replaceRecordCriteria,
} from "./recordUrlState";

describe("parseRecordUrlState", () => {
  it("keeps one unique raw value for every Records URL key", () => {
    expect(
      parseRecordUrlState(
        new URLSearchParams(
          "q=REC-100&q=REC-100&record_type=NOT_A_TYPE&disposition_state=UNKNOWN&legal_hold=perhaps&source_document_id=doc-1&captured_by=user-1&cursor=cursor-1",
        ),
      ),
    ).toEqual({
      q: "REC-100",
      record_type: "NOT_A_TYPE",
      disposition_state: "UNKNOWN",
      legal_hold: "perhaps",
      source_document_id: "doc-1",
      captured_by: "user-1",
      cursor: "cursor-1",
    });
  });

  it("drops conflicting duplicates instead of choosing an arbitrary criterion", () => {
    expect(parseRecordUrlState(new URLSearchParams("q=old&q=new&record_type=AUDIT"))).toEqual({
      record_type: "AUDIT",
    });
  });
});

describe("buildRecordsQuery", () => {
  it("serializes nonblank raw values in the API's stable parameter order", () => {
    expect(
      buildRecordsQuery({
        limit: 50,
        legal_hold: "not-a-boolean",
        disposition_state: "not-a-disposition",
        captured_by: "user-1",
        source_document_id: "doc-1",
        record_type: "not-a-record-type",
        q: "REC & title",
        cursor: "page+token",
      }),
    ).toBe(
      "limit=50&cursor=page%2Btoken&q=REC+%26+title&record_type=not-a-record-type&source_document_id=doc-1&captured_by=user-1&disposition_state=not-a-disposition&legal_hold=not-a-boolean",
    );
  });

  it("omits blank criteria", () => {
    expect(
      buildRecordsQuery({
        limit: 50,
        q: "",
        record_type: "",
        disposition_state: "",
        legal_hold: "",
        source_document_id: "",
        captured_by: "",
        cursor: "",
      }),
    ).toBe("limit=50");
  });
});

describe("Records URL history helpers", () => {
  it("replaces criteria, clears the cursor, and preserves unrelated keys", () => {
    expect(
      replaceRecordCriteria(new URLSearchParams("view=compact&q=old&cursor=abc"), {
        q: "new",
      }).toString(),
    ).toBe("view=compact&q=new");
  });

  it("pushes a cursor without changing criteria", () => {
    expect(pushRecordCursor(new URLSearchParams("q=new"), "next-token").toString()).toBe(
      "q=new&cursor=next-token",
    );
  });

  it("removes only the cursor", () => {
    expect(clearRecordCursor(new URLSearchParams("q=new&cursor=bad")).toString()).toBe("q=new");
  });
});
