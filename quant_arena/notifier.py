"""Notification fan-out across multiple QQ channels."""

from quant_arena.config import AgentConfig
from quant_arena.models import FillRecord, OrderRecord
from quant_arena.napcat import NapCatNotifier
from quant_arena.qq_open import QQOpenNotifier


class NotifierService:
    """Dispatch notifications to all configured channel backends."""

    def __init__(self, napcat: NapCatNotifier, qq_open: QQOpenNotifier):
        self.napcat = napcat
        self.qq_open = qq_open

    async def start(self) -> None:
        await self.napcat.start()
        await self.qq_open.start()

    async def close(self) -> None:
        await self.qq_open.close()
        await self.napcat.close()

    def notify_order_submitted(self, agent: AgentConfig, order: OrderRecord) -> None:
        self.napcat.notify_order_submitted(agent.display_name, agent.napcat_notify_targets, order)
        self.qq_open.notify_order_submitted(agent.display_name, agent.qq_open_notify_targets, order)

    def notify_order_canceled(self, agent: AgentConfig, order: OrderRecord) -> None:
        self.napcat.notify_order_canceled(agent.display_name, agent.napcat_notify_targets, order)
        self.qq_open.notify_order_canceled(agent.display_name, agent.qq_open_notify_targets, order)

    def notify_order_filled(self, agent: AgentConfig, order: OrderRecord, fill: FillRecord) -> None:
        self.napcat.notify_order_filled(agent.display_name, agent.napcat_notify_targets, order, fill)
        self.qq_open.notify_order_filled(agent.display_name, agent.qq_open_notify_targets, order, fill)

    def notify_daily_report(self, agent: AgentConfig, agent_id: str, file_name: str, pdf_bytes: bytes) -> None:
        # NapCat only, and routed to the agent's dedicated daily-report
        # destinations — independent of the order-notification target lists.
        self.napcat.notify_daily_report(
            agent.display_name, agent.daily_report_notify_targets, agent_id, file_name, pdf_bytes
        )
