import { useState } from "react";
import type { ManualClearForm } from "../../lib/types";

function makeDefaultForm(): ManualClearForm {
  return { comment: "", keep_unrealized_pnl: true, keep_realized_pnl: true };
}

type ManualResetModalProps = {
  agentDisplayName: string;
  onClose: () => void;
  /** Performs the reset; throws on failure so the error renders inline. */
  onConfirm: (form: ManualClearForm) => Promise<void>;
};

/** Modal that clears all positions, cancels pending orders, and applies the
 * keep-P&L flags. Owns its form, submitting, and inline-error state. */
export function ManualResetModal({ agentDisplayName, onClose, onConfirm }: ManualResetModalProps) {
  const [form, setForm] = useState<ManualClearForm>(() => makeDefaultForm());
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  function requestClose() {
    if (submitting) return;
    onClose();
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = form.comment.trim();
    if (!trimmed) {
      setError("Comment is required.");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      await onConfirm({ ...form, comment: trimmed });
      onClose();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="manual-clear-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="manual-clear-title"
      onClick={(event) => {
        if (event.target === event.currentTarget) requestClose();
      }}
    >
      <form className="manual-clear-card" onSubmit={handleSubmit}>
        <div className="manual-clear-head">
          <h3 id="manual-clear-title">Manual Reset · {agentDisplayName}</h3>
          <span className="manual-clear-meta">
            Clears all positions, cancels pending orders, and applies the two flags below.
          </span>
        </div>
        <div className="manual-clear-body">
          <label className="manual-clear-field" htmlFor="manual-clear-comment">
            <span>Comment</span>
            <textarea
              id="manual-clear-comment"
              value={form.comment}
              onChange={(event) => setForm((prev) => ({ ...prev, comment: event.target.value }))}
              placeholder="why this reset"
              rows={3}
              required
            />
          </label>
          <label className="manual-clear-toggle">
            <input
              type="checkbox"
              checked={form.keep_unrealized_pnl}
              onChange={(event) =>
                setForm((prev) => ({ ...prev, keep_unrealized_pnl: event.target.checked }))
              }
            />
            <span>
              <strong>Keep unrealized P&amp;L</strong>
              <small>
                On: floating P&amp;L is realized into cash at the last known price. Off: it is wiped
                — positions wind back to cost basis.
              </small>
            </span>
          </label>
          <label className="manual-clear-toggle">
            <input
              type="checkbox"
              checked={form.keep_realized_pnl}
              onChange={(event) =>
                setForm((prev) => ({ ...prev, keep_realized_pnl: event.target.checked }))
              }
            />
            <span>
              <strong>Keep realized P&amp;L</strong>
              <small>
                On: existing realized P&amp;L is preserved. Off: realized P&amp;L is wiped and the
                same amount is removed from cash. With both off, the agent returns to its initial
                cash.
              </small>
            </span>
          </label>
          {error && <div className="manual-clear-error">{error}</div>}
        </div>
        <div className="manual-clear-foot">
          <button
            type="button"
            className="button button-ghost"
            onClick={requestClose}
            disabled={submitting}
          >
            Cancel
          </button>
          <button type="submit" className="button manual-clear-submit" disabled={submitting}>
            {submitting ? "Resetting…" : "Confirm Reset"}
          </button>
        </div>
      </form>
    </div>
  );
}
