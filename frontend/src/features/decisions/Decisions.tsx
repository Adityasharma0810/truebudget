import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AnimatePresence, motion } from "motion/react";
import {
  Search,
  Download,
  ShieldCheck,
  Check,
  RotateCcw,
  Flag,
  ChevronDown,
  ArrowRight,
  Sparkles,
  Send,
  Hash,
  FileJson,
  CheckCheck,
  Info,
} from "lucide-react";
import { useApi, request } from "../../lib/api";
import { useConsole } from "../../app/Layout";
import {
  SectionHeading,
  Button,
  Badge,
  CardHeader,
  EmptyState,
  ErrorState,
  Skeletons,
  useTask,
  ExperimentalBadge,
  ProgressBar,
  useToasts,
} from "../../components/ui";
import { download, csv, dateTime } from "../../lib/format";
import type { Decision, AskAnswer } from "../../types";
export default function Decisions() {
  const { capabilities } = useConsole();
  const { data: decisions, error, isLoading, refetch } = useApi<Decision[]>("/decisions");
  const [search, setSearch] = useState("");
  const [verdict, setVerdict] = useState("all");
  const [params] = useSearchParams();
  const [expanded, setExpanded] = useState<string | null>(params.get("entry"));
  const [limit, setLimit] = useState(12);
  const [verified, setVerified] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [ask, setAsk] = useState(false);
  const [question, setQuestion] = useState("Why did you prioritize the call?");
  const [answer, setAnswer] = useState<AskAnswer | null>(null);
  const [streamed, setStreamed] = useState("");
  const chat = useTask<AskAnswer>("");
  const filtered = (decisions ?? []).filter(
    (d) =>
      (verdict === "all" || d.verdict === verdict) &&
      `${d.trigger} ${d.reason}`.toLowerCase().includes(search.toLowerCase()),
  );
  useEffect(() => {
    if (!answer) return;
    let i = 0;
    setStreamed("");
    const words = answer.text.split(" ");
    const timer = setInterval(() => {
      i += 3;
      setStreamed(words.slice(0, i).join(" "));
      if (i >= words.length) clearInterval(timer);
    }, 35);
    return () => clearInterval(timer);
  }, [answer]);
  async function verify() {
    setVerifying(true);
    try {
      const result = await request<{ valid: boolean; count: number }>("GET", "/decisions/verify");
      setVerified(result.valid);
      useToasts
        .getState()
        .push(
          result.valid
            ? `${result.count} decisions verified. SHA-256 links are intact.`
            : "The chain could not be verified.",
          !result.valid,
        );
    } catch (e) {
      useToasts.getState().push(String(e), true);
    } finally {
      setVerifying(false);
    }
  }
  return (
    <>
      <SectionHeading
        title="Every change has a reason."
        description="A transparent record of what your agent did, why, and whether it helped."
      >
        <Button variant="secondary" pending={verifying} onClick={verify}>
          <ShieldCheck size={14} />
          {verified ? "Hash chain verified" : "Verify hash chain"}
        </Button>
        <Button variant="secondary" onClick={() => download("decision-log.json", decisions ?? [])}>
          <Download size={13} /> JSON
        </Button>
        <Button
          variant="secondary"
          onClick={() =>
            download(
              "decision-log.csv",
              csv([
                ["id", "timestamp", "trigger", "reason", "verdict", "hash"],
                ...(decisions ?? []).map((d) => [
                  d.id,
                  d.ts,
                  d.trigger,
                  d.reason,
                  d.verdict,
                  d.hash,
                ]),
              ]),
              "csv",
            )
          }
        >
          <Download size={13} /> CSV
        </Button>
      </SectionHeading>
      <div className="filter-toolbar">
        <div className="search-field">
          <Search size={15} />
          <input
            aria-label="Search decisions"
            placeholder="Search decisions…"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setLimit(12);
            }}
          />
        </div>
        <select
          aria-label="Filter verdict"
          value={verdict}
          onChange={(e) => setVerdict(e.target.value)}
        >
          <option value="all">All verdicts</option>
          <option value="kept">Kept</option>
          <option value="rolled_back">Rolled back</option>
          <option value="escalated">Escalated</option>
        </select>
        <span className="toolbar-spacer" />
        {capabilities?.flags.ask && (
          <Button variant="secondary" onClick={() => setAsk(!ask)}>
            <Sparkles size={13} /> Ask the router <ExperimentalBadge />
          </Button>
        )}
      </div>
      {ask && (
        <section className="card ask-card">
          <CardHeader
            title="A little context from your router"
            subtitle="Answers come from a small local model; check the log."
          >
            <Badge tone="emulated">Canned demo answer</Badge>
          </CardHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              chat.mutate({ path: "/ask", body: { question } }, { onSuccess: setAnswer });
            }}
          >
            <input
              aria-label="Ask the router"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              required
              maxLength={1000}
            />
            <Button pending={chat.isPending} type="submit">
              <Send size={14} /> Ask
            </Button>
          </form>
          {answer && (
            <div className="ask-answer">
              <Sparkles size={18} />
              <div>
                <p>{streamed}</p>
                <div className="citation-chips">
                  {answer.citations.map((id) => (
                    <button
                      key={id}
                      onClick={() => {
                        setExpanded(id);
                        setSearch("");
                        setVerdict("all");
                        setLimit(decisions?.length ?? 100);
                        setTimeout(
                          () =>
                            document
                              .getElementById(id)
                              ?.scrollIntoView({ behavior: "smooth", block: "center" }),
                          100,
                        );
                      }}
                    >
                      <Hash size={10} />
                      {id}
                    </button>
                  ))}
                </div>
                <small>{answer.disclaimer}</small>
              </div>
            </div>
          )}
        </section>
      )}
      {isLoading ? (
        <Skeletons />
      ) : error ? (
        <ErrorState message={error.message} retry={() => void refetch()} />
      ) : !filtered.length ? (
        <div className="card">
          <EmptyState
            title="No decisions match"
            description="Try a different search or clear the verdict filter."
          />
        </div>
      ) : (
        <div className={`decision-timeline ${verifying ? "verifying-chain" : ""}`}>
          {filtered.slice(0, limit).map((d, i) => (
            <motion.article
              className={`card decision-entry ${expanded === d.id ? "entry-open" : ""}`}
              key={d.id}
              id={d.id}
              layout
            >
              <button
                className="decision-entry-toggle"
                onClick={() => setExpanded(expanded === d.id ? null : d.id)}
                aria-expanded={expanded === d.id}
              >
                <span className={`decision-stamp verdict-${d.verdict}`}>
                  {d.verdict === "kept" ? (
                    <Check size={18} />
                  ) : d.verdict === "rolled_back" ? (
                    <RotateCcw size={18} />
                  ) : (
                    <Flag size={18} />
                  )}
                </span>
                <div>
                  <div className="decision-meta">
                    <span>{dateTime(d.ts)}</span>
                    <span>·</span>
                    <code>{d.id}</code>
                    <span>·</span>
                    <Badge
                      tone={
                        d.verdict === "kept"
                          ? "blue"
                          : d.verdict === "rolled_back"
                            ? "neutral"
                            : "purple"
                      }
                    >
                      {d.verdict.replace("_", " ")}
                    </Badge>
                  </div>
                  <h3>{d.trigger}</h3>
                  <p>{d.reason}</p>
                </div>
                <ChevronDown size={17} className={expanded === d.id ? "rotated" : ""} />
              </button>
              <AnimatePresence>
                {expanded === d.id && (
                  <motion.div
                    className="decision-evidence"
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                  >
                    <div className="decision-comparison">
                      <div>
                        <h4>Before</h4>
                        {Object.entries(d.before).map(([key, value]) => (
                          <div key={key}>
                            <span>{key}</span>
                            <strong>{value}</strong>
                            <ProgressBar value={key === "qoe" ? value : value / 50} />
                          </div>
                        ))}
                      </div>
                      <ArrowRight size={18} />
                      <div>
                        <h4>After</h4>
                        {Object.entries(d.after).map(([key, value]) => (
                          <div key={key}>
                            <span>{key}</span>
                            <strong>{value}</strong>
                            <ProgressBar value={key === "qoe" ? value : value / 50} />
                          </div>
                        ))}
                      </div>
                    </div>
                    <details>
                      <summary>Raw verifier evidence</summary>
                      <pre>{JSON.stringify(d.evidence, null, 2)}</pre>
                    </details>
                    <div className="hash-chain">
                      <Hash size={12} />
                      <span>
                        SHA-256 <code>{d.hash.slice(0, 20)}…</code>
                        <small>Previous: {d.prevHash.slice(0, 20)}…</small>
                      </span>
                      <ShieldCheck size={16} />
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </motion.article>
          ))}
        </div>
      )}
      {limit < filtered.length && (
        <div className="load-more">
          <Button variant="secondary" onClick={() => setLimit((n) => n + 12)}>
            Load more decisions <ChevronDown size={13} />
          </Button>
          <span>
            Showing {Math.min(limit, filtered.length)} of {filtered.length}
          </span>
        </div>
      )}
      <div className="page-note">
        <Info size={13} /> Hashes verify local log continuity, not the trustworthiness of a remote
        agent. All events here are emulated.
      </div>
    </>
  );
}
