import { useEffect, useRef, type ReactNode, type ButtonHTMLAttributes } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  X,
  ArrowUpRight,
  Check,
  CircleHelp,
  AlertCircle,
  LoaderCircle,
  ShieldCheck,
  Activity,
  Laptop,
  Smartphone,
  Tv,
  Gamepad2,
  Speaker,
  Radio,
  Zap,
} from "lucide-react";
import { create } from "zustand";
import { useMutation } from "@tanstack/react-query";
import { queryClient, request } from "../lib/api";
import type { QoEBand, Device } from "../types";
import { useEffectiveTier } from "../motion/store";

export const useToasts = create<{
  items: { id: number; text: string; error: boolean }[];
  push: (text: string, error?: boolean) => void;
  remove: (id: number) => void;
}>((set, get) => ({
  items: [],
  push: (text, error = false) => {
    const id = Date.now();
    set((s) => ({ items: [...s.items, { id, text, error }] }));
    setTimeout(() => get().remove(id), 4500);
  },
  remove: (id) => set((s) => ({ items: s.items.filter((x) => x.id !== id) })),
}));
export function Toasts() {
  const { items, remove } = useToasts();
  return (
    <div className="toasts" role="status">
      <AnimatePresence>
        {items.map((item) => (
          <motion.div
            className={`toast ${item.error ? "toast-error" : ""}`}
            key={item.id}
            initial={{ opacity: 0, y: 16, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, x: 30 }}
          >
            {item.error ? <AlertCircle size={17} /> : <Check size={17} />}
            <span>{item.text}</span>
            <button aria-label="Dismiss notification" onClick={() => remove(item.id)}>
              <X size={14} />
            </button>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
export function useTask<T = unknown>(success = "Changes saved") {
  return useMutation<T, Error, { path: string; body?: unknown; method?: string }>({
    mutationFn: ({ path, body, method = "POST" }) => request<T>(method, path, body),
    onSuccess: () => {
      void queryClient.invalidateQueries();
      if (success) useToasts.getState().push(success);
    },
    onError: (error) => useToasts.getState().push(error.message, true),
  });
}
export function Button({
  children,
  variant = "primary",
  pending = false,
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  pending?: boolean;
}) {
  return (
    <button
      className={`btn btn-${variant} ${className}`}
      disabled={pending || props.disabled}
      {...props}
    >
      {pending ? <LoaderCircle className="spin" size={15} /> : null}
      {children}
    </button>
  );
}
export function IconButton({
  children,
  label,
  onClick,
}: {
  children: ReactNode;
  label: string;
  onClick?: () => void;
}) {
  return (
    <button className="icon-button" title={label} aria-label={label} onClick={onClick}>
      {children}
    </button>
  );
}
export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: string }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}
export function EmulatedBadge() {
  return (
    <Badge tone="emulated">
      <span className="emulated-dot" /> Emulated
    </Badge>
  );
}
export function QoEBadge({
  band,
  score,
  small = false,
}: {
  band: QoEBand;
  score?: number | null;
  small?: boolean;
}) {
  const text = { green: "Excellent", amber: "Degraded", red: "Poor", idle: "Idle" };
  return (
    <span
      className={`qoe-badge qoe-${band} ${small ? "small" : ""}`}
      title={
        score == null
          ? "No active, classifiable traffic"
          : `QoE proxy ${Math.round(score * 100)}/100. Not a subjective experience measurement.`
      }
    >
      {band === "idle" ? (
        <span className="status-dot" />
      ) : band === "green" ? (
        <Check size={12} />
      ) : (
        <AlertCircle size={12} />
      )}{" "}
      {text[band]}
      {score != null && !small ? ` · ${Math.round(score * 100)}` : ""}
    </span>
  );
}
export function CardHeader({
  title,
  subtitle,
  children,
  icon,
}: {
  title: string;
  subtitle?: string;
  children?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="card-header">
      <div>
        <h2>
          {icon}
          {title}
        </h2>
        {subtitle && <p>{subtitle}</p>}
      </div>
      <div className="card-header-actions">{children}</div>
    </div>
  );
}
export function SectionHeading({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  children?: ReactNode;
}) {
  return (
    <header className="page-heading">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      <div className="heading-actions">{children}</div>
    </header>
  );
}
export function InfoTip({ text }: { text: string }) {
  return (
    <span className="info-tip" tabIndex={0} aria-label={text}>
      <CircleHelp size={14} />
      <span role="tooltip">{text}</span>
    </span>
  );
}
export function EmptyState({
  title = "Nothing here yet",
  description = "Your agent will show updates here as they arrive.",
  children,
}: {
  title?: string;
  description?: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-illustration">
        <Radio size={30} />
        <i />
      </span>
      <h3>{title}</h3>
      <p>{description}</p>
      {children}
    </div>
  );
}
export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="error-state" role="alert">
      <AlertCircle />
      <div>
        <h3>We couldn’t reach the agent</h3>
        <p>{message}</p>
      </div>
      {retry && (
        <Button variant="secondary" onClick={retry}>
          Try again
        </Button>
      )}
    </div>
  );
}
export function Modal({
  open,
  onClose,
  title,
  children,
  drawer = false,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  drawer?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (open && !ref.current?.open) ref.current?.showModal();
    else if (!open && ref.current?.open) ref.current.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      className={drawer ? "modal drawer" : "modal"}
      onCancel={onClose}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal-inner">
        <div className="modal-heading">
          <h2>{title}</h2>
          <IconButton label="Close dialog" onClick={onClose}>
            <X size={19} />
          </IconButton>
        </div>
        {children}
      </div>
    </dialog>
  );
}
export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  pending,
  title = "Reset to safe state?",
  description = "Temporary boosts will be cancelled. The agent will use a conservative policy. Your fairness floor stays protected.",
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  pending?: boolean;
  title?: string;
  description?: string;
}) {
  return (
    <Modal open={open} onClose={onClose} title={title}>
      <div className="confirm-icon">
        <ShieldCheck size={28} />
      </div>
      <p className="dialog-description">{description}</p>
      <div className="diff-preview">
        <span>
          Temporary priorities <b>Cancelled</b>
        </span>
        <span>
          Control mode <b>Conservative</b>
        </span>
        <span>
          Minimum service floor <b>Preserved</b>
        </span>
      </div>
      <div className="modal-footer">
        <Button variant="secondary" onClick={onClose}>
          Keep current state
        </Button>
        <Button pending={pending} onClick={onConfirm}>
          Confirm reset
        </Button>
      </div>
    </Modal>
  );
}
export function DeviceIcon({ device, size = 21 }: { device: Pick<Device, "type">; size?: number }) {
  const Icon =
    device.type === "laptop"
      ? Laptop
      : device.type === "phone"
        ? Smartphone
        : device.type === "tv"
          ? Tv
          : device.type === "console"
            ? Gamepad2
            : Speaker;
  return <Icon size={size} />;
}
export function TrafficIcon({ cls, size = 13 }: { cls: string; size?: number }) {
  const Icon =
    cls === "call"
      ? Radio
      : cls === "game"
        ? Gamepad2
        : cls === "stream"
          ? Tv
          : cls === "bulk"
            ? ArrowUpRight
            : CircleHelp;
  return <Icon size={size} />;
}
export function ProgressBar({ value, className = "" }: { value: number; className?: string }) {
  const tier = useEffectiveTier();
  return (
    <div className={`progress-track ${className}`}>
      <motion.span
        initial={false}
        animate={{ scaleX: Math.max(0, Math.min(1, value)) }}
        transition={{ duration: tier === "Off" ? 0 : 0.4 }}
      />
    </div>
  );
}
export function LockNote({ floor }: { floor: number }) {
  return (
    <span className="lock-note">
      <ShieldCheck size={14} />
      {floor} Mbps floor, always protected
    </span>
  );
}
export function ExperimentalBadge() {
  return <Badge tone="purple">Experimental</Badge>;
}
export function Skeletons() {
  return (
    <div className="skeletons" aria-busy="true" aria-label="Loading">
      <div className="skeleton-heading" />
      <div className="skeleton-grid">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} />
        ))}
      </div>
      <div className="skeleton-main" />
    </div>
  );
}
export function LoadingAction() {
  return <Activity className="spin" size={14} />;
}
export const BoostIcon = Zap;
