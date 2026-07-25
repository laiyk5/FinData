import { useEffect, useState, type ReactNode } from "react";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  /** Consequence statement, rendered verbatim. */
  message: ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  /** Equivalent CLI command, shown so the mutation maps to a documented command. */
  cliCommand?: string;
  /** When set, the user must type this exact name to enable the confirm button. */
  typedName?: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Shared confirmation dialog for destructive or state-changing mutations. */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  danger = false,
  cliCommand,
  typedName,
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const [typed, setTyped] = useState("");

  useEffect(() => {
    if (open) setTyped("");
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  const blocked = typedName !== undefined && typed !== typedName;

  return (
    <div className="dialog-overlay" onClick={onCancel}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="dialog-title">{title}</h2>
        <div className="dialog-message">{message}</div>
        {cliCommand && (
          <div className="dialog-cli">
            <span className="muted">equivalent CLI:</span> <code>{cliCommand}</code>
          </div>
        )}
        {typedName !== undefined && (
          <label className="field">
            <span className="field-label">
              Type <span className="mono">{typedName}</span> to confirm
            </span>
            <input
              type="text"
              autoFocus
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={typedName}
            />
          </label>
        )}
        <div className="dialog-actions">
          <button className="btn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            className={`btn ${danger ? "btn-danger" : "btn-primary"}`}
            disabled={busy || blocked}
            onClick={onConfirm}
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
