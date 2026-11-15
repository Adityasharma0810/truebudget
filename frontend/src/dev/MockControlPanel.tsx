import { useEffect, useState } from "react";
import { SlidersHorizontal, RotateCcw, Play } from "lucide-react";
import { useApi } from "../lib/api";
import { Button, Modal, useTask, Badge, EmulatedBadge } from "../components/ui";
import { Toggle } from "../features/settings/Settings";
import type { MockControl, Capabilities } from "../types";
export default function DevControlPanel() {
  const [open, setOpen] = useState(false);
  const { data, refetch } = useApi<MockControl>("/dev/control", { enabled: open });
  const { data: capabilities } = useApi<Capabilities>("/capabilities");
  const task = useTask("Agent configuration updated.");
  useEffect(() => {
    const listener = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === "m") {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, []);
  return (
    <Modal drawer open={open} onClose={() => setOpen(false)} title="Dev control panel">
      <EmulatedBadge />
      <p className="field-hint">
        Development controls · Ctrl + Shift + M. These controls affect the live agent runtime.
      </p>
      {data && (
        <>
          <div className="mock-toggles">
            {(["offline", "stale", "slow", "rateLimit"] as const).map((key) => (
              <div key={key} className="setting-row">
                <h3>{key}</h3>
                <Toggle
                  label={key}
                  checked={data[key]}
                  onChange={(value) =>
                    task.mutate(
                      { path: "/dev/control", method: "PATCH", body: { [key]: value } },
                      { onSuccess: () => void refetch() },
                    )
                  }
                />
              </div>
            ))}
          </div>
          <label className="field-label">
            Engine speed (simulation seconds / wall second)
            <select
              value={data.speed}
              onChange={(e) =>
                task.mutate({
                  path: "/dev/control",
                  method: "PATCH",
                  body: { speed: Number(e.target.value) },
                })
              }
            >
              <option value={1}>1× · real time</option>
              <option value={10}>10× · default</option>
              <option value={30}>30× · fast</option>
            </select>
          </label>
          <label className="field-label">
            Seed
            <input
              type="number"
              defaultValue={data.seed}
              onBlur={(e) =>
                task.mutate({
                  path: "/dev/control",
                  method: "PATCH",
                  body: { seed: Number(e.target.value) },
                })
              }
            />
          </label>
          <div className="mock-scenarios">
            {capabilities?.scenarios.map((s) => (
              <Button
                key={s.id}
                variant="secondary"
                pending={task.isPending}
                onClick={() => task.mutate({ path: `/scenarios/${s.id}` })}
              >
                <Play size={12} />
                {s.name}
              </Button>
            ))}
          </div>
          <Button
            variant="secondary"
            className="full-width"
            onClick={() =>
              task.mutate({ path: "/dev/reset" }, { onSuccess: () => location.reload() })
            }
          >
            <RotateCcw size={13} /> Reset & reload
          </Button>
        </>
      )}
    </Modal>
  );
}
