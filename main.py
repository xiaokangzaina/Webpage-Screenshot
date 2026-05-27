"""网页截图定时推送插件。"""

from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path
from sys import maxsize
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import StarTools
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

try:
    from playwright.async_api import async_playwright
except Exception:  # pragma: no cover
    async_playwright = None


DEFAULT_CONFIG: dict[str, Any] = {
    "tasks": [],
    "default_viewport_width": 1280,
    "default_viewport_height": 720,
    "default_wait_seconds": 5,
    "send_text": True,
    "screenshot_text": "{name} 网页截图",
    "platform": "aiocqhttp",
}


class WebpageScreenshot(star.Star):
    """按固定间隔截取网页整页，并主动推送到群聊或私聊。"""

    def __init__(self, context: star.Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.context = context
        self.config = self._merge_config(config or {})
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self.screenshot_dir = Path(get_astrbot_temp_path()) / "astrbot_plugin_webpage_screenshot"
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        if async_playwright is None:
            logger.error("网页截图插件缺少依赖 playwright，请先安装 requirements.txt 内依赖并执行 playwright install chromium")
            return

        enabled_tasks = [
            task for task in self.config.get("tasks", [])
            if isinstance(task, dict) and task.get("enabled", True)
        ]
        if not enabled_tasks:
            logger.warning("网页截图插件未配置启用的推送任务")
            return

        self._running = True
        for index, task_conf in enumerate(enabled_tasks, start=1):
            self._tasks.append(asyncio.create_task(self._run_task(index, task_conf)))
        logger.info(f"网页截图插件已启动，启用任务数：{len(self._tasks)}")

    async def terminate(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("网页截图插件已停止")

    def _merge_config(self, config: dict[str, Any]) -> dict[str, Any]:
        merged = dict(DEFAULT_CONFIG)
        merged.update(config)
        tasks = merged.get("tasks")
        merged["tasks"] = [task for task in tasks if isinstance(task, dict)] if isinstance(tasks, list) else []
        return merged

    async def _run_task(self, index: int, task_conf: dict[str, Any]) -> None:
        name = str(task_conf.get("name") or f"网页截图任务{index}")
        interval_minutes = self._safe_float(task_conf.get("interval_minutes"), 60.0)
        interval_seconds = max(60, int(interval_minutes * 60))

        await asyncio.sleep(interval_seconds)

        while self._running:
            started = time.time()
            try:
                await self._capture_and_send(index, name, task_conf)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"网页截图任务失败：{name}，原因：{exc}", exc_info=True)

            elapsed = time.time() - started
            await asyncio.sleep(max(5, interval_seconds - int(elapsed)))

    @filter.event_message_type(filter.EventMessageType.ALL, priority=maxsize)
    async def status_command(self, event: AstrMessageEvent):
        """收到纯文本“状态”时，仅在配置目标会话内立刻截图。"""
        if not self._is_status_request(event):
            return

        task_index, task_conf = self._find_task_for_event(event)
        if task_conf is None:
            event.stop_event()
            return

        if async_playwright is None:
            await event.send(event.plain_result("网页截图依赖 playwright 未安装"))
            event.stop_event()
            return

        name = str(task_conf.get("name") or "网页状态")
        url = str(task_conf.get("url") or "").strip()
        if not url:
            await event.send(event.plain_result("网页截图任务未填写网页地址"))
            event.stop_event()
            return

        try:
            width = int(self._safe_float(task_conf.get("viewport_width"), self.config["default_viewport_width"]))
            height = int(self._safe_float(task_conf.get("viewport_height"), self.config["default_viewport_height"]))
            wait_seconds = self._safe_float(task_conf.get("wait_seconds"), self.config["default_wait_seconds"])
            timeout_ms = int(max(10, self._safe_float(task_conf.get("timeout_seconds"), 30.0)) * 1000)
            image_path = await self._capture_viewport(task_index, url, width, height, wait_seconds, timeout_ms)
        except Exception as exc:
            logger.error(f"即时状态截图失败：{exc}", exc_info=True)
            await event.send(event.plain_result(f"状态截图失败：{exc}"))
            event.stop_event()
            return

        await event.send(MessageChain(chain=self._build_message_chain(name, url, image_path, task_conf)))
        event.stop_event()

    async def _capture_and_send(self, index: int, name: str, task_conf: dict[str, Any]) -> None:
        url = str(task_conf.get("url") or "").strip()
        target_type = self._resolve_target_type(task_conf)
        target_id = str(task_conf.get("target_id") or "").strip()
        if not url or not target_id:
            logger.warning(f"网页截图任务配置不完整：{name}")
            return

        width = int(self._safe_float(task_conf.get("viewport_width"), self.config["default_viewport_width"]))
        height = int(self._safe_float(task_conf.get("viewport_height"), self.config["default_viewport_height"]))
        wait_seconds = self._safe_float(task_conf.get("wait_seconds"), self.config["default_wait_seconds"])
        timeout_ms = int(max(10, self._safe_float(task_conf.get("timeout_seconds"), 30.0)) * 1000)

        image_path = await self._capture_viewport(index, url, width, height, wait_seconds, timeout_ms)
        chain = self._build_message_chain(name, url, image_path, task_conf)

        await StarTools.send_message_by_id(
            type=target_type,
            id=target_id,
            message_chain=MessageChain(chain=chain),
            platform=str(self.config.get("platform") or "aiocqhttp"),
        )
        logger.info(f"网页截图已推送：{name} -> {target_type}:{target_id}")

    async def _capture_viewport(
        self,
        index: int,
        url: str,
        width: int,
        height: int,
        wait_seconds: float,
        timeout_ms: int,
    ) -> Path:
        safe_name = hashlib.md5(f"{index}:{url}".encode("utf-8")).hexdigest()[:12]
        image_path = self.screenshot_dir / f"webpage_{safe_name}.png"

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={"width": width, "height": height})
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                if wait_seconds > 0:
                    await page.wait_for_timeout(int(wait_seconds * 1000))
                await page.screenshot(path=str(image_path), full_page=True)
            finally:
                await browser.close()
        return image_path

    def _find_task_for_event(self, event: AstrMessageEvent) -> tuple[int, dict[str, Any] | None]:
        """只允许在任务配置的目标会话内触发即时截图。"""
        for index, task_conf in enumerate(self.config.get("tasks", []), start=1):
            if not isinstance(task_conf, dict) or not task_conf.get("enabled", True):
                continue
            target_id = str(task_conf.get("target_id") or "").strip()
            if not target_id:
                continue
            target_type = self._resolve_target_type(task_conf)
            if target_type == "GroupMessage":
                current_id = str(event.get_group_id() or "").strip()
                if current_id == target_id:
                    return index, task_conf
            else:
                candidates = {
                    str(event.get_sender_id() or "").strip(),
                    str(getattr(event, "unified_msg_origin", "") or "").strip(),
                }
                if target_id in candidates:
                    return index, task_conf
        return 0, None

    def _is_status_request(self, event: AstrMessageEvent) -> bool:
        """必须带唤醒词或艾特机器人，且正文为“状态”。"""
        raw_text = str(event.message_str or "").strip()
        if not raw_text:
            return False

        if self._is_at_or_wake_command(event) and raw_text == "状态":
            return True

        wake_prefixes = self._get_wake_prefixes(event)
        for prefix in wake_prefixes:
            prefix_text = str(prefix or "").strip()
            if prefix_text and raw_text.startswith(prefix_text):
                return raw_text[len(prefix_text):].strip() == "状态"

        self_id = str(event.get_self_id() or "").strip()
        at_me = False
        for seg in event.get_messages():
            qq = str(getattr(seg, "qq", "") or "").strip()
            if self_id and qq == self_id:
                at_me = True
                break
        if at_me:
            return raw_text.replace(f"@{self_id}", "", 1).strip() == "状态" or raw_text == "状态"
        return False

    def _is_at_or_wake_command(self, event: AstrMessageEvent) -> bool:
        """兼容 AstrBot 的唤醒词/艾特判断。"""
        checker = getattr(event, "is_at_or_wake_command", None)
        if callable(checker):
            try:
                return bool(checker())
            except TypeError:
                try:
                    return bool(checker(self_id=event.get_self_id()))
                except Exception:
                    return False
            except Exception:
                return False
        return bool(checker)

    def _get_wake_prefixes(self, event: AstrMessageEvent) -> list[str]:
        """兼容不同 AstrBot 版本获取唤醒词配置。"""
        try:
            cfg = self.context.get_config(umo=getattr(event, "unified_msg_origin", None))
            prefixes = cfg.get("wake_prefix", []) if isinstance(cfg, dict) else []
        except Exception:
            prefixes = []
        if isinstance(prefixes, str):
            return [prefixes]
        if isinstance(prefixes, list):
            return [str(item) for item in prefixes]
        return []

    def _build_message_chain(
        self,
        name: str,
        url: str,
        image_path: Path,
        task_conf: dict[str, Any],
    ) -> list[Any]:
        chain = []
        if bool(self.config.get("send_text", True)):
            text = str(
                task_conf.get("screenshot_text")
                or self.config.get("screenshot_text")
                or "{name} 网页截图"
            )
            text = text.format(name=name, url=url)
            if text.strip():
                chain.append(Comp.Plain(text))
        chain.append(Comp.Image.fromFileSystem(str(image_path)))
        return chain

    def _resolve_target_type(self, task_conf: dict[str, Any]) -> str:
        """兼容中文配置和旧版英文配置。"""
        send_to = str(task_conf.get("send_to") or "").strip()
        if send_to == "私聊":
            return "PrivateMessage"
        if send_to == "群聊":
            return "GroupMessage"
        legacy = str(task_conf.get("target_type") or "GroupMessage").strip()
        if legacy in {"PrivateMessage", "FriendMessage", "私聊"}:
            return "PrivateMessage"
        return "GroupMessage"

    def _safe_float(self, value: Any, default: float) -> float:
        try:
            return float(value)
        except Exception:
            return float(default)
