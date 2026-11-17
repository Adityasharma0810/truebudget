import { useState } from "react";
import { motion } from "motion/react";
import {
  BrainCircuit,
  Check,
  X,
  RotateCcw,
  Play,
  ArrowUpRight,
  ShieldCheck,
  Info,
} from "lucide-react";
import { useApi } from "../../lib/api";
import type { MLData } from "../../types";
import {
  SectionHeading,
  CardHeader,
  Button,
  Badge,
  useTask,
  EmptyState,
  ErrorState,
  ProgressBar,
  Skeletons,
} from "../../components/ui";
import { percent } from "../../lib/format";
export default function ML() {
  const { data, error, refetch } = useApi<MLData>("/ml/metrics");
  const task = useTask("Model request completed. See the returned status.");
  const correction = useTask("Correction reviewed.");
  const [cell, setCell] = useState<string | null>(null);
  if (error) return <ErrorState message={error.message} retry={() => void refetch()} />;
  if (!data) return <Skeletons />;
  const queue = data.corrections.filter((c) => c.status === "pending");
  return (
    <>
      <SectionHeading
        title="Intelligence, with its workings shown."
        description="Classifier quality, honest uncertainty, and a human in the loop."
      >
        <Badge tone="emulated">Synthetic evaluation</Badge>
        <Button
          variant="secondary"
          pending={task.isPending}
          onClick={() => task.mutate({ path: "/ml/rollback" })}
        >
          <RotateCcw size={13} /> Roll back model
        </Button>
        <Button pending={task.isPending} onClick={() => task.mutate({ path: "/ml/retrain" })}>
          <BrainCircuit size={14} /> Retrain locally
        </Button>
      </SectionHeading>
      <div className="ml-stats">
        {[
          {
            title: "Classifier accuracy",
            value: data.accuracy,
            desc:
              data.baseline === null
                ? "no labelled baseline yet"
                : "vs port-rule baseline " + percent(data.baseline),
          },
          { title: "Macro F1 score", value: data.f1, desc: "All five classes, including unknown" },
          {
            title: "Encrypted subset",
            value: data.encrypted,
            desc: "Lower certainty, not guessed labels",
          },
        ].map((m) => (
          <section className="card ml-stat" key={m.title}>
            <span>{m.title}</span>
            <strong>{m.value === null ? "Unknown" : percent(m.value)}</strong>
            <p>{m.desc}</p>
            {m.value !== null && <ProgressBar value={m.value} />}
          </section>
        ))}
      </div>
      <div className="ml-grid">
        <section className="card matrix-card">
          <CardHeader
            title="Where the classifier gets it right"
            subtitle="Rows: actual class · Columns: predicted class"
          />
          {data.labels.length === 0 || data.matrix.length === 0 ? (
            <EmptyState
              title="No labelled evaluation yet."
              description="The classifier is running its rule baseline. A confusion matrix appears after a real local model has been trained and evaluated."
            />
          ) : (
            <>
              <div className="confusion-matrix">
                <div />
                {data.labels.map((l) => (
                  <div key={l} className="matrix-label">
                    {l}
                  </div>
                ))}
                {data.matrix.map((row, i) => (
                  <div className="matrix-row" key={i}>
                    <div className="matrix-label">{data.labels[i]}</div>
                    {row.map((v, j) => (
                      <motion.button
                        key={j}
                        initial={{ opacity: 0, scale: 0.9 }}
                        animate={{ opacity: 1, scale: 1 }}
                        transition={{ delay: (i * 5 + j) * 0.015 }}
                        onClick={() =>
                          setCell(`${data.labels[i]} → ${data.labels[j]}: ${v} samples`)
                        }
                        title={`${data.labels[i]} predicted as ${data.labels[j]}: ${v}`}
                        style={{
                          background: `rgba(88,130,228,${0.06 + (v / 1000) * 0.62})`,
                          color: "#142d58",
                        }}
                      >
                        {v}
                      </motion.button>
                    ))}
                  </div>
                ))}
              </div>
              <div className="matrix-footer">
                <span>{cell ?? "Select a cell to inspect the sample count."}</span>
                <span>
                  Low <i /> High
                </span>
              </div>
            </>
          )}
        </section>
        <section className="card review-card">
          <CardHeader
            title="A little human insight"
            subtitle={`${queue.length} correction${queue.length === 1 ? "" : "s"} waiting for review`}
          />
          {queue.length ? (
            <div className="correction-stack">
              <motion.div
                className="correction-review"
                key={queue[0].id}
                drag="x"
                dragConstraints={{ left: 0, right: 0 }}
                onDragEnd={(_, info) => {
                  if (Math.abs(info.offset.x) > 80)
                    correction.mutate({
                      path: "/ml/corrections",
                      body: {
                        id: queue[0].id,
                        status: info.offset.x > 0 ? "approved" : "rejected",
                      },
                    });
                }}
              >
                <span className="correction-avatar">
                  <BrainCircuit size={26} />
                </span>
                <h3>{queue[0].device}</h3>
                <p>Suggested correction</p>
                <div className="correction-change">
                  <Badge>{queue[0].from}</Badge>
                  <ArrowUpRight size={18} />
                  <Badge tone="blue">{queue[0].to}</Badge>
                </div>
                <p className="field-hint">
                  Review metadata before teaching the local model.
                  <br />
                  Drag right to approve, left to reject, or use the buttons.
                </p>
                <div className="review-actions">
                  <Button
                    variant="secondary"
                    pending={correction.isPending}
                    onClick={() =>
                      correction.mutate({
                        path: "/ml/corrections",
                        body: { id: queue[0].id, status: "rejected" },
                      })
                    }
                  >
                    <X size={13} /> Reject
                  </Button>
                  <Button
                    pending={correction.isPending}
                    onClick={() =>
                      correction.mutate({
                        path: "/ml/corrections",
                        body: { id: queue[0].id, status: "approved" },
                      })
                    }
                  >
                    <Check size={13} /> Approve
                  </Button>
                </div>
              </motion.div>
            </div>
          ) : (
            <EmptyState
              title="Your review queue is clear."
              description="Corrections made in the usage tree will appear here before the next local training run."
            />
          )}
        </section>
      </div>
      <section className="card model-timeline">
        <CardHeader
          title="A model you can always roll back"
          subtitle="Model history stays local. No payload data leaves the agent."
        />
        <div className="model-versions">
          {data.models.length === 0 ? (
            <EmptyState
              title="No local model yet."
              description="The classifier is using its rule baseline. A version appears here only after a real local training run."
            />
          ) : (
            data.models.map((m) => (
              <div key={m.version}>
                <span className={`model-point ${m.status === "active" ? "model-active" : ""}`}>
                  <BrainCircuit size={19} />
                </span>
                <div>
                  <h3>
                    {m.version}{" "}
                    <Badge tone={m.status === "active" ? "blue" : "neutral"}>{m.status}</Badge>
                  </h3>
                  <p>
                    {m.date} · {m.accuracy === null ? "unknown" : percent(m.accuracy)} accuracy
                  </p>
                </div>
              </div>
            ))
          )}
        </div>
      </section>
      <div className="page-note">
        <Info size={13} /> Model versions are read from the agent's local database. Retrain and
        rollback are refused until a real training job exists; no synthetic model metrics are shown.
      </div>
    </>
  );
}
