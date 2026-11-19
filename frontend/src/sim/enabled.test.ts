import { describe, expect, it } from "vitest";
import { isApiSimulationEnabled } from "./enabled";

describe("isApiSimulationEnabled", () => {
  it("is off when not DEV", () => {
    expect(
      isApiSimulationEnabled({
        DEV: false,
        VITE_API_SIMULATION: "1",
      } as ImportMetaEnv),
    ).toBe(false);
  });

  it("defaults ON in DEV; honors explicit on/off", () => {
    expect(
      isApiSimulationEnabled({
        DEV: true,
        VITE_API_SIMULATION: "",
      } as ImportMetaEnv),
    ).toBe(true);
    expect(
      isApiSimulationEnabled({
        DEV: true,
        VITE_API_SIMULATION: "1",
      } as ImportMetaEnv),
    ).toBe(true);
    expect(
      isApiSimulationEnabled({
        DEV: true,
        VITE_API_SIMULATION: "0",
      } as ImportMetaEnv),
    ).toBe(false);
  });
});
