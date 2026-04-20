"""QQ Open Platform notification service."""

import asyncio
import time
from dataclasses import dataclass
from logging import getLogger

import httpx

from quant_arena.config import QQOpenConfig, QQOpenGroupTargetConfig
from quant_arena.models import FillRecord, OrderRecord

logger = getLogger(__name__)

QQ_OPEN_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
QQ_OPEN_API_BASE_URL = "https://api.sgroup.qq.com"
QQ_OPEN_SANDBOX_API_BASE_URL = "https://sandbox.api.sgroup.qq.com"


@dataclass(slots=True)
class QueuedQQOpenMessage:
    """One outbound QQ Open Platform request."""

    destination_key: str
    destination_type: str
    destination_id: str
    agent_id: str
    order_id: str
    event_name: str
    message_text: str
    attempt: int = 0


class QQOpenNotifier:
    """Send backend notifications to QQ Open Platform over HTTP."""

    def __init__(self, config: QQOpenConfig):
        self.config = config
        self._queue: asyncio.Queue[QueuedQQOpenMessage] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._client: httpx.AsyncClient | None = None
        self._access_token = ""
        self._access_token_expires_at = 0.0

    async def start(self) -> None:
        if not self.config.enabled:
            logger.info("QQ Open notifications are disabled")
            return
        if not self.config.app_id or not self.config.client_secret:
            raise ValueError("QQ Open notifications require both app_id and client_secret")
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._client = httpx.AsyncClient(timeout=self.config.request_timeout_seconds)
        self._worker_task = asyncio.create_task(self._run(), name="qq-open-notifier")
        logger.info(
            "QQ Open notifier enabled for %d configured destinations using %s",
            len(self.config.destinations),
            self._api_base_url(),
        )

    async def close(self) -> None:
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            finally:
                self._worker_task = None
        client = self._client
        self._client = None
        self._queue = None
        self._loop = None
        self._access_token = ""
        self._access_token_expires_at = 0.0
        if client is not None:
            await client.aclose()
        logger.info("QQ Open notifier stopped")

    def notify_order_submitted(self, agent_display_name: str, target_keys: list[str], order: OrderRecord) -> None:
        if not self.config.notify_on_submit:
            logger.debug("QQ Open submit notifications are disabled for order %s", order.order_id)
            return
        message = self._format_order_submitted(agent_display_name, order)
        self._enqueue_for_targets(target_keys, order.agent_id, order.order_id, "submit", message)

    def notify_order_canceled(self, agent_display_name: str, target_keys: list[str], order: OrderRecord) -> None:
        if not self.config.notify_on_cancel:
            logger.debug("QQ Open cancel notifications are disabled for order %s", order.order_id)
            return
        message = self._format_order_canceled(agent_display_name, order)
        self._enqueue_for_targets(target_keys, order.agent_id, order.order_id, "cancel", message)

    def notify_order_filled(
        self,
        agent_display_name: str,
        target_keys: list[str],
        order: OrderRecord,
        fill: FillRecord,
    ) -> None:
        if not self.config.notify_on_fill:
            logger.debug("QQ Open fill notifications are disabled for order %s", order.order_id)
            return
        message = self._format_order_filled(agent_display_name, order, fill)
        self._enqueue_for_targets(target_keys, order.agent_id, order.order_id, "fill", message)

    async def _run(self) -> None:
        queue = self._queue
        if queue is None:
            return
        while True:
            outbound = await queue.get()
            try:
                await self._send_message(outbound)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception(
                    "QQ Open send failed for event=%s agent=%s order=%s target=%s",
                    outbound.event_name,
                    outbound.agent_id,
                    outbound.order_id,
                    outbound.destination_key,
                )
                self._requeue_message(outbound, str(exc))
                await asyncio.sleep(self.config.retry_interval_seconds)
            finally:
                queue.task_done()

    async def _send_message(self, outbound: QueuedQQOpenMessage) -> None:
        client = self._client
        if client is None:
            raise RuntimeError("QQ Open notifier HTTP client is not initialized")
        access_token = await self._get_access_token()
        url = f"{self._api_base_url()}/v2/groups/{outbound.destination_id}/messages"
        headers = {
            "Authorization": f"QQBot {access_token}",
            "Content-Type": "application/json",
        }
        response = await client.post(
            url,
            headers=headers,
            json={
                "content": outbound.message_text,
                "msg_type": 0,
            },
        )
        if response.status_code == 401:
            access_token = await self._get_access_token(force_refresh=True)
            headers["Authorization"] = f"QQBot {access_token}"
            response = await client.post(
                url,
                headers=headers,
                json={
                    "content": outbound.message_text,
                    "msg_type": 0,
                },
            )
        if response.status_code >= 400:
            logger.warning(
                "QQ Open API returned error status=%s body=%s for event=%s agent=%s order=%s target=%s",
                response.status_code,
                response.text,
                outbound.event_name,
                outbound.agent_id,
                outbound.order_id,
                outbound.destination_key,
            )
            response.raise_for_status()
        logger.info(
            "Sent QQ Open %s notification for agent=%s order=%s to %s(%s)",
            outbound.event_name,
            outbound.agent_id,
            outbound.order_id,
            outbound.destination_type,
            outbound.destination_key,
        )

    async def _get_access_token(self, force_refresh: bool = False) -> str:
        client = self._client
        if client is None:
            raise RuntimeError("QQ Open notifier HTTP client is not initialized")
        now = time.monotonic()
        if not force_refresh and self._access_token and now < self._access_token_expires_at:
            return self._access_token
        response = await client.post(
            QQ_OPEN_TOKEN_URL,
            headers={"Content-Type": "application/json"},
            json={
                "appId": self.config.app_id,
                "clientSecret": self.config.client_secret,
            },
        )
        if response.status_code >= 400:
            logger.warning(
                "QQ Open token request failed with status=%s body=%s",
                response.status_code,
                response.text,
            )
            response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("QQ Open token response was not a JSON object")
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            access_token = payload.get("accessToken")
        expires_in = payload.get("expires_in")
        if expires_in is None:
            expires_in = payload.get("expiresIn", 7200)
        if not isinstance(access_token, str) or not access_token:
            raise ValueError(f"QQ Open token response missing access token: {payload!r}")
        self._access_token = access_token
        self._access_token_expires_at = now + max(float(expires_in) - 60.0, 60.0)
        return self._access_token

    def _api_base_url(self) -> str:
        if self.config.sandbox:
            return QQ_OPEN_SANDBOX_API_BASE_URL
        return QQ_OPEN_API_BASE_URL

    def _requeue_message(self, outbound: QueuedQQOpenMessage, reason: str) -> None:
        if outbound.attempt >= 1:
            logger.warning(
                "Dropping QQ Open %s notification for agent=%s order=%s target=%s after retry failure: %s",
                outbound.event_name,
                outbound.agent_id,
                outbound.order_id,
                outbound.destination_key,
                reason,
            )
            return
        queue = self._queue
        loop = self._loop
        if queue is None or loop is None:
            logger.warning(
                "Cannot requeue QQ Open %s notification for agent=%s order=%s target=%s: %s",
                outbound.event_name,
                outbound.agent_id,
                outbound.order_id,
                outbound.destination_key,
                reason,
            )
            return
        retried = QueuedQQOpenMessage(
            destination_key=outbound.destination_key,
            destination_type=outbound.destination_type,
            destination_id=outbound.destination_id,
            agent_id=outbound.agent_id,
            order_id=outbound.order_id,
            event_name=outbound.event_name,
            message_text=outbound.message_text,
            attempt=outbound.attempt + 1,
        )
        logger.warning(
            "Requeueing QQ Open %s notification for agent=%s order=%s target=%s: %s",
            outbound.event_name,
            outbound.agent_id,
            outbound.order_id,
            outbound.destination_key,
            reason,
        )
        loop.call_soon_threadsafe(queue.put_nowait, retried)

    def _enqueue_for_targets(self, target_keys: list[str], agent_id: str, order_id: str, event_name: str, message_text: str) -> None:
        if not self.config.enabled:
            logger.debug("Skipping QQ Open %s notification for order %s because notifier is disabled", event_name, order_id)
            return
        queue = self._queue
        loop = self._loop
        if queue is None or loop is None:
            logger.warning(
                "Skipping QQ Open %s notification for agent=%s order=%s because notifier is not started",
                event_name,
                agent_id,
                order_id,
            )
            return
        if not target_keys:
            logger.debug("Skipping QQ Open %s notification for agent=%s order=%s because no target keys are configured", event_name, agent_id, order_id)
            return
        for target_key in target_keys:
            target = self.config.destinations.get(target_key)
            if target is None:
                logger.warning(
                    "Unknown QQ Open destination key %r configured for agent=%s order=%s",
                    target_key,
                    agent_id,
                    order_id,
                )
                continue
            if not isinstance(target, QQOpenGroupTargetConfig):
                logger.warning(
                    "Unsupported QQ Open destination type for key %r agent=%s order=%s",
                    target_key,
                    agent_id,
                    order_id,
                )
                continue
            outbound = QueuedQQOpenMessage(
                destination_key=target_key,
                destination_type="group",
                destination_id=target.group_openid,
                agent_id=agent_id,
                order_id=order_id,
                event_name=event_name,
                message_text=message_text,
            )
            logger.info(
                "Queueing QQ Open %s notification for agent=%s order=%s to group(%s)",
                event_name,
                agent_id,
                order_id,
                target_key,
            )
            loop.call_soon_threadsafe(queue.put_nowait, outbound)

    @staticmethod
    def _format_order_submitted(agent_display_name: str, order: OrderRecord) -> str:
        return (
            f"[quant-arena] Order submitted\n"
            f"Agent: {agent_display_name} ({order.agent_id})\n"
            f"Order ID: {order.order_id}\n"
            f"Side: {order.side}\n"
            f"Code: {order.code}\n"
            f"Quantity: {order.quantity}\n"
            f"Limit Price: {order.limit_price:.2f}\n"
            f"Comment: {order.comment}\n"
            f"Submitted At: {order.submitted_at.isoformat()}"
        )

    @staticmethod
    def _format_order_canceled(agent_display_name: str, order: OrderRecord) -> str:
        canceled_at = order.canceled_at.isoformat() if order.canceled_at is not None else "unknown"
        reason_line = ""
        if order.rejection_reason:
            reason_line = f"\nReason: {order.rejection_reason}"
        return (
            f"[quant-arena] Order canceled\n"
            f"Agent: {agent_display_name} ({order.agent_id})\n"
            f"Order ID: {order.order_id}\n"
            f"Side: {order.side}\n"
            f"Code: {order.code}\n"
            f"Quantity: {order.quantity}\n"
            f"Limit Price: {order.limit_price:.2f}\n"
            f"Comment: {order.comment}\n"
            f"Canceled At: {canceled_at}"
            f"{reason_line}"
        )

    @staticmethod
    def _format_order_filled(agent_display_name: str, order: OrderRecord, fill: FillRecord) -> str:
        return (
            f"[quant-arena] Order filled\n"
            f"Agent: {agent_display_name} ({order.agent_id})\n"
            f"Order ID: {order.order_id}\n"
            f"Side: {order.side}\n"
            f"Code: {order.code}\n"
            f"Quantity: {fill.quantity}\n"
            f"Limit Price: {order.limit_price:.2f}\n"
            f"Filled Price: {fill.executed_price:.2f}\n"
            f"Commission: {fill.commission:.2f}\n"
            f"Stamp Tax: {fill.stamp_tax:.2f}\n"
            f"Comment: {order.comment}\n"
            f"Filled At: {fill.executed_at.isoformat()}"
        )
