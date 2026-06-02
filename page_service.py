from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger

PLUGIN_DIR = Path(__file__).resolve().parent
DATA_DIR = PLUGIN_DIR.parents[1]
CONFIG_FILE = DATA_DIR / "config" / "astrbot_plugin_webpage_screenshot_config.json"
PLUGIN_TASKS_FILE = PLUGIN_DIR / "data" / "webpage_screenshot_tasks.json"
GROUP_TOUCH_FILE = PLUGIN_DIR / "data" / "group_config_touch_times.json"


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


class WebpageScreenshotPageService:
    """网页截图配置页服务：群列表 + 单群截图任务 + 全局设置。"""

    def __init__(self, plugin: Any):
        self.plugin = plugin
        self.schema = self._load_schema(PLUGIN_DIR / "_conf_schema.json")

    def get_bootstrap_payload(self) -> dict[str, Any]:
        config = self._read_current_config()
        return {
            "schema": self.schema,
            "config": config,
            "groups": self._build_configured_groups(config),
        }

    async def list_groups(self, force: bool = False) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for client in self._iter_qq_clients():
            try:
                result = await client.call_action("get_group_list")
                for item in self._extract_group_list(result):
                    group_id = str(item.get("group_id", "")).strip()
                    if group_id and group_id not in groups:
                        groups[group_id] = self._normalize_group_item(item)
            except Exception as exc:
                logger.debug("[WebpageScreenshot] 获取群列表失败: %s", exc)
        for item in self._build_configured_groups(self._read_current_config()):
            groups.setdefault(item["group_id"], item)
        return self._sort_groups_by_recent_config(groups.values())

    def get_group_config(self, group_id: str) -> dict[str, Any]:
        group_id = str(group_id or "").strip()
        if not group_id:
            raise ValueError("group_id must not be empty")
        config = self._read_current_config()
        task = self._find_group_task(config, group_id) or self._default_group_task(group_id)
        return {"group_info": self._build_group_info(group_id), "config": task}

    def update_group_config(self, group_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        group_id = str(group_id or "").strip()
        if not group_id:
            raise ValueError("group_id must not be empty")
        config = self._read_current_config()
        tasks = self._tasks(config)
        task = self._sanitize_group_task(group_id, payload if isinstance(payload, dict) else {})
        replaced = False
        for index, item in enumerate(tasks):
            if self._is_group_task(item, group_id):
                tasks[index] = task
                replaced = True
                break
        if not replaced:
            tasks.append(task)
        self._write_tasks(tasks)
        self._touch_group_config(group_id)
        self._reload_plugin()
        return self.get_group_config(group_id)

    def reset_group_config(self, group_id: str) -> dict[str, Any]:
        group_id = str(group_id or "").strip()
        config = self._read_current_config()
        tasks = [task for task in self._tasks(config) if not self._is_group_task(task, group_id)]
        self._write_tasks(tasks)
        self._touch_group_config(group_id)
        self._reload_plugin()
        return self.get_group_config(group_id)

    def _tasks(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        tasks = config.get("tasks", [])
        return [self._ensure_task_template(task) for task in tasks if isinstance(task, dict)] if isinstance(tasks, list) else []

    @staticmethod
    def _ensure_task_template(task: dict[str, Any]) -> dict[str, Any]:
        fixed = dict(task)
        fixed["__template_key"] = "webpage"
        return fixed

    def _is_group_task(self, task: dict[str, Any], group_id: str) -> bool:
        return str(task.get("target_id") or "").strip() == group_id and str(task.get("send_to") or "群聊") == "群聊"

    def _find_group_task(self, config: dict[str, Any], group_id: str) -> dict[str, Any] | None:
        for task in self._tasks(config):
            if self._is_group_task(task, group_id):
                return dict(task)
        return None

    def _default_group_task(self, group_id: str) -> dict[str, Any]:
        return {
            "__template_key": "webpage",
            "name": "网页截图",
            "enabled": False,
            "url": "https://example.com",
            "send_to": "群聊",
            "target_id": group_id,
            "interval_minutes": 60,
            "screenshot_text": "",
            "viewport_width": 1280,
            "viewport_height": 720,
            "wait_seconds": 5,
            "timeout_seconds": 30,
            "send_text": True,
            "mention_and_quote_sender": False,
            "notify_on_failure": True,
        }

    def _sanitize_group_task(self, group_id: str, data: dict[str, Any]) -> dict[str, Any]:
        task = self._default_group_task(group_id)
        task.update({
            "name": str(data.get("name") or "网页截图").strip() or "网页截图",
            "enabled": bool(data.get("enabled", False)),
            "url": str(data.get("url") or "").strip(),
            "interval_minutes": _safe_float(data.get("interval_minutes"), 60),
            "screenshot_text": str(data.get("screenshot_text") or ""),
            "viewport_width": _safe_int(data.get("viewport_width"), 1280),
            "viewport_height": _safe_int(data.get("viewport_height"), 720),
            "wait_seconds": _safe_float(data.get("wait_seconds"), 5),
            "timeout_seconds": _safe_float(data.get("timeout_seconds"), 30),
            "send_text": bool(data.get("send_text", True)),
            "mention_and_quote_sender": bool(data.get("mention_and_quote_sender", False)),
            "notify_on_failure": bool(data.get("notify_on_failure", True)),
        })
        return task

    def _build_configured_groups(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        group_ids = {str(task.get("target_id") or "").strip() for task in self._tasks(config) if str(task.get("send_to") or "群聊") == "群聊"}
        return [self._build_group_info(group_id, source="configured") for group_id in sorted(x for x in group_ids if x)]

    def _build_group_info(self, group_id: str, source: str = "fallback") -> dict[str, Any]:
        group_id = str(group_id).strip()
        return {
            "group_id": group_id,
            "group_name": f"群 {group_id}",
            "avatar": f"https://p.qlogo.cn/gh/{group_id}/{group_id}/640",
            "member_count": 0,
            "max_member_count": 0,
            "source": source,
            "config_updated_at": self._group_config_touch_times().get(group_id, 0),
        }

    def _normalize_group_item(self, item: dict[str, Any]) -> dict[str, Any]:
        group_id = str(item.get("group_id", "")).strip()
        data = self._build_group_info(group_id, source="live")
        data.update({
            "group_name": str(item.get("group_name") or f"群 {group_id}"),
            "member_count": _safe_int(item.get("member_count"), 0),
            "max_member_count": _safe_int(item.get("max_member_count"), 0),
        })
        return data

    def _sort_groups_by_recent_config(self, group_list: Any) -> list[dict[str, Any]]:
        return sorted(group_list, key=lambda item: (-_safe_int(item.get("config_updated_at"), 0), str(item.get("group_name") or item.get("group_id") or "")))

    def _group_config_touch_times(self) -> dict[str, int]:
        try:
            data = json.loads(GROUP_TOUCH_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return {str(k): _safe_int(v, 0) for k, v in data.items()} if isinstance(data, dict) else {}

    def _touch_group_config(self, group_id: str) -> None:
        times = self._group_config_touch_times()
        times[str(group_id)] = int(time.time() * 1000)
        GROUP_TOUCH_FILE.parent.mkdir(parents=True, exist_ok=True)
        GROUP_TOUCH_FILE.write_text(json.dumps(times, ensure_ascii=False, indent=2), encoding="utf-8")

    def _iter_qq_clients(self) -> list[Any]:
        clients: list[Any] = []
        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import AiocqhttpAdapter
        except Exception:
            AiocqhttpAdapter = None
        try:
            platform_insts = self.plugin.context.platform_manager.platform_insts
        except Exception:
            platform_insts = []
        for inst in platform_insts:
            if AiocqhttpAdapter is not None and not isinstance(inst, AiocqhttpAdapter):
                continue
            try:
                client = inst.get_client()
            except Exception:
                continue
            if client is not None:
                clients.append(client)
        return clients

    @staticmethod
    def _extract_group_list(result: Any) -> list[dict[str, Any]]:
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        if isinstance(result, dict) and isinstance(result.get("data"), list):
            return [item for item in result["data"] if isinstance(item, dict)]
        return []

    def _read_current_config(self) -> dict[str, Any]:
        data = {}
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
            except Exception:
                data = {}
        if isinstance(getattr(self.plugin, "config", None), dict):
            data.update(self.plugin.config)
        plugin_tasks = self._read_tasks()
        if plugin_tasks:
            data["tasks"] = plugin_tasks
        return data

    def _read_tasks(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(PLUGIN_TASKS_FILE.read_text(encoding="utf-8-sig"))
        except Exception:
            return []
        return [self._ensure_task_template(item) for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def _write_tasks(self, tasks: list[dict[str, Any]]) -> None:
        PLUGIN_TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        fixed = [self._ensure_task_template(task) for task in tasks if isinstance(task, dict)]
        PLUGIN_TASKS_FILE.write_text(json.dumps(fixed, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_config({"tasks": fixed})

    def _write_config(self, patch: dict[str, Any]) -> None:
        config = self._read_current_config()
        config.update(patch)
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        self.plugin.config = config

    def _reload_plugin(self) -> None:
        try:
            self.plugin.config = self.plugin._merge_config(self.plugin.config)
        except Exception:
            pass

    @staticmethod
    def _load_schema(path: Path) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            return {}
