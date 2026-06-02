from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from astrbot.api import logger
from astrbot.api.star import Context

try:
    from quart import jsonify as quart_jsonify
    from quart import request as quart_request_obj
except ImportError:
    quart_jsonify = None
    quart_request_obj = None

from .page_service import WebpageScreenshotPageService

PLUGIN_NAME = "astrbot_plugin_webpage_screenshot"


class WebpageScreenshotWebController:
    """网页截图配置页 Web API。"""

    def __init__(self, context: Context, plugin: Any):
        self.context = context
        self.service = WebpageScreenshotPageService(plugin)

    def register_routes(self) -> None:
        routes = [
            ("/ping", self.page_ping, ["GET"], "Webpage screenshot page ping"),
            ("/settings/bootstrap", self.page_bootstrap, ["GET"], "Load screenshot settings"),
            ("/settings/groups/refresh", self.page_refresh_groups, ["POST"], "Refresh QQ group list"),
            ("/settings/group", self.page_get_group, ["GET"], "Load group screenshot config"),
            ("/settings/group", self.page_update_group, ["POST"], "Update group screenshot config"),
            ("/settings/group/reset", self.page_reset_group, ["POST"], "Reset group screenshot config"),
        ]
        for path, handler, methods, desc in routes:
            self.context.register_web_api(f"/{PLUGIN_NAME}{path}", self._wrap_handler(handler), methods, desc)

    @staticmethod
    def _check_quart_available() -> None:
        if quart_jsonify is None or quart_request_obj is None:
            raise RuntimeError("Web framework is unavailable")

    @staticmethod
    def _jsonify(payload: dict[str, Any]):
        WebpageScreenshotWebController._check_quart_available()
        return cast(Callable[[dict[str, Any]], Any], quart_jsonify)(payload)

    @staticmethod
    def _request():
        WebpageScreenshotWebController._check_quart_available()
        return cast(Any, quart_request_obj)

    def _wrap_handler(self, handler: Callable[[], Awaitable]) -> Callable[[], Awaitable]:
        async def wrapped():
            self._check_quart_available()
            try:
                return await handler()
            except ValueError as exc:
                return self._jsonify({"ok": False, "message": str(exc)}), 400
            except Exception as exc:
                logger.exception("[WebpageScreenshot] page request failed")
                return self._jsonify({"ok": False, "message": str(exc)}), 500
        wrapped.__name__ = handler.__name__
        return wrapped

    async def page_ping(self):
        return self._jsonify({"ok": True, "message": "pong"})

    async def page_bootstrap(self):
        return self._jsonify({"ok": True, "data": self.service.get_bootstrap_payload()})

    async def page_refresh_groups(self):
        groups = await self.service.list_groups(force=True)
        return self._jsonify({"ok": True, "data": groups})

    async def page_get_group(self):
        group_id = self._request().args.get("group_id", "")
        return self._jsonify({"ok": True, "data": self.service.get_group_config(group_id)})

    async def page_update_group(self):
        payload = await self._request().get_json(force=True, silent=True) or {}
        result = self.service.update_group_config(payload.get("group_id", ""), payload.get("config", {}))
        return self._jsonify({"ok": True, "message": "群截图配置已保存", "data": result})

    async def page_reset_group(self):
        payload = await self._request().get_json(force=True, silent=True) or {}
        result = self.service.reset_group_config(payload.get("group_id", ""))
        return self._jsonify({"ok": True, "message": "群截图配置已重置", "data": result})
