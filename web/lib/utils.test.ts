import { describe, expect, it } from "vitest";
import { formatMoney, haversineM, initials, splitList } from "./utils";
import { priceMinor, slotProblem } from "./slot";

describe("utils", () => {
  it("formats kobo as naira", () => {
    expect(formatMoney(34_000_000)).toBe("₦340,000");
    expect(formatMoney(550_000, "NGN")).toBe("₦5,500");
    expect(formatMoney(1050, "GBP")).toBe("£10.50");
  });
  it("makes initials", () => {
    expect(initials("Ngozi Eze")).toBe("NE");
    expect(initials("Ada")).toBe("AD");
  });
  it("splits care-profile lists", () => {
    expect(splitList("Travel, Cold\nDehydration")).toEqual(["Travel", "Cold", "Dehydration"]);
    expect(splitList(null)).toEqual([]);
  });
  it("measures distance", () => {
    const d = haversineM({ latitude: 6.4413, longitude: 7.4988 }, { latitude: 6.4413, longitude: 7.5088 });
    expect(Math.round(d)).toBeGreaterThan(1000);
    expect(Math.round(d)).toBeLessThan(1200);
  });
});

describe("slot", () => {
  it("prices the same way the server does", () => {
    // 8 hours at ₦8,500/hr
    expect(priceMinor(850_000, { type: "hourly", start: "2030-01-01T08:00", end: "2030-01-01T16:00" })).toBe(6_800_000);
  });
  it("rejects short and past slots", () => {
    expect(slotProblem({ type: "hourly", start: "2030-01-01T08:00", end: "2030-01-01T08:30" })).toMatch(/1 hour/);
    expect(slotProblem({ type: "hourly", start: "2001-01-01T08:00", end: "2001-01-01T16:00" })).toMatch(/passed/);
    expect(slotProblem({ type: "hourly", start: "2030-01-01T08:00", end: "2030-01-01T16:00" })).toBeNull();
  });
});
