"""NapCat QQ notification service."""

import asyncio
import json
import secrets
import threading
from dataclasses import dataclass
from logging import getLogger
from pathlib import Path
from typing import TypeAlias

import websockets
from websockets.exceptions import ConnectionClosed
from pydantic import BaseModel, Field

from quant_arena.config import NapCatConfig, NapCatPrivateTargetConfig
from quant_arena.models import FillRecord, OrderRecord

logger = getLogger(__name__)


NapCatTextSegmentData: TypeAlias = dict[str, str]
NapCatReplySegmentData: TypeAlias = dict[str, str]
NapCatSegment: TypeAlias = dict[str, str | NapCatTextSegmentData | NapCatReplySegmentData]
NapCatMessagePayload: TypeAlias = str | list[NapCatSegment]
NapCatRequestParams: TypeAlias = dict[str, str | NapCatMessagePayload]
NapCatResponseData: TypeAlias = dict[str, str | int | float | bool | None]
NapCatResponse: TypeAlias = dict[str, str | int | float | bool | None | NapCatResponseData]


@dataclass(slots=True)
class QueuedNapCatMessage:
    """One outbound NapCat request."""

    destination_key: str
    destination_type: str
    destination_id: str
    action: str
    params: NapCatRequestParams
    agent_id: str
    order_id: str
    event_name: str
    message_text: str
    attempt: int = 0


class NapCatOrderMessageState(BaseModel):
    """Persisted NapCat message ids for one order in one target."""

    submitted_message_id: str | None = None
    canceled_message_id: str | None = None
    filled_message_id: str | None = None


class NapCatTargetMessageState(BaseModel):
    """Persisted NapCat message ids grouped by destination target."""

    orders: dict[str, NapCatOrderMessageState] = Field(default_factory=dict)


class NapCatMessageState(BaseModel):
    """Per-agent NapCat auxiliary message state."""

    targets: dict[str, NapCatTargetMessageState] = Field(default_factory=dict)


class NapCatNotifier:
    """Send backend notifications to NapCat over WebSocket."""

    def __init__(self, config: NapCatConfig, agents_root: Path):
        self.config = config
        self.agents_root = agents_root
        self._queue: asyncio.Queue[QueuedNapCatMessage] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._thread: threading.Thread | None = None
        self._ready: threading.Event = threading.Event()
        self._stop_event: asyncio.Event | None = None
        self._state_lock = threading.Lock()

    async def start(self) -> None:
        """
        Start the NapCat notifier on a dedicated thread + event loop.

        Running the WebSocket worker on its own loop keeps the websockets
        keepalive task and per-request `wait_for` deadlines isolated from
        whatever else is happening on the main FastAPI/asyncio loop or its
        threadpool — so heavy intraday polling / pandas work in another
        thread can no longer starve the heartbeat into a 1011 close.
        """
        if not self.config.enabled:
            logger.info("NapCat notifications are disabled")
            return
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._thread_main,
            name="napcat-notifier-loop",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            logger.error("NapCat notifier thread failed to initialize within 5s")
            return
        logger.info(
            "NapCat notifier enabled for %s with %d configured destinations",
            self.config.url,
            len(self.config.destinations),
        )

    async def close(self) -> None:
        if self._thread is None:
            return
        loop = self._loop
        stop_event = self._stop_event
        if loop is not None and stop_event is not None and loop.is_running():
            loop.call_soon_threadsafe(stop_event.set)
        self._thread.join(timeout=5.0)
        self._thread = None
        self._loop = None
        self._queue = None
        self._stop_event = None
        self._worker_task = None
        logger.info("NapCat notifier stopped")

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._queue = asyncio.Queue()
        self._stop_event = asyncio.Event()
        self._ready.set()
        try:
            loop.run_until_complete(self._supervise())
        except Exception:
            logger.exception("NapCat notifier thread crashed")
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            finally:
                loop.close()

    async def _supervise(self) -> None:
        assert self._stop_event is not None
        worker = asyncio.create_task(self._run(), name="napcat-notifier")
        self._worker_task = worker
        stop_wait = asyncio.create_task(self._stop_event.wait(), name="napcat-stop")
        try:
            done, _ = await asyncio.wait(
                {worker, stop_wait}, return_when=asyncio.FIRST_COMPLETED
            )
            if stop_wait in done:
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass
        finally:
            stop_wait.cancel()
            try:
                await stop_wait
            except (asyncio.CancelledError, Exception):
                pass

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
            else:
                logger.warning(
                    "NapCat WebSocket disconnected; reconnecting in %.1f seconds",
                    self.config.reconnect_interval_seconds,
                )
            if self._worker_task is None:
                return
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
            ping_interval=20,
            ping_timeout=60,
            close_timeout=5,
            max_queue=64,
        ) as websocket:
            login_info = await self._fetch_login_info(websocket)
            logger.info(
                "Connected to NapCat WebSocket at %s as %s (%s)",
                self.config.url,
                login_info.get("nickname"),
                login_info.get("user_id"),
            )
            queue = self._queue
            if queue is None:
                logger.warning("NapCat notifier queue is not initialized after connect")
                return
            while True:
                outbound = await queue.get()
                try:
                    await self._send_message(websocket, outbound)
                except asyncio.CancelledError:
                    raise
                except ConnectionClosed:
                    logger.warning(
                        "NapCat WebSocket disconnected from %s with code=%r reason=%r",
                        self.config.url,
                        websocket.close_code,
                        websocket.close_reason,
                    )
                    self._requeue_message(outbound, "connection closed before send completed")
                    raise
                except TimeoutError:
                    # Do NOT drop the socket. NapCat sometimes takes >request_timeout
                    # to ack a send_msg; the connection itself is fine as long as
                    # websockets ping/pong is still flowing on its own task.
                    logger.warning(
                        "NapCat send timed out for event=%s agent=%s order=%s target=%s; keeping connection",
                        outbound.event_name,
                        outbound.agent_id,
                        outbound.order_id,
                        outbound.destination_key,
                    )
                    self._requeue_message(outbound, "request timed out")
                except Exception:
                    logger.exception(
                        "Unexpected NapCat send failure for event=%s agent=%s order=%s target=%s",
                        outbound.event_name,
                        outbound.agent_id,
                        outbound.order_id,
                        outbound.destination_key,
                    )
                finally:
                    queue.task_done()

    async def _fetch_login_info(self, websocket) -> NapCatResponseData:
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

    async def _send_message(self, websocket, outbound: QueuedNapCatMessage) -> None:
        response = await self._call_api(websocket, outbound.action, outbound.params)
        self._record_sent_message_id(outbound, response)
        logger.info(
            "Sent NapCat %s notification for agent=%s order=%s to %s(%s)",
            outbound.event_name,
            outbound.agent_id,
            outbound.order_id,
            outbound.destination_type,
            outbound.destination_key,
        )
        logger.debug(
            "NapCat response for event=%s agent=%s order=%s target=%s: %r",
            outbound.event_name,
            outbound.agent_id,
            outbound.order_id,
            outbound.destination_key,
            response,
        )

    async def _call_api(self, websocket, action: str, params: NapCatRequestParams) -> NapCatResponse:
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

    async def _receive_response(self, websocket, echo: str) -> NapCatResponse:
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

    def _requeue_message(self, outbound: QueuedNapCatMessage, reason: str) -> None:
        if outbound.attempt >= 1:
            logger.warning(
                "Dropping NapCat %s notification for agent=%s order=%s target=%s after retry failure: %s",
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
                "Cannot requeue NapCat %s notification for agent=%s order=%s target=%s: %s",
                outbound.event_name,
                outbound.agent_id,
                outbound.order_id,
                outbound.destination_key,
                reason,
            )
            return
        retried = QueuedNapCatMessage(
            destination_key=outbound.destination_key,
            destination_type=outbound.destination_type,
            destination_id=outbound.destination_id,
            action=outbound.action,
            params=outbound.params,
            agent_id=outbound.agent_id,
            order_id=outbound.order_id,
            event_name=outbound.event_name,
            message_text=outbound.message_text,
            attempt=outbound.attempt + 1,
        )
        logger.warning(
            "Requeueing NapCat %s notification for agent=%s order=%s target=%s: %s",
            outbound.event_name,
            outbound.agent_id,
            outbound.order_id,
            outbound.destination_key,
            reason,
        )
        loop.call_soon_threadsafe(queue.put_nowait, retried)

    def _enqueue_for_targets(self, target_keys: list[str], agent_id: str, order_id: str, event_name: str, message_text: str) -> None:
        if not self.config.enabled:
            logger.debug("Skipping NapCat %s notification for order %s because notifier is disabled", event_name, order_id)
            return
        queue = self._queue
        loop = self._loop
        if queue is None or loop is None:
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
                outbound = QueuedNapCatMessage(
                    destination_key=target_key,
                    destination_type="private",
                    destination_id=target.user_id,
                    action="send_private_msg",
                    params={
                        "user_id": target.user_id,
                        "message": self._build_message_payload(agent_id, target_key, order_id, event_name, message_text),
                    },
                    agent_id=agent_id,
                    order_id=order_id,
                    event_name=event_name,
                    message_text=message_text,
                )
            else:
                outbound = QueuedNapCatMessage(
                    destination_key=target_key,
                    destination_type="group",
                    destination_id=target.group_id,
                    action="send_group_msg",
                    params={
                        "group_id": target.group_id,
                        "message": self._build_message_payload(agent_id, target_key, order_id, event_name, message_text),
                    },
                    agent_id=agent_id,
                    order_id=order_id,
                    event_name=event_name,
                    message_text=message_text,
                )
            logger.info(
                "Queueing NapCat %s notification for agent=%s order=%s to %s(%s)",
                event_name,
                agent_id,
                order_id,
                outbound.destination_type,
                target_key,
            )
            loop.call_soon_threadsafe(queue.put_nowait, outbound)

    def _build_message_payload(
        self,
        agent_id: str,
        target_key: str,
        order_id: str,
        event_name: str,
        message_text: str,
    ) -> NapCatMessagePayload:
        if event_name == "submit":
            return message_text
        state = self._load_message_state(agent_id)
        target_state = state.targets.get(target_key)
        if target_state is None:
            return message_text
        order_state = target_state.orders.get(order_id)
        if order_state is None or not order_state.submitted_message_id:
            return message_text
        return [
            {
                "type": "reply",
                "data": {
                    "id": order_state.submitted_message_id,
                },
            },
            {
                "type": "text",
                "data": {
                    "text": message_text,
                },
            },
        ]

    def _record_sent_message_id(self, outbound: QueuedNapCatMessage, response: NapCatResponse) -> None:
        message_id = self._extract_message_id(response)
        if message_id is None:
            logger.debug(
                "NapCat response for event=%s agent=%s order=%s target=%s did not include a message_id",
                outbound.event_name,
                outbound.agent_id,
                outbound.order_id,
                outbound.destination_key,
            )
            return
        state = self._load_message_state(outbound.agent_id)
        target_state = state.targets.setdefault(outbound.destination_key, NapCatTargetMessageState())
        order_state = target_state.orders.setdefault(outbound.order_id, NapCatOrderMessageState())
        if outbound.event_name == "submit":
            order_state.submitted_message_id = message_id
        elif outbound.event_name == "cancel":
            order_state.canceled_message_id = message_id
        elif outbound.event_name == "fill":
            order_state.filled_message_id = message_id
        self._save_message_state(outbound.agent_id, state)

    @staticmethod
    def _extract_message_id(response: NapCatResponse) -> str | None:
        data = response.get("data")
        if not isinstance(data, dict):
            return None
        message_id = data.get("message_id")
        if message_id is None:
            return None
        return str(message_id)

    def _load_message_state(self, agent_id: str) -> NapCatMessageState:
        path = self._message_state_path(agent_id)
        with self._state_lock:
            if not path.exists():
                return NapCatMessageState()
            with path.open("r", encoding="utf-8") as handle:
                return NapCatMessageState.model_validate(json.load(handle))

    def _save_message_state(self, agent_id: str, state: NapCatMessageState) -> None:
        with self._state_lock:
            self._agent_dir(agent_id).mkdir(parents=True, exist_ok=True)
            with self._message_state_path(agent_id).open("w", encoding="utf-8") as handle:
                json.dump(state.model_dump(mode="json"), handle, ensure_ascii=False, indent="\t")
                handle.write("\n")

    def _agent_dir(self, agent_id: str) -> Path:
        return self.agents_root / agent_id

    def _message_state_path(self, agent_id: str) -> Path:
        return self._agent_dir(agent_id) / "napcat_message_state.json"

    @staticmethod
    def _format_order_submitted(agent_display_name: str, order: OrderRecord) -> str:
        return (
            f"{agent_display_name} 提交订单\n"
            f"操作：{order.side} {'买入' if order.side == 'buy' else '卖出'}\n"
            f"代码：{order.code}\n"
            f"数量：{order.quantity}\n"
            f"价格：{order.limit_price:.2f}\n"
            f"备注：{order.comment}\n"
            f"时间：{order.submitted_at.isoformat(timespec='seconds')}"
        )

    @staticmethod
    def _format_order_canceled(agent_display_name: str, order: OrderRecord) -> str:
        # canceled_at = order.canceled_at.isoformat() if order.canceled_at is not None else "unknown"
        reason_line = ""
        if order.rejection_reason:
            reason_line = f"\n原因：{order.rejection_reason}"
        return (
            f"{agent_display_name} 撤单："
            f"{'买入' if order.side == 'buy' else '卖出'} {order.code} "
            f"数量 {order.quantity} "
            f"价格 {order.limit_price:.2f}"
            f"{reason_line}"
        )

    @staticmethod
    def _format_order_filled(agent_display_name: str, order: OrderRecord, fill: FillRecord) -> str:
        return (
            f"{agent_display_name} 成交："
            f"{'买入' if order.side == 'buy' else '卖出'} {order.code} "
            f"数量 {fill.quantity} "
            f"成交价 {fill.executed_price:.2f}"
        )
