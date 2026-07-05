import type { ReactNode } from "react";
import { ToastProvider } from "./ToastProvider";
import { ConfirmProvider } from "./ConfirmProvider";

export { useToast, type ToastKind } from "./ToastProvider";
export { useConfirm, type ConfirmOptions } from "./ConfirmProvider";

/** App-wide UI shell: toast viewport + confirm-dialog host. */
export function UiProvider({ children }: { children: ReactNode }) {
  return (
    <ToastProvider>
      <ConfirmProvider>{children}</ConfirmProvider>
    </ToastProvider>
  );
}
