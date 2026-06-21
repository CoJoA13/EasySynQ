import type { InterestedParty, InterestedPartyType } from "../../lib/types";

// The 7 party-type cards in DISPLAY order — the ISO 9001:2015 clause-4.2 spine, customer-first (the
// most common relevant party) through partner. Pinned by board.test.ts so the layout can't silently
// drift (the SWOT golden-test / risk-matrix golden-test precedent). There is no threshold table here:
// clause 4.2 has no graded axis, so the card IS the party type. Unlike the SWOT board there is NO
// "uncategorized" overflow — party_type is NOT NULL, so every live row buckets into exactly one card.
export const PARTY_TYPE_ORDER: InterestedPartyType[] = [
  "customer",
  "regulator",
  "supplier",
  "employee",
  "owner",
  "community",
  "partner",
];

export type PartyTypeBuckets = Record<InterestedPartyType, InterestedParty[]>;

// Bucket the live working rows by party_type. Pure — the board renders the result verbatim
// (categorical, no re-grade). Every row buckets (party_type is NOT NULL); the buckets cover the full
// 7-card spine so an empty card still renders (a completeness prompt, the SWOT fixed-frame analogue).
export function bucketByPartyType(rows: InterestedParty[]): PartyTypeBuckets {
  const buckets: PartyTypeBuckets = {
    customer: [],
    regulator: [],
    supplier: [],
    employee: [],
    owner: [],
    community: [],
    partner: [],
  };
  for (const row of rows) {
    // Defensive: an unknown party_type (impossible on a server-validated enum) is simply dropped from
    // the board rather than crashing — the table below still lists it.
    if (row.party_type in buckets) buckets[row.party_type].push(row);
  }
  return buckets;
}
