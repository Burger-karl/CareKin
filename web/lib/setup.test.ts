import { describe, expect, it } from "vitest";
import { shouldSendToSetup } from "./setup";

describe("shouldSendToSetup", () => {
  it("waits until the skip flag has been read from storage", () => {
    // A family follower's first render: patients are cached (empty) but storage hasn't been read yet.
    expect(shouldSendToSetup({ role: "guardian", patientCount: 0, skipSetup: null })).toBe(false);
  });
  it("lets a family follower who pressed Continue through", () => {
    expect(shouldSendToSetup({ role: "guardian", patientCount: 0, skipSetup: true })).toBe(false);
  });
  it("sends a guardian or patient with no care profile to setup", () => {
    expect(shouldSendToSetup({ role: "guardian", patientCount: 0, skipSetup: false })).toBe(true);
    expect(shouldSendToSetup({ role: "patient", patientCount: 0, skipSetup: false })).toBe(true);
  });
  it("leaves everyone else alone", () => {
    expect(shouldSendToSetup({ role: "guardian", patientCount: 1, skipSetup: false })).toBe(false);
    expect(shouldSendToSetup({ role: "family_abroad", patientCount: 0, skipSetup: false })).toBe(false);
    expect(shouldSendToSetup({ role: "patient", patientCount: undefined, skipSetup: false })).toBe(false);
  });
});
