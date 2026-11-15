import { describe, expect, it } from "vitest";
import flow from "../../../packages/contracts/examples/flow.json";
import device from "../../../packages/contracts/examples/device.json";
import snapshot from "../../../packages/contracts/examples/snapshot.json";
import intent from "../../../packages/contracts/examples/intent.json";
import decision from "../../../packages/contracts/examples/decision.json";
import settings from "../../../packages/contracts/examples/settings.json";
import error from "../../../packages/contracts/examples/error.json";
import {
  DecisionSchema,
  DeviceSchema,
  ErrorSchema,
  FlowSchema,
  IntentSchema,
  SettingsSchema,
  SnapshotSchema,
} from "./index";

describe("OpenAPI examples against frontend Zod schemas", () => {
  it("accepts the Flow example", () => {
    expect(FlowSchema.safeParse(flow).success).toBe(true);
  });

  it("accepts the Device example", () => {
    expect(DeviceSchema.safeParse(device).success).toBe(true);
  });

  it("accepts the Snapshot example", () => {
    expect(SnapshotSchema.safeParse(snapshot).success).toBe(true);
  });

  it("accepts the Intent example", () => {
    expect(IntentSchema.safeParse(intent).success).toBe(true);
  });

  it("accepts the Decision example", () => {
    expect(DecisionSchema.safeParse(decision).success).toBe(true);
  });

  it("accepts the Settings example", () => {
    expect(SettingsSchema.safeParse(settings).success).toBe(true);
  });

  it("accepts the Error example", () => {
    expect(ErrorSchema.safeParse(error).success).toBe(true);
  });
});
