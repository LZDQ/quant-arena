import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import "./ui.css";

export type ConfirmOptions = {
  title: string;
  body?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Render the confirm button as a destructive action. */
  danger?: boolean;
};

type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

type PendingConfirm = { options: ConfirmOptions; resolve: (ok: boolean) => void };

/**
 * Promise-based replacement for `window.confirm`. `await confirm({...})`
 * resolves to true/false. Escape or a backdrop click resolves false; Enter
 * confirms. Replaces the blocking native dialogs across the app.
 */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<PendingConfirm | null>(null);
  const confirmButtonRef = useRef<HTMLButtonElement>(null);

  const confirm = useCallback<ConfirmFn>((options) => {
    return new Promise<boolean>((resolve) => {
      setPending((prev) => {
        // If a dialog is already open, decline it before opening the new one.
        prev?.resolve(false);
        return { options, resolve };
      });
    });
  }, []);

  const settle = useCallback(
    (ok: boolean) => {
      setPending((prev) => {
        prev?.resolve(ok);
        return null;
      });
    },
    [],
  );

  useEffect(() => {
    if (!pending) return;
    confirmButtonRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        settle(false);
      } else if (event.key === "Enter") {
        event.preventDefault();
        settle(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pending, settle]);

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {pending && (
        <div
          className="confirm-overlay"
          role="dialog"
          aria-modal="true"
          aria-labelledby="confirm-title"
          onClick={(event) => {
            if (event.target === event.currentTarget) settle(false);
          }}
        >
          <div className="confirm-card">
            <h3 id="confirm-title" className="confirm-title">
              {pending.options.title}
            </h3>
            {pending.options.body != null && (
              <div className="confirm-body">{pending.options.body}</div>
            )}
            <div className="confirm-foot">
              <button
                type="button"
                className="button button-ghost"
                onClick={() => settle(false)}
              >
                {pending.options.cancelLabel ?? "Cancel"}
              </button>
              <button
                ref={confirmButtonRef}
                type="button"
                className={`button ${pending.options.danger ? "button-danger" : ""}`}
                onClick={() => settle(true)}
              >
                {pending.options.confirmLabel ?? "Confirm"}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext);
  if (!ctx) {
    throw new Error("useConfirm must be used within a <ConfirmProvider>");
  }
  return ctx;
}
