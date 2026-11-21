import { describe, expect, it } from "vitest";
import { SchedulingApiError } from "../api/scheduling";
import {
  operatorCandidateLabel,
  operatorsBlockerMessage,
  operatorsLoadFromError,
  operatorsLoadFromSuccess,
  usableSchedulingOperators,
} from "./schedulingOperators";

describe("schedulingOperators", () => {
  it("keeps named and nameless operators when email present", () => {
    const usable = usableSchedulingOperators([
      { id: "1", email: "a@x.local", display_name: "A" },
      { id: "2", email: "b@x.local", display_name: "  " },
      { id: "3", email: "", display_name: "C" },
    ]);
    expect(usable.map((o) => o.email)).toEqual(["a@x.local", "b@x.local"]);
  });

  it("falls back label to email when display_name empty", () => {
    expect(
      operatorCandidateLabel({ email: "op@x.local", display_name: "" }),
    ).toBe("op@x.local");
    expect(
      operatorCandidateLabel({ email: "op@x.local", display_name: "Budi" }),
    ).toBe("Budi");
  });

  it("maps empty candidate list to empty state message", () => {
    const state = operatorsLoadFromSuccess([]);
    expect(state.status).toBe("empty");
    expect(operatorsBlockerMessage(state)).toMatch(/Ops = operator/);
  });

  it("maps 403 to denied and other errors to error", () => {
    const denied = operatorsLoadFromError(
      new SchedulingApiError("Daftar operator hanya untuk peran penjadwalan.", 403),
    );
    expect(denied.status).toBe("denied");
    expect(operatorsBlockerMessage(denied)).toMatch(/penjadwalan/);

    const err = operatorsLoadFromError(new Error("jaringan putus"));
    expect(err.status).toBe("error");
    expect(operatorsBlockerMessage(err)).toBe("jaringan putus");
  });

  it("preserves selection identity by email across label fallback", () => {
    const state = operatorsLoadFromSuccess([
      { id: "1", email: "op@x.local", display_name: "" },
    ]);
    expect(state.status).toBe("ok");
    if (state.status !== "ok") return;
    expect(state.items[0].email).toBe("op@x.local");
    expect(operatorCandidateLabel(state.items[0])).toBe("op@x.local");
  });
});
