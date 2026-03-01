# meta developer: @elisartix

import asyncio
import contextlib
import logging
from datetime import datetime

from .. import loader, utils

logger = logging.getLogger(__name__)


@loader.tds
class RouletteDailyMod(loader.Module):
    """Daily auto messages to bot 6197735411 (2 times per day)."""

    strings = {
        "name": "RouletteDaily",
        "invalid_time": "❌ Неверный формат времени. Используй `HH:MM,HH:MM`",
        "times_saved": "✅ Новое расписание: <code>{}</code>",
        "status": (
            "<b>RouletteDaily</b>\n"
            "Вкл: <code>{enabled}</code>\n"
            "Бот: <code>{bot}</code>\n"
            "Время: <code>{times}</code>\n"
            "Последний запуск: <code>{last}</code>"
        ),
        "enabled": "✅ Авто-отправка включена",
        "disabled": "🛑 Авто-отправка выключена",
        "sent": "✅ Сообщения отправлены",
        "send_error": "❌ Ошибка отправки: <code>{}</code>",
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "bot_id",
                6197735411,
                "ID бота, куда отправлять сообщения.",
                validator=loader.validators.Integer(minimum=1),
            ),
            loader.ConfigValue(
                "times",
                "10:00,22:00",
                "Время отправки каждый день: HH:MM,HH:MM",
                validator=loader.validators.String(),
            ),
        )
        self._task = None

    @staticmethod
    def _parse_times(raw: str):
        result = []
        for token in raw.split(","):
            token = token.strip()
            if not token:
                continue
            parts = token.split(":")
            if len(parts) != 2:
                raise ValueError(token)
            hour = int(parts[0])
            minute = int(parts[1])
            if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                raise ValueError(token)
            result.append((hour, minute))
        if not result:
            raise ValueError("empty")
        return sorted(set(result))

    async def client_ready(self, client, db):
        self._client = client
        self._db = db

        if self.get("enabled", None) is None:
            self.set("enabled", True)
        if self.get("last_mark", None) is None:
            self.set("last_mark", "")

        if not self._task or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def on_unload(self):
        if self._task:
            self._task.cancel()
            with contextlib.suppress(Exception):
                await self._task

    async def _send_pair(self):
        bot_id = int(self.config["bot_id"])
        await self._client.send_message(bot_id, "Рулетка на примогемы")
        await asyncio.sleep(1.5)
        await self._client.send_message(bot_id, "Рулетка на луну")

    async def _loop(self):
        while True:
            try:
                if self.get("enabled", True):
                    now = datetime.now()
                    schedule = self._parse_times(self.config["times"])
                    current = (now.hour, now.minute)
                    mark = now.strftime("%Y-%m-%d %H:%M")
                    if current in schedule and self.get("last_mark", "") != mark:
                        await self._send_pair()
                        self.set("last_mark", mark)
                        logger.info("RouletteDaily: sent for mark %s", mark)
            except Exception:
                logger.exception("RouletteDaily loop error")

            await asyncio.sleep(20)

    async def rstatuscmd(self, message):
        """Показать статус авто-отправки."""
        text = self.strings("status").format(
            enabled=self.get("enabled", True),
            bot=self.config["bot_id"],
            times=self.config["times"],
            last=self.get("last_mark", "never") or "never",
        )
        await utils.answer(message, text)

    async def roncmd(self, message):
        """Включить авто-отправку."""
        self.set("enabled", True)
        await utils.answer(message, self.strings("enabled"))

    async def roffcmd(self, message):
        """Выключить авто-отправку."""
        self.set("enabled", False)
        await utils.answer(message, self.strings("disabled"))

    async def rtimescmd(self, message):
        """Задать время отправки: .rtimes 10:00,22:00"""
        raw = utils.get_args_raw(message).strip()
        if not raw:
            return await utils.answer(message, self.strings("invalid_time"))

        try:
            parsed = self._parse_times(raw)
        except Exception:
            return await utils.answer(message, self.strings("invalid_time"))

        normalized = ",".join(f"{h:02d}:{m:02d}" for h, m in parsed)
        self.config["times"] = normalized
        await utils.answer(message, self.strings("times_saved").format(normalized))

    async def rsendcmd(self, message):
        """Отправить 2 сообщения прямо сейчас."""
        try:
            await self._send_pair()
            now_mark = datetime.now().strftime("%Y-%m-%d %H:%M")
            self.set("last_mark", now_mark)
            await utils.answer(message, self.strings("sent"))
        except Exception as e:
            await utils.answer(message, self.strings("send_error").format(utils.escape_html(str(e))))
