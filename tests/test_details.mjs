import assert from "node:assert/strict";
import test from "node:test";
import { absoluteTime, remainingSeconds, resetLines, windowTitle } from "../mods/herdr-lazyagent/details.js";

const now = Date.parse("2026-11-12T12:00:00Z");

test("model and feature windows have distinct bar labels", () => {
  assert.equal(windowTitle({ label: "7-day" }), "7-day");
  assert.equal(windowTitle({ label: "7-day", scope: "Fable" }), "7-day · Fable");
  assert.equal(windowTitle({ label: "7-day", scope: "Code review" }), "7-day · Code review");
});

test("absolute reset time stays correct when a snapshot is reused", () => {
  const at = "2026-11-12T13:00:00Z";
  assert.equal(remainingSeconds(at, 9000, now / 1000 - 5000, now), 3600);
  assert.equal(remainingSeconds(at, 9000, now / 1000, now + 1800000), 1800);
  assert.equal(remainingSeconds(at, 9000, now / 1000, now + 7200000), 0);
});

test("countdowns without a usable timestamp fall back to snapshot age", () => {
  assert.equal(remainingSeconds("invalid", 3600, now / 1000 - 600, now), 3000);
  assert.equal(remainingSeconds(null, 60, now / 1000 - 600, now), 0);
  assert.equal(absoluteTime("invalid"), "");
});

test("banked reset counts, expiry and recurring availability stay separate", () => {
  const reset = {
    left: 0, total: 1, usable: false, status: "Already used",
    expires_at: "2026-11-13T12:00:00Z",
    next_available_at: "2026-11-12T14:00:00Z",
    text: "old cached countdown that must not be displayed",
  };
  const lines = resetLines(reset, now / 1000, now);
  assert.equal(lines[0], "0 of 1 left · Already used");
  assert.ok(lines[1].startsWith("Expiry: in 1d 0h ("));
  assert.ok(lines[2].startsWith("Next available in 2h 0m ("));
  const later = resetLines(reset, now / 1000, now + 7200000);
  assert.ok(later[2].startsWith("Next available now ("));
});

test("unknown dates and totals do not invent expiry or reset grants", () => {
  assert.deepEqual(resetLines({ left: 2, total: 0, usable: true }, now / 1000, now), ["2 left · Usable now"]);
  assert.deepEqual(resetLines({ left: 0, total: 1, usable: false }, now / 1000, now), ["0 of 1 left · Not usable now"]);
});
