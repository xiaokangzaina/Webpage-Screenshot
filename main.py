"""网页截图定时推送插件。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from sys import maxsize
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import StarTools, register
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

try:
    from playwright.async_api import async_playwright
except Exception:  # pragma: no cover
    async_playwright = None

try:
    from .web import WebpageScreenshotWebController
except Exception:  # pragma: no cover
    WebpageScreenshotWebController = None


DEFAULT_CONFIG: dict[str, Any] = {
    "tasks": [],
    "default_viewport_width": 1280,
    "default_viewport_height": 720,
    "default_wait_seconds": 5,
    "send_text": True,
    "mention_and_quote_sender": False,
    "notify_on_failure": True,
    "screenshot_text": "{name} 网页截图",
    "platform": "aiocqhttp",
}


@register(
    "astrbot_plugin_webpage_screenshot",
    "xiaokangzaina",
    "定时截取指定网页并推送到群聊或私聊，支持指定会话手动获取截图",
    "1.0.4",
)
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
        self._register_web_page()

    def _register_web_page(self) -> None:
        if WebpageScreenshotWebController is None:
            return
        try:
            self.web = WebpageScreenshotWebController(self.context, self)
            self.web.register_routes()
            logger.info("网页截图插件配置页 Web API 已注册")
        except Exception as exc:
            logger.warning("网页截图插件配置页注册失败：%s", exc)

    async def initialize(self) -> None:
        await self._cancel_existing_scheduler_tasks()

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
        current_task = asyncio.current_task()
        owner = f"{id(self)}"
        for index, task_conf in enumerate(enabled_tasks, start=1):
            task = asyncio.create_task(
                self._run_task(index, task_conf),
                name=f"astrbot_plugin_webpage_screenshot:{index}:{owner}",
            )
            if current_task is not None:
                setattr(task, "_webpage_screenshot_owner", current_task)
            self._tasks.append(task)
        logger.info(f"网页截图插件已启动，启用任务数：{len(self._tasks)}")

    async def terminate(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("网页截图插件已停止")

    async def _cancel_existing_scheduler_tasks(self) -> None:
        """取消热重载/重复初始化残留的旧定时任务，避免同一配置重复推送。"""
        current_task = asyncio.current_task()
        stale_tasks = []
        for task in asyncio.all_tasks():
            if task is current_task or task.done():
                continue
            try:
                task_name = task.get_name()
            except Exception:
                task_name = ""
            if task_name.startswith("astrbot_plugin_webpage_screenshot:"):
                stale_tasks.append(task)

        if not stale_tasks:
            return

        logger.warning(f"发现残留网页截图定时任务 {len(stale_tasks)} 个，已取消以避免重复推送")
        for task in stale_tasks:
            task.cancel()
        await asyncio.gather(*stale_tasks, return_exceptions=True)

    def _merge_config(self, config: dict[str, Any]) -> dict[str, Any]:
        merged = dict(DEFAULT_CONFIG)
        merged.update(config)
        plugin_tasks_file = Path(__file__).resolve().parent / "data" / "webpage_screenshot_tasks.json"
        try:
            plugin_tasks = json.loads(plugin_tasks_file.read_text(encoding="utf-8-sig"))
            if isinstance(plugin_tasks, list):
                merged["tasks"] = [task for task in plugin_tasks if isinstance(task, dict)]
        except Exception:
            pass

        screenshot_settings = config.get("screenshot_settings")
        if isinstance(screenshot_settings, dict):
            for key in (
                "default_viewport_width",
                "default_viewport_height",
                "default_wait_seconds",
            ):
                if key in screenshot_settings:
                    merged[key] = screenshot_settings[key]

        message_settings = config.get("message_settings")
        if isinstance(message_settings, dict):
            for key in (
                "send_text",
                "screenshot_text",
                "mention_and_quote_sender",
                "notify_on_failure",
            ):
                if key in message_settings:
                    merged[key] = message_settings[key]

        advanced_settings = config.get("advanced_settings")
        if isinstance(advanced_settings, dict) and "platform" in advanced_settings:
            merged["platform"] = advanced_settings["platform"]

        tasks = merged.get("tasks")
        merged["tasks"] = [task for task in tasks if isinstance(task, dict)] if isinstance(tasks, list) else []
        return merged

    async def _run_task(self, index: int, task_conf: dict[str, Any]) -> None:
        while self._running:
            name = str(task_conf.get("name") or f"网页截图任务{index}")
            try:
                live_conf = self.config.get("tasks", [])[index - 1] if 0 < index <= len(self.config.get("tasks", [])) else task_conf
            except Exception:
                live_conf = task_conf
            interval_minutes = self._safe_float(live_conf.get("interval_minutes"), 60.0)
            interval_seconds = max(60, int(interval_minutes * 60))

            now = time.time()
            remainder = now % interval_seconds
            wait_seconds = interval_seconds - remainder
            if wait_seconds < 5:
                wait_seconds += interval_seconds
            logger.debug(
                "网页截图任务 %s 下一次按系统时间触发，等待 %.1f 秒",
                name,
                wait_seconds,
            )
            await asyncio.sleep(wait_seconds)
            if not self._running:
                break

            try:
                await self._capture_and_send(index, name, live_conf)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"网页截图任务失败：{name}，原因：{exc}", exc_info=True)
                await self._send_failure_notice(name, live_conf, exc)


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

        chain = self._build_sender_prefix(event)
        chain.extend(self._build_message_chain(name, url, image_path, task_conf))
        await event.send(MessageChain(chain=chain))
        event.stop_event()

    async def _send_failure_notice(self, name: str, task_conf: dict[str, Any], exc: Exception) -> None:
        """定时截图失败时通知目标会话。"""
        if not bool(self.config.get("notify_on_failure", True)):
            return

        target_type = self._resolve_target_type(task_conf)
        target_id = str(task_conf.get("target_id") or "").strip()
        if not target_id:
            return

        url = str(task_conf.get("url") or "").strip()
        text = f"网页截图失败：{name}\n原因：{exc}"
        if url:
            text += f"\n地址：{url}"

        try:
            await StarTools.send_message_by_id(
                type=target_type,
                id=target_id,
                message_chain=MessageChain(chain=[Comp.Plain(text)]),
                platform=str(self.config.get("platform") or "aiocqhttp"),
            )
        except Exception as notice_exc:
            logger.error(f"网页截图失败通知发送失败：{notice_exc}", exc_info=True)

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


    def _build_sender_prefix(self, event: AstrMessageEvent) -> list[Any]:
        """Build optional quote/mention prefix for manual status replies."""
        if not bool(self.config.get("mention_and_quote_sender", False)):
            return []

        prefix: list[Any] = []
        quote_message_id = str(
            getattr(getattr(event, "message_obj", None), "message_id", "") or ""
        ).strip()
        sender_id = str(getattr(event, "get_sender_id", lambda: "")() or "").strip()

        if quote_message_id:
            prefix.append(Comp.Reply(id=quote_message_id))
        if sender_id:
            prefix.append(Comp.At(qq=sender_id))
            prefix.append(Comp.Plain(" "))
        return prefix

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
