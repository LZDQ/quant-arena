"""NapCat QQ notification service."""

import asyncio
import json
import secrets
from logging import getLogger
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from quant_arena.config import NapCatConfig, NapCatGroupTargetConfig, NapCatPrivateTargetConfig
from quant_arena.models import FillRecord, OrderRecord

logger = getLogger(__name__)


class NapCatNotifier:
    """Send backend notifications to NapCat over WebSocket."""

    def __init__(self, config: NapCatConfig):
        self.config = config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._websocket = None
        self._send_lock: asyncio.Lock | None = None

    async def start(self) -> None:
        if not self.config.enabled:
            logger.info("NapCat notifications are disabled")
            return
        self._loop = asyncio.get_running_loop()
        self._send_lock = asyncio.Lock()
        self._worker_task = asyncio.create_task(self._run(), name="napcat-notifier")
        logger.info("NapCat notifier enabled for %s with %d configured destinations", self.config.url, len(self.config.destinations))

    async def close(self) -> None:
        if self._worker_task is None:
            return
        self._worker_task.cancel()
        try:
            await self._worker_task
        except asyncio.CancelledError:
            pass
        finally:
            self._worker_task = None
            self._websocket = None
            self._send_lock = None
            self._loop = None
        logger.info("NapCat notifier stopped")

    def notify_order_submitted(self, agent_display_name: str, target_keys: list[str], order: OrderRecord) -> None:
        if not self.config.notify_on_submit:
            logger.debug("NapCat submit notifications are disabled for order %s", order.order_id)
            return
        message = self._format_order_submitted(agent_display_name, order)
        self._enqueue_for_targets(target_keys, order.agent_id, order.order_id, "submit", message)

    def notify_order_canceled(self, agent_display_name: str, target_keys: list[str], order: OrderRecord) -> None:
        if not self.config.notify_on_cancel:
            logger.debug("NapCat cancel notifications are disabled for order %s", order.order_id)
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
            logger.debug("NapCat fill notifications are disabled for order %s", order.order_id)
            return
        message = self._format_order_filled(agent_display_name, order, fill)
        self._enqueue_for_targets(target_keys, order.agent_id, order.order_id, "fill", message)

    async def _run(self) -> None:
        while True:
            try:
                await self._run_connection()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NapCat notifier worker crashed")
            logger.warning(
                "NapCat notifier reconnecting in %.1f seconds",
                self.config.reconnect_interval_seconds,
            )
            await asyncio.sleep(self.config.reconnect_interval_seconds)

    async def _run_connection(self) -> None:
        headers: dict[str, str] = {}
        if self.config.access_token:
            headers["Authorization"] = f"Bearer {self.config.access_token}"
        logger.info("Connecting to NapCat WebSocket at %s", self.config.url)
        async with websockets.connect(
            self.config.url,
            additional_headers=headers or None,
            open_timeout=self.config.request_timeout_seconds,
        ) as websocket:
            self._websocket = websocket
            login_info = await self._fetch_login_info(websocket)
            logger.info(
                "Connected to NapCat WebSocket at %s as %s (%s)",
                self.config.url,
                login_info.get("nickname"),
                login_info.get("user_id"),
            )
            try:
                await websocket.wait_closed()
            finally:
                if self._websocket is websocket:
                    self._websocket = None

    async def _fetch_login_info(self, websocket) -> dict[str, Any]:
        try:
            response = await self._call_api(websocket, "get_login_info", {})
        except Exception:
            logger.exception("NapCat get_login_info failed during connection verification")
            raise
        data = response.get("data")
        if not isinstance(data, dict):
            logger.warning("NapCat get_login_info returned unexpected data payload: %r", response)
            raise ValueError("NapCat get_login_info returned invalid data")
        return data

    async def _send_message(
        self,
        destination_key: str,
        destination_type: str,
        action: str,
        params: dict[str, str],
        agent_id: str,
        order_id: str,
        event_name: str,
    ) -> None:
        websocket = self._websocket
        send_lock = self._send_lock
        if websocket is None or send_lock is None:
            logger.warning(
                "Skipping NapCat %s notification for agent=%s order=%s because notifier is not connected",
                event_name,
                agent_id,
                order_id,
            )
            return
        try:
            async with send_lock:
                response = await self._call_api(websocket, action, params)
        except ConnectionClosed:
            logger.exception(
                "NapCat connection closed while sending %s notification for agent=%s order=%s target=%s",
                event_name,
                agent_id,
                order_id,
                destination_key,
            )
            return
        except Exception:
            logger.exception(
                "NapCat send failed for event=%s agent=%s order=%s target=%s",
                event_name,
                agent_id,
                order_id,
                destination_key,
            )
            return
        logger.info(
            "Sent NapCat %s notification for agent=%s order=%s to %s(%s)",
            event_name,
            agent_id,
            order_id,
            destination_type,
            destination_key,
        )
        logger.debug(
            "NapCat response for event=%s agent=%s order=%s target=%s: %r",
            event_name,
            agent_id,
            order_id,
            destination_key,
            response,
        )

    async def _call_api(self, websocket, action: str, params: dict[str, str]) -> dict[str, Any]:
        echo = secrets.token_hex(12)
        request = {
            "action": action,
            "params": params,
            "echo": echo,
        }
        await websocket.send(json.dumps(request, ensure_ascii=False))
        response = await self._receive_response(websocket, echo)
        status = response.get("status")
        retcode = response.get("retcode")
        if status != "ok" or retcode not in (None, 0):
            logger.warning(
                "NapCat API action=%s returned non-ok response status=%r retcode=%r response=%r",
                action,
                status,
                retcode,
                response,
            )
            raise ValueError(f"NapCat API action {action} failed")
        return response

    async def _receive_response(self, websocket, echo: str) -> dict[str, Any]:
        while True:
            raw = await asyncio.wait_for(websocket.recv(), timeout=self.config.request_timeout_seconds)
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Received non-JSON NapCat WebSocket payload: %r", raw)
                continue
            if not isinstance(payload, dict):
                logger.warning("Received unexpected NapCat WebSocket payload: %r", payload)
                continue
            if payload.get("echo") == echo:
                return payload
            post_type = payload.get("post_type")
            if post_type is not None:
                logger.debug("Ignoring NapCat event post_type=%r while waiting for API response", post_type)
                continue
            logger.debug("Ignoring unrelated NapCat response while waiting for echo=%s: %r", echo, payload)

    def _enqueue_for_targets(self, target_keys: list[str], agent_id: str, order_id: str, event_name: str, message_text: str) -> None:
        if not self.config.enabled:
            logger.debug("Skipping NapCat %s notification for order %s because notifier is disabled", event_name, order_id)
            return
        loop = self._loop
        if loop is None:
            logger.warning(
                "Skipping NapCat %s notification for agent=%s order=%s because notifier is not started",
                event_name,
                agent_id,
                order_id,
            )
            return
        if not target_keys:
            logger.debug("Skipping NapCat %s notification for agent=%s order=%s because no target keys are configured", event_name, agent_id, order_id)
            return
        for target_key in target_keys:
            target = self.config.destinations.get(target_key)
            if target is None:
                logger.warning(
                    "Unknown NapCat destination key %r configured for agent=%s order=%s",
                    target_key,
                    agent_id,
                    order_id,
                )
                continue
            if isinstance(target, NapCatPrivateTargetConfig):
                destination_type = "private"
                action = "send_private_msg"
                params = {
                    "user_id": target.user_id,
                    "message": message_text,
                }
            else:
                destination_type = "group"
                action = "send_group_msg"
                params = {
                    "group_id": target.group_id,
                    "message": message_text,
                }
            logger.info(
                "Sending NapCat %s notification for agent=%s order=%s to %s(%s)",
                event_name,
                agent_id,
                order_id,
                destination_type,
                target_key,
            )
            future = asyncio.run_coroutine_threadsafe(
                self._send_message(
                    destination_key=target_key,
                    destination_type=destination_type,
                    action=action,
                    params=params,
                    agent_id=agent_id,
                    order_id=order_id,
                    event_name=event_name,
                ),
                loop,
            )
            future.add_done_callback(self._log_send_task_failure)

    @staticmethod
    def _log_send_task_failure(future) -> None:
        try:
            future.result()
        except Exception:
            logger.exception("Unexpected NapCat send task failure")

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
