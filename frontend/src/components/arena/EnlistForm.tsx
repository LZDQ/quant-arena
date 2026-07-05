import { useState } from "react";
import type {
  CreateAgentForm,
  Currency,
  CurrencyOption,
  IBMode,
  IBModeOption,
} from "../../lib/types";

function makeDefaultForm(currency: Currency, ibMode: IBMode | null): CreateAgentForm {
  return {
    agent_id: "",
    display_name: "",
    initial_cash: "100000",
    currency,
    role: "normal",
    ib_mode: ibMode,
  };
}

type EnlistFormProps = {
  currencyOptions: CurrencyOption[];
  ibModeOptions?: IBModeOption[];
  placeholders: { agentId: string; displayName: string };
  createdToken: string;
  createdAgentId: string;
  /** Returns true when the agent was created, so the form can reset. */
  onCreate: (form: CreateAgentForm) => Promise<boolean>;
};

/** "Enlist" panel: the new-agent form plus the one-time token card. Owns its
 * own field + dropdown state; surfaces only the create action upward. */
export function EnlistForm({
  currencyOptions,
  ibModeOptions,
  placeholders,
  createdToken,
  createdAgentId,
  onCreate,
}: EnlistFormProps) {
  const defaultCurrency = currencyOptions[0]?.value ?? "CNY";
  const currencyLocked = currencyOptions.length <= 1;
  const defaultIbMode = ibModeOptions?.[0]?.value ?? null;
  const currencyLabel = (value: Currency): string =>
    currencyOptions.find((option) => option.value === value)?.label ?? value;
  const ibModeLabel = (value: IBMode | null): string => {
    if (value === null) return "—";
    return ibModeOptions?.find((option) => option.value === value)?.label ?? value;
  };

  const [form, setForm] = useState<CreateAgentForm>(() =>
    makeDefaultForm(defaultCurrency, defaultIbMode),
  );
  const [currencyMenuOpen, setCurrencyMenuOpen] = useState(false);
  const [ibModeMenuOpen, setIbModeMenuOpen] = useState(false);
  const [modeMenuOpen, setModeMenuOpen] = useState(false);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const created = await onCreate(form);
    if (created) {
      setForm(makeDefaultForm(defaultCurrency, defaultIbMode));
    }
  }

  return (
    <form className="form" onSubmit={handleSubmit}>
      <div className="section-head" style={{ borderBottomWidth: 1, marginBottom: 0 }}>
        <h3 style={{ fontSize: 22 }}>Enlist</h3>
        <span className="meta">New Agent</span>
      </div>
      <div className="form-grid">
        <div className="field field-half">
          <label htmlFor="agent_id">Agent ID</label>
          <input
            id="agent_id"
            value={form.agent_id}
            onChange={(event) => setForm((prev) => ({ ...prev, agent_id: event.target.value }))}
            placeholder={placeholders.agentId}
            required
          />
        </div>
        <div className="field field-half">
          <label htmlFor="initial_cash">Initial Cash · {currencyLabel(form.currency)}</label>
          <input
            id="initial_cash"
            value={form.initial_cash}
            onChange={(event) => setForm((prev) => ({ ...prev, initial_cash: event.target.value }))}
            type="number"
            min="1"
            required
          />
        </div>
        <div className="field field-half">
          <label htmlFor="display_name">Display Name</label>
          <input
            id="display_name"
            value={form.display_name}
            onChange={(event) => setForm((prev) => ({ ...prev, display_name: event.target.value }))}
            placeholder={placeholders.displayName}
            required
          />
        </div>
        <div
          className="field field-half select-wrap"
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
              setCurrencyMenuOpen(false);
            }
          }}
        >
          <label>Currency</label>
          {currencyLocked ? (
            <button className="select-trigger" type="button" disabled aria-disabled="true">
              <span>{currencyLabel(form.currency)}</span>
            </button>
          ) : (
            <>
              <button
                className="select-trigger"
                type="button"
                aria-haspopup="listbox"
                aria-expanded={currencyMenuOpen}
                onClick={() => setCurrencyMenuOpen((open) => !open)}
              >
                <span>{currencyLabel(form.currency)}</span>
              </button>
              {currencyMenuOpen && (
                <div className="select-menu" role="listbox" aria-label="Trading currency">
                  {currencyOptions.map((option) => (
                    <button
                      key={option.value}
                      className={`select-option ${form.currency === option.value ? "is-active" : ""}`}
                      type="button"
                      onClick={() => {
                        setForm((prev) => ({ ...prev, currency: option.value }));
                        setCurrencyMenuOpen(false);
                      }}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
        {ibModeOptions && ibModeOptions.length > 0 && (
          <div
            className="field field-half select-wrap"
            onBlur={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                setIbModeMenuOpen(false);
              }
            }}
          >
            <label>Account</label>
            <button
              className="select-trigger"
              type="button"
              aria-haspopup="listbox"
              aria-expanded={ibModeMenuOpen}
              onClick={() => setIbModeMenuOpen((open) => !open)}
            >
              <span>{ibModeLabel(form.ib_mode)}</span>
            </button>
            {ibModeMenuOpen && (
              <div className="select-menu" role="listbox" aria-label="IB account">
                {ibModeOptions.map((option) => (
                  <button
                    key={option.value}
                    className={`select-option ${form.ib_mode === option.value ? "is-active" : ""}`}
                    type="button"
                    onClick={() => {
                      setForm((prev) => ({ ...prev, ib_mode: option.value }));
                      setIbModeMenuOpen(false);
                    }}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        <div
          className="field select-wrap"
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
              setModeMenuOpen(false);
            }
          }}
        >
          <label>Mode</label>
          <button
            className="select-trigger"
            type="button"
            aria-haspopup="listbox"
            aria-expanded={modeMenuOpen}
            onClick={() => setModeMenuOpen((open) => !open)}
          >
            <span>{form.role === "normal" ? "Normal" : "Monitor"}</span>
          </button>
          {modeMenuOpen && (
            <div className="select-menu" role="listbox" aria-label="Agent mode">
              <button
                className={`select-option ${form.role === "normal" ? "is-active" : ""}`}
                type="button"
                onClick={() => {
                  setForm((prev) => ({ ...prev, role: "normal" }));
                  setModeMenuOpen(false);
                }}
              >
                Normal · trades the book
              </button>
              <button
                className={`select-option ${form.role === "monitor" ? "is-active" : ""}`}
                type="button"
                onClick={() => {
                  setForm((prev) => ({ ...prev, role: "monitor" }));
                  setModeMenuOpen(false);
                }}
              >
                Monitor · watches only
              </button>
            </div>
          )}
        </div>
      </div>
      <button className="button" type="submit">
        Issue Token
      </button>
      {createdToken && (
        <div className="token-card">
          <div className="token-card-label">One-time token · {createdAgentId}</div>
          <div className="token-card-value">{createdToken}</div>
          <button
            className="button button-ghost"
            type="button"
            onClick={() => void navigator.clipboard.writeText(createdToken)}
          >
            Copy Token
          </button>
        </div>
      )}
    </form>
  );
}
