import test from "node:test";
import assert from "node:assert/strict";
import { verificationText } from "../steps/sources.js";

test("source verification labels distinguish automatic checks from human review and conflicts", () => {
  const unchecked = verificationText({ metadata_verified: false });
  assert.match(unchecked, /Metadata not verified/);
  assert.match(unchecked, /No automatic check recorded/);
  const automatic = verificationText({ metadata_verified: true, verification_method: "automatic", verification: { verdict: "verified" } });
  assert.match(automatic, /verified by an automatic check/);
  assert.match(automatic, /not reviewed by a person/);
  const human = verificationText({ metadata_verified: true, verification_method: "human", verification: { verdict: "mismatch" } });
  assert.match(human, /verified by a person/);
  assert.match(human, /metadata conflict requires review/);
  assert.doesNotMatch(human, /not reviewed by a person/);
  const revoked = verificationText({ metadata_verified: false, verification: { verdict: "mismatch" } });
  assert.match(revoked, /Metadata not verified/);
  assert.match(revoked, /metadata conflict requires review/);
});
