import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  Search,
  LayoutGrid,
  List,
  Pencil,
  ArrowUpRight,
  Check,
  Laptop,
  Wifi,
  Users,
} from "lucide-react";
import { Link } from "react-router-dom";
import { useConsole } from "../../app/Layout";
import { useApi } from "../../lib/api";
import {
  SectionHeading,
  CardHeader,
  Button,
  Badge,
  DeviceIcon,
  QoEBadge,
  TrafficIcon,
  Modal,
  useTask,
  EmptyState,
  ErrorState,
} from "../../components/ui";
import { Sparkline } from "../../components/Chart";
import type { Device, MetricPoint } from "../../types";
export default function Devices() {
  const { snapshot } = useConsole();
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [sort, setSort] = useState("name");
  const [view, setView] = useState("cards");
  const [edit, setEdit] = useState<Device | null>(null);
  const [name, setName] = useState("");
  const [person, setPerson] = useState("");
  const task = useTask("Device details updated.");
  const { data: points = [] } = useApi<MetricPoint[]>("/metrics?range=1h");
  const devices = snapshot.devices
    .filter(
      (d) =>
        (filter === "all" || (filter === "active" ? d.flows.length > 0 : d.flows.length === 0)) &&
        `${d.name} ${d.person}`.toLowerCase().includes(search.toLowerCase()),
    )
    .sort((a, b) => (sort === "qoe" ? (b.qoe ?? 0) - (a.qoe ?? 0) : a.name.localeCompare(b.name)));
  function openEdit(d: Device) {
    setEdit(d);
    setName(d.name);
    setPerson(d.person ?? "");
  }
  return (
    <>
      <SectionHeading
        title="The people behind the packets."
        description="Familiar devices. Different needs. One thoughtful connection."
      >
        <Badge tone="blue">
          <Wifi size={11} />
          {snapshot.devices.length} connected
        </Badge>
      </SectionHeading>
      <div className="filter-toolbar">
        <div className="search-field">
          <Search size={15} />
          <input
            aria-label="Search devices"
            placeholder="Find a device or person…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          aria-label="Device status filter"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        >
          <option value="all">All devices</option>
          <option value="active">Active</option>
          <option value="idle">Idle</option>
        </select>
        <select aria-label="Sort devices" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="name">Sort by name</option>
          <option value="qoe">Sort by QoE</option>
        </select>
        <span className="toolbar-spacer" />
        <div className="segmented-control">
          <button
            aria-label="Card view"
            className={view === "cards" ? "active" : ""}
            onClick={() => setView("cards")}
          >
            <LayoutGrid size={13} />
          </button>
          <button
            aria-label="Table view"
            className={view === "table" ? "active" : ""}
            onClick={() => setView("table")}
          >
            <List size={13} />
          </button>
        </div>
      </div>
      {!devices.length ? (
        <div className="card">
          <EmptyState
            title="No devices found"
            description="Try another name, person or status filter."
          />
        </div>
      ) : view === "cards" ? (
        <motion.div className="devices-grid" layout>
          <AnimatePresence>
            {devices.map((d) => (
              <motion.article
                className="card device-card"
                key={d.id}
                layout
                initial={{ opacity: 0, scale: 0.97 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.97 }}
              >
                <div className="row-between">
                  <span className="device-card-icon">
                    <DeviceIcon device={d} size={27} />
                  </span>
                  <QoEBadge band={d.band} score={d.qoe} small />
                </div>
                <h2>{d.name}</h2>
                <p>
                  <Users size={12} />
                  {d.person || "Unassigned"} <span>·</span> {d.type}
                </p>
                <div className="device-card-metrics">
                  <div>
                    <strong>
                      {d.flows.reduce((n, f) => n + f.throughputMbps, 0).toFixed(1)}
                      <span>Mbps</span>
                    </strong>
                    <small>{d.flows.length} active experiences</small>
                  </div>
                  <Sparkline
                    points={points
                      .slice(-25)
                      .map((p) => (d.flows.length ? p.throughput / snapshot.devices.length : 0))}
                    color={d.flows.length ? "#8aa7e5" : "#c3ccdc"}
                  />
                </div>
                <div className="device-card-classes">
                  {d.flows.map((f) => (
                    <span className={`traffic-class class-${f.cls}`} title={f.cls} key={f.id}>
                      <TrafficIcon cls={f.cls} />
                      {f.cls}
                    </span>
                  ))}
                  {!d.flows.length && <span className="muted">Resting, not disconnected.</span>}
                </div>
                <footer>
                  <code>{d.ip?.replace(/\.\d+$/, ".•••")}</code>
                  <button className="text-link" onClick={() => openEdit(d)}>
                    <Pencil size={12} /> Edit
                  </button>
                  <Link
                    to={`/app/tree?device=${d.id}`}
                    className="text-link"
                    aria-label={`Inspect ${d.name}`}
                  >
                    <ArrowUpRight size={14} />
                  </Link>
                </footer>
              </motion.article>
            ))}
          </AnimatePresence>
        </motion.div>
      ) : (
        <div className="card table-scroll">
          <table>
            <thead>
              <tr>
                <th>Device</th>
                <th>Person</th>
                <th>Throughput</th>
                <th>QoE proxy</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {devices.map((d) => (
                <tr key={d.id}>
                  <td>
                    <button className="table-device" onClick={() => openEdit(d)}>
                      <span className="table-device-icon">
                        <DeviceIcon device={d} size={17} />
                      </span>
                      <div>
                        <strong>{d.name}</strong>
                        <small>{d.ip?.replace(/\.\d+$/, ".•••")}</small>
                      </div>
                    </button>
                  </td>
                  <td>{d.person || "Unassigned"}</td>
                  <td className="mono">
                    {d.flows.reduce((n, f) => n + f.throughputMbps, 0).toFixed(1)} Mbps
                  </td>
                  <td>
                    <QoEBadge band={d.band} score={d.qoe} />
                  </td>
                  <td>
                    <Button variant="ghost" onClick={() => openEdit(d)}>
                      <Pencil size={12} /> Edit
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Modal open={!!edit} onClose={() => setEdit(null)} title="A familiar name helps.">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            task.mutate(
              { path: "/devices", method: "PATCH", body: { id: edit?.id, name, person } },
              { onSuccess: () => setEdit(null) },
            );
          }}
        >
          <label className="field-label">
            Device name
            <input value={name} required maxLength={60} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field-label">
            Person
            <input value={person} maxLength={40} onChange={(e) => setPerson(e.target.value)} />
          </label>
          <p className="field-hint">
            Names are stored locally by the emulated agent. Network addresses stay masked in the
            console.
          </p>
          <div className="modal-footer">
            <Button variant="secondary" type="button" onClick={() => setEdit(null)}>
              Cancel
            </Button>
            <Button type="submit" pending={task.isPending}>
              <Check size={13} /> Save device
            </Button>
          </div>
        </form>
      </Modal>
    </>
  );
}
