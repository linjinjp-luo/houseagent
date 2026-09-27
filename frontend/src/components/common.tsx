import { Component, useState, type ErrorInfo, type ReactNode } from "react";
import { ApiError } from "../api/client";
import { translate, useI18n } from "../i18n";
import { useApp } from "../lib/app";
import { IconExternal } from "./Icons";
import { sourceHref } from "../lib/format";

export function PageHeader({ title, description, actions }: { title: string; description: string; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        <p className="page-desc">{description}</p>
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

/** Localised error text: what happened, whether data is saved and what to do next. */
export function useErrorText(): (e: unknown) => string {
  const { t } = useI18n();
  return (e: unknown) => {
    if (e instanceof ApiError) {
      const base = t(e.messageKey);
      return e.correlationId ? `${base} (${t("common.correlation_id")}: ${e.correlationId})` : base;
    }
    return t("error.UNKNOWN_ERROR");
  };
}

export function ErrorPanel({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  const { t } = useI18n();
  const text = useErrorText()(error);
  const fatal = error.unrecoverable;
  return (
    <div className={`state-panel ${fatal ? "state-fatal" : "state-error"}`} role="alert">
      <strong>{fatal ? t("state.unrecoverable") : t("state.error")}</strong>
      <p>{text}</p>
      {fatal ? <p className="muted">{t("state.unrecoverable_hint")}</p> : onRetry && <button className="btn" onClick={onRetry}>{t("common.retry")}</button>}
    </div>
  );
}

/** Loading / empty / error states shared by every page. */
export function StateView({ loading, error, empty, emptyText, onRetry, children }: {
  loading: boolean;
  error: ApiError | null;
  empty?: boolean;
  emptyText?: ReactNode;
  onRetry?: () => void;
  children?: ReactNode;
}) {
  const { t } = useI18n();
  if (error) return <ErrorPanel error={error} onRetry={onRetry} />;
  if (loading && !children) return <div className="state-panel state-loading"><span className="spinner" />{t("state.loading")}</div>;
  if (empty) return <div className="state-panel state-empty">{emptyText ?? t("state.empty")}</div>;
  return <>{loading && <div className="loading-bar" />}{children}</>;
}

export function Badge({ tone, children, title }: { tone: "ok" | "warn" | "bad" | "info" | "muted" | "accent"; children: ReactNode; title?: string }) {
  return <span className={`badge badge-${tone}`} title={title}>{children}</span>;
}

const RUN_TONE: Record<string, "ok" | "warn" | "bad" | "info" | "muted"> = {
  completed: "ok", running: "info", queued: "muted", paused: "warn", failed: "bad", cancelled: "muted", interrupted: "warn",
};
export function RunStatusBadge({ status }: { status: string }) {
  const { tv } = useI18n();
  return <Badge tone={RUN_TONE[status] ?? "muted"}>{tv("run_status", status)}</Badge>;
}

const LOGIN_TONE: Record<string, "ok" | "warn" | "bad" | "muted"> = {
  valid: "ok", expiring: "warn", relogin_required: "bad", check_failed: "warn", not_logged_in: "muted",
};
export function LoginBadge({ status }: { status: string }) {
  const { tv } = useI18n();
  return <Badge tone={LOGIN_TONE[status] ?? "muted"}>{tv("login", status)}</Badge>;
}

const PERM_TONE = { allowed: "ok", denied: "bad", unknown: "warn" } as const;
export function PermissionBadge({ status }: { status: "allowed" | "denied" | "unknown" }) {
  const { tv } = useI18n();
  return <Badge tone={PERM_TONE[status]}>{tv("perm_status", status)}</Badge>;
}

const LISTING_TONE: Record<string, "ok" | "warn" | "bad" | "muted"> = { active: "ok", not_found: "warn", unavailable: "bad", ended: "muted" };
export function ListingStatusBadge({ status }: { status: string }) {
  const { tv } = useI18n();
  return <Badge tone={LISTING_TONE[status] ?? "muted"}>{tv("obs", status)}</Badge>;
}

const EVENT_TONE: Record<string, "ok" | "warn" | "bad" | "info" | "accent"> = {
  NEW: "accent", PRICE_DOWN: "ok", PRICE_UP: "bad", NOT_FOUND: "warn", UNAVAILABLE: "bad", REAPPEARED: "info", SALE_ENDED: "bad",
};
export function EventBadge({ type }: { type: string }) {
  const { tv } = useI18n();
  return <Badge tone={EVENT_TONE[type] ?? "info"}>{tv("event", type)}</Badge>;
}

/** Link to the original site page. The page's full content is only ever viewed there. */
export function SourceLink({ url, status }: { url: string; status?: string }) {
  const { t } = useI18n();
  if (status === "unreachable") return <span className="muted" title={url}>{t("listing.link_unreachable")}</span>;
  return (
    <a className="source-link" href={sourceHref(url)} target="_blank" rel="noopener noreferrer">
      {t("listing.open_source")} <IconExternal />
    </a>
  );
}

export function Modal({ title, onClose, children, footer, wide }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  const { t } = useI18n();
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "modal-wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="btn-icon" onClick={onClose} aria-label={t("common.close")}>×</button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

/** Every delete / destructive action goes through a second confirmation. */
export function ConfirmDialog({ title, message, confirmLabel, danger, onConfirm, onClose, children, confirmDisabled, wide }: {
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  confirmDisabled?: boolean;
  wide?: boolean;
  onConfirm: () => Promise<void> | void;
  onClose: () => void;
  children?: ReactNode;
}) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const errText = useErrorText();
  return (
    <Modal
      title={title}
      onClose={onClose}
      wide={wide}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>{t("common.cancel")}</button>
          <button
            className={`btn ${danger ? "btn-danger" : "btn-primary"}`}
            disabled={busy || confirmDisabled}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                await onConfirm();
                onClose();
              } catch (e) {
                setError(e);
              } finally {
                setBusy(false);
              }
            }}
          >
            {confirmLabel ?? t("common.confirm")}
          </button>
        </>
      }
    >
      <div className="confirm-message">{message}</div>
      {children}
      {error !== null && <p className="form-error">{errText(error)}</p>}
    </Modal>
  );
}

export function Toasts() {
  const { toasts, dismissToast } = useApp();
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((x) => (
        <div key={x.id} className={`toast toast-${x.kind}`} onClick={() => dismissToast(x.id)}>{x.text}</div>
      ))}
    </div>
  );
}

export function Pager({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const { t } = useI18n();
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="pager">
      <span className="muted">{t("common.total_count", { n: total })}</span>
      <button className="btn btn-sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>‹</button>
      <span>{page} / {pages}</span>
      <button className="btn btn-sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>›</button>
    </div>
  );
}

export function Field({ label, hint, children, required }: { label: string; hint?: ReactNode; children: ReactNode; required?: boolean }) {
  return (
    <label className="field">
      <span className="field-label">{label}{required && <span className="req">*</span>}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

export function Stat({ label, value, onClick, tone }: { label: string; value: ReactNode; onClick?: () => void; tone?: string }) {
  return (
    <button className={`stat-card ${tone ? `stat-${tone}` : ""}`} onClick={onClick} disabled={!onClick}>
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
    </button>
  );
}

interface EBState { error: Error | null }

/** Unrecoverable error boundary: shows a safe message, never a stack trace. */
export class ErrorBoundary extends Component<{ lang: "ja" | "zh" | "en"; children: ReactNode }, EBState> {
  override state: EBState = { error: null };

  static getDerivedStateFromError(error: Error): EBState {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("UI error", error.name, info.componentStack?.split("\n")[1]);
  }

  override render() {
    if (this.state.error) {
      const t = (k: string) => translate(this.props.lang, k);
      return (
        <div className="state-panel state-fatal" style={{ margin: 24 }}>
          <strong>{t("state.unrecoverable")}</strong>
          <p>{t("state.ui_crash")}</p>
          <button className="btn" onClick={() => window.location.reload()}>{t("common.reload")}</button>
        </div>
      );
    }
    return this.props.children;
  }
}
