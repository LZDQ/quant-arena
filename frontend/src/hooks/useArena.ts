import { startTransition, useCallback, useEffect, useMemo, useState } from "react";
import type { ArenaApi } from "../lib/api";
import type {
  AgentNotificationTargets,
  AgentResponse,
  AgentSnapshotResponse,
  CreateAgentForm,
  ManualClearForm,
  NotificationDestinations,
  RankingEntry,
  SpecialEvent,
} from "../lib/types";
import { useToast } from "../components/ui";

function getAgentIdFromUrl(): string {
  return new URLSearchParams(window.location.search).get("agent-id") ?? "";
}

function setAgentIdInUrl(agentId: string): void {
  const url = new URL(window.location.href);
  if (agentId) {
    url.searchParams.set("agent-id", agentId);
  } else {
    url.searchParams.delete("agent-id");
  }
  window.history.replaceState(null, "", url);
}

type NotifField = keyof AgentNotificationTargets;

/**
 * The coupled core of the arena dashboard: the agent roster, the selected
 * agent's snapshot, the leaderboard rankings, the shared notification
 * destinations, and the actions that mutate them (create / delete / manual
 * reset / notification toggles). Selection is mirrored to the `agent-id` query
 * param so the view is shareable and survives back/forward. Outcomes surface as
 * toasts; the one exception is {@link manualClear}, which throws so the modal
 * can show the failure inline.
 */
export function useArena(api: ArenaApi) {
  const { ok, error } = useToast();

  const [agents, setAgents] = useState<AgentResponse[]>([]);
  // Starts empty; the initial agent-list load validates the URL's agent-id and
  // selects it only if it exists (see refreshAgents below).
  const [selectedAgentId, setSelectedAgentId] = useState<string>("");
  const [snapshot, setSnapshot] = useState<AgentSnapshotResponse | null>(null);
  const [rankings, setRankings] = useState<RankingEntry[]>([]);
  const [loadingAgents, setLoadingAgents] = useState(true);
  const [loadingSnapshot, setLoadingSnapshot] = useState(false);
  const [loadingRankings, setLoadingRankings] = useState(true);
  const [createdToken, setCreatedToken] = useState<string>("");
  const [createdAgentId, setCreatedAgentId] = useState<string>("");
  const [destinations, setDestinations] = useState<NotificationDestinations | null>(null);
  const [agentTargets, setAgentTargets] = useState<AgentNotificationTargets | null>(null);
  const [savingTargets, setSavingTargets] = useState(false);
  const [specialEvents, setSpecialEvents] = useState<SpecialEvent[]>([]);
  const [loadingSpecialEvents, setLoadingSpecialEvents] = useState(false);

  const refreshAgents = useCallback(
    async (preferredAgentId?: string) => {
      setLoadingAgents(true);
      try {
        const data = await api.listAgents();
        setAgents(data);
        startTransition(() => {
          const nextAgentId =
            preferredAgentId && data.some((agent) => agent.agent_id === preferredAgentId)
              ? preferredAgentId
              : "";
          setSelectedAgentId(nextAgentId);
        });
      } catch (err) {
        error((err as Error).message);
      } finally {
        setLoadingAgents(false);
      }
    },
    [api, error],
  );

  const refreshSnapshot = useCallback(
    async (agentId: string) => {
      setLoadingSnapshot(true);
      try {
        const data = await api.getSnapshot(agentId);
        setSnapshot(data);
        setAgentTargets({
          napcat: data.agent.napcat_notify_targets,
          daily_report: data.agent.daily_report_notify_targets,
        });
      } catch (err) {
        error((err as Error).message);
        setSnapshot(null);
        setAgentTargets(null);
      } finally {
        setLoadingSnapshot(false);
      }
    },
    [api, error],
  );

  const refreshRankings = useCallback(async () => {
    setLoadingRankings(true);
    try {
      setRankings(await api.getRankings());
    } catch (err) {
      error((err as Error).message);
    } finally {
      setLoadingRankings(false);
    }
  }, [api, error]);

  const refreshDestinations = useCallback(async () => {
    try {
      setDestinations(await api.getDestinations());
    } catch (err) {
      // Soft failure: the rest of the dashboard works without the notification
      // panel; just surface the error.
      error((err as Error).message);
    }
  }, [api, error]);

  const refreshSpecialEvents = useCallback(
    async (agentId: string) => {
      setLoadingSpecialEvents(true);
      try {
        setSpecialEvents(await api.listSpecialEvents(agentId));
      } catch (err) {
        error((err as Error).message);
        setSpecialEvents([]);
      } finally {
        setLoadingSpecialEvents(false);
      }
    },
    [api, error],
  );

  // Initial load.
  useEffect(() => {
    void refreshAgents(getAgentIdFromUrl());
    void refreshRankings();
    void refreshDestinations();
  }, [refreshAgents, refreshRankings, refreshDestinations]);

  // Keep selection in sync with browser back/forward.
  useEffect(() => {
    const handlePopState = () => setSelectedAgentId(getAgentIdFromUrl());
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  // Mirror selection to the URL and load (or clear) the selected agent's data.
  useEffect(() => {
    setAgentIdInUrl(selectedAgentId);
    if (!selectedAgentId) {
      setSnapshot(null);
      setSpecialEvents([]);
      setAgentTargets(null);
      return;
    }
    void refreshSnapshot(selectedAgentId);
    void refreshSpecialEvents(selectedAgentId);
  }, [selectedAgentId, refreshSnapshot, refreshSpecialEvents]);

  const selectAgent = useCallback((agentId: string) => setSelectedAgentId(agentId), []);

  const createAgent = useCallback(
    async (form: CreateAgentForm): Promise<boolean> => {
      setCreatedToken("");
      setCreatedAgentId("");
      try {
        const created = await api.createAgent(form);
        setCreatedToken(created.token_secret);
        setCreatedAgentId(created.agent.agent_id);
        ok(`Agent ${created.agent.agent_id} created.`);
        await refreshAgents(created.agent.agent_id);
        await refreshRankings();
        return true;
      } catch (err) {
        error((err as Error).message);
        return false;
      }
    },
    [api, ok, error, refreshAgents, refreshRankings],
  );

  const deleteAgent = useCallback(
    async (agentId: string) => {
      setCreatedToken("");
      setCreatedAgentId("");
      try {
        await api.deleteAgent(agentId);
        ok(`Agent ${agentId} deleted.`);
        await refreshAgents();
        await refreshRankings();
      } catch (err) {
        error((err as Error).message);
      }
    },
    [api, ok, error, refreshAgents, refreshRankings],
  );

  /** Throws on failure so the calling modal can render the error inline. */
  const manualClear = useCallback(
    async (agentId: string, form: ManualClearForm) => {
      await api.manualClear(agentId, form);
      ok(`Positions cleared for ${agentId}.`);
      await Promise.all([
        refreshSnapshot(agentId),
        refreshSpecialEvents(agentId),
        refreshRankings(),
      ]);
    },
    [api, ok, refreshSnapshot, refreshSpecialEvents, refreshRankings],
  );

  const saveAgentTargets = useCallback(
    async (next: AgentNotificationTargets) => {
      if (!snapshot) return;
      setSavingTargets(true);
      try {
        const saved = await api.putNotificationTargets(snapshot.agent.agent_id, next);
        setAgentTargets(saved);
        ok("Notification targets updated.");
      } catch (err) {
        error((err as Error).message);
      } finally {
        setSavingTargets(false);
      }
    },
    [api, snapshot, ok, error],
  );

  const toggleAgentTarget = useCallback(
    (field: NotifField, key: string) => {
      if (!agentTargets) return;
      const current = agentTargets[field];
      const next = current.includes(key)
        ? current.filter((item) => item !== key)
        : [...current, key];
      void saveAgentTargets({ ...agentTargets, [field]: next });
    },
    [agentTargets, saveAgentTargets],
  );

  const agentById = useMemo(
    () => new Map(agents.map((agent) => [agent.agent_id, agent])),
    [agents],
  );

  return {
    agents,
    agentById,
    selectedAgentId,
    snapshot,
    rankings,
    loadingAgents,
    loadingSnapshot,
    loadingRankings,
    createdToken,
    createdAgentId,
    destinations,
    agentTargets,
    savingTargets,
    specialEvents,
    loadingSpecialEvents,
    selectAgent,
    createAgent,
    deleteAgent,
    manualClear,
    toggleAgentTarget,
  };
}
