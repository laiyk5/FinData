import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { AlertIcon, CheckIcon } from "./icons";

export type ToastKind = "success" | "error";

interface Toast {
  id: number;
  kind: ToastKind;
  text: string;
}

interface ToastApi {
  /** Queues a toast; errors auto-dismiss more slowly. */
  notify: (kind: ToastKind, text: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

let nextId = 1;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const notify = useCallback((kind: ToastKind, text: string) => {
    const id = nextId;
    nextId += 1;
    setToasts((current) => [...current, { id, kind, text }]);
    const ttl = kind === "error" ? 8000 : 4000;
    setTimeout(() => {
      setToasts((current) => current.filter((t) => t.id !== id));
    }, ttl);
  }, []);

  const api = useMemo(() => ({ notify }), [notify]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-container" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`}>
            {t.kind === "success" ? <CheckIcon /> : <AlertIcon />}
            <span>{t.text}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>");
  return ctx;
}
