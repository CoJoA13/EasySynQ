import { describe, expect, it } from "vitest";
import type { InterestedParty } from "../../lib/types";
import { bucketByPartyType, PARTY_TYPE_ORDER } from "./board";

function party(over: Partial<InterestedParty> & Pick<InterestedParty, "id">): InterestedParty {
  return {
    register_doc_id: "head",
    party_type: "customer",
    party_name: "x",
    needs_expectations: "y",
    influence: null,
    status: "active",
    last_reviewed_at: null,
    row_version: 1,
    created_at: null,
    updated_at: null,
    ...over,
  };
}

// The golden layout pin (the SWOT-golden / matrix-golden analogue): the board lays out the 7 party-type
// cards in the canonical ISO-4.2 spine order, customer-first through partner.
describe("PARTY_TYPE_ORDER golden layout", () => {
  it("lays out the 7 party-type cards in the canonical clause-4.2 spine order", () => {
    expect(PARTY_TYPE_ORDER).toEqual([
      "customer",
      "regulator",
      "supplier",
      "employee",
      "owner",
      "community",
      "partner",
    ]);
  });
});

describe("bucketByPartyType", () => {
  it("buckets rows by party_type across the full 7-card spine", () => {
    const rows = [
      party({ id: "1", party_type: "customer" }),
      party({ id: "2", party_type: "regulator" }),
      party({ id: "3", party_type: "supplier" }),
      party({ id: "4", party_type: "customer" }),
      party({ id: "5", party_type: "partner" }),
    ];
    const b = bucketByPartyType(rows);
    expect(b.customer.map((r) => r.id)).toEqual(["1", "4"]);
    expect(b.regulator.map((r) => r.id)).toEqual(["2"]);
    expect(b.supplier.map((r) => r.id)).toEqual(["3"]);
    expect(b.partner.map((r) => r.id)).toEqual(["5"]);
    // an unrepresented type is an empty bucket (the card still renders as a completeness prompt)
    expect(b.employee).toEqual([]);
    expect(b.owner).toEqual([]);
    expect(b.community).toEqual([]);
  });

  it("returns empty buckets across the full spine for an empty register", () => {
    const b = bucketByPartyType([]);
    expect(PARTY_TYPE_ORDER.every((t) => b[t].length === 0)).toBe(true);
  });
});
