# ---------------------------------------------------------------------------------
# Name: Role
# Description: Manage custom admin titles without giving Telegram rights.
# Author: @ElisArt
# Commands: role, unrole, g, gapi, gsys
# ---------------------------------------------------------------------------------

import asyncio
import inspect
import logging

import requests
from telethon.tl import types
from telethon.tl.functions import channels

from .. import loader, utils

logger = logging.getLogger("Role")

GEN_API_URL = "https://api.gen-api.ru/api/v1/networks/deepseek-chat"
DEFAULT_SYSTEM_PROMPT = (
    "отвечай коротко и ясно, не используй форматирование, специальные символы, **, ### , ."
)


def _empty_admin_rights(force_other: bool = True) -> types.ChatAdminRights:
    sig = inspect.signature(types.ChatAdminRights)
    kwargs = {
        name: False
        for name in sig.parameters.keys()
        if name not in {"self", "_"}
    }
    rights = types.ChatAdminRights(**kwargs)

    if force_other and hasattr(rights, "other"):
        rights.other = True

    return rights


@loader.tds
class RoleMod(loader.Module):
    strings = {
        "name": "Role",
        "no_reply": "<b>Нужен reply на пользователя.</b>",
        "no_title": "<b>Укажи название роли: <code>{prefix}role Название</code></b>",
        "not_group": "<b>Команда работает только в группах/мегагруппах.</b>",
        "no_rights": "<b>Нет прав выдавать админку (нужно право <code>add_admins</code>).</b>",
        "done": "<b>Выдал(а) роль <code>{title}</code> пользователю {user}.</b>",
        "removed": "<b>Снял(а) роль с пользователя {user}.</b>",
        "error": "<b>Ошибка: <code>{error}</code></b>",
        "api_doc": "Gen-API ключ для команды .g",
        "api_saved": "<b>Gen-API ключ сохранён.</b>",
        "api_cleared": "<b>Gen-API ключ удалён.</b>",
        "api_show": "<b>Текущий Gen-API ключ:</b> <code>{key}</code>",
        "api_not_set": "<b>Gen-API ключ ещё не задан.</b>",
        "no_api_key": "<b>Сначала сохрани ключ: <code>{prefix}gapi API_KEY</code></b>",
        "no_query": "<b>Укажи текст запроса: <code>{prefix}g Текст</code></b>",
        "g_empty": "<b>API вернул пустой ответ.</b>",
        "g_failed": "<b>Не удалось получить ответ: <code>{error}</code></b>",
        "g_result": "<b>Gen-API:</b>\n{response}",
        "system_doc": "Системный промпт для Gen-API (.g)",
        "system_saved": "<b>Системный промпт обновлён.</b>",
        "system_reset": "<b>Системный промпт сброшен.</b>",
        "system_show": "<b>Текущий системный промпт:</b> {prompt}",
        "role_list_title": "<b>Админы и их титулы:</b>\n{rows}",
        "role_list_item": "• {name} — <code>{title}</code>",
        "role_list_empty": "<b>В чате нет админов.</b>",
        "not_admin": "<b>Пользователь не является админом.</b>",
        "role_reset": "<b>Сбросил титул у {user}.</b>",
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "gen_api_key",
                None,
                lambda: self.strings["api_doc"],
            ),
            loader.ConfigValue(
                "gen_system_prompt",
                DEFAULT_SYSTEM_PROMPT,
                lambda: self.strings["system_doc"],
            ),
        )

    @staticmethod
    def _mask_key(key: str) -> str:
        if not key:
            return ""
        if len(key) <= 8:
            return key
        return f"{key[:4]}…{key[-4:]}"

    async def client_ready(self, client, db):
        self._client = client
        self._db = db

    async def _get_target_admin_rights(self, chat, user) -> types.ChatAdminRights:
        try:
            participant = await self._client(
                channels.GetParticipantRequest(chat, user)
            )
        except Exception:
            logger.debug(
                "Falling back to minimal admin rights (participant lookup failed)",
                exc_info=True,
            )
            return _empty_admin_rights()

        participant = getattr(participant, "participant", None)

        if isinstance(
            participant,
            (types.ChannelParticipantAdmin, types.ChannelParticipantCreator),
        ):
            rights = getattr(participant, "admin_rights", None)
            if isinstance(rights, types.ChatAdminRights):
                return rights

        return _empty_admin_rights()

    def _perform_gen_api_request(
        self, prompt: str, api_key: str, system_prompt: str
    ) -> str:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        payload = {
            "is_sync": True,
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        }

        response = requests.post(
            GEN_API_URL,
            json=payload,
            headers=headers,
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()

        try:
            reply_text = data["response"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            reply_text = None

        if not reply_text:
            raise ValueError("empty_response")

        return reply_text

    async def _call_gen_api(self, prompt: str, api_key: str, system_prompt: str) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._perform_gen_api_request(prompt, api_key, system_prompt),
        )

    async def rolecmd(self, message):
        """[reply] Title - give admin without rights with custom rank"""
        reply = await message.get_reply_message()
        if not reply:
            return await utils.answer(message, self.strings["no_reply"])

        title = (utils.get_args_raw(message) or "").strip()
        if not title:
            return await utils.answer(
                message,
                self.strings["no_title"].format(prefix=self.get_prefix()),
            )

        chat = await message.get_chat()
        if not isinstance(chat, types.Channel):
            return await utils.answer(message, self.strings["not_group"])

        if not getattr(chat, "creator", False) and not (
            getattr(chat, "admin_rights", None)
            and getattr(chat.admin_rights, "add_admins", False)
        ):
            return await utils.answer(message, self.strings["no_rights"])

        user = await reply.get_sender()

        safe_title = title[:16]
        admin_rights = await self._get_target_admin_rights(chat, user)

        try:
            await self._client(
                channels.EditAdminRequest(
                    chat,
                    user,
                    admin_rights,
                    safe_title,
                )
            )
        except Exception as e:
            logger.exception("Failed to set role")
            return await utils.answer(message, self.strings["error"].format(error=str(e)))

        return await utils.answer(
            message,
            self.strings["done"].format(
                title=utils.escape_html(safe_title),
                user=(
                    utils.escape_html(user.first_name)
                    if getattr(user, "first_name", None)
                    else "<code>unknown</code>"
                ),
            ),
        )

    async def unrolecmd(self, message):
        """[reply] - remove previously issued admin title/rights"""
        reply = await message.get_reply_message()
        if not reply:
            return await utils.answer(message, self.strings["no_reply"])

        chat = await message.get_chat()
        if not isinstance(chat, types.Channel):
            return await utils.answer(message, self.strings["not_group"])

        if not getattr(chat, "creator", False) and not (
            getattr(chat, "admin_rights", None)
            and getattr(chat.admin_rights, "add_admins", False)
        ):
            return await utils.answer(message, self.strings["no_rights"])

        user = await reply.get_sender()

        try:
            await self._client(
                channels.EditAdminRequest(
                    chat,
                    user,
                    _empty_admin_rights(force_other=False),
                    "",
                )
            )
        except Exception as e:
            logger.exception("Failed to remove role")
            return await utils.answer(
                message, self.strings["error"].format(error=str(e))
            )

        return await utils.answer(
            message,
            self.strings["removed"].format(
                user=(
                    utils.escape_html(user.first_name)
                    if getattr(user, "first_name", None)
                    else "<code>unknown</code>"
                ),
            ),
        )

    async def gapicmd(self, message):
        """[api] - save/show Gen-API token for .g"""
        arg = (utils.get_args_raw(message) or "").strip()
        if not arg:
            key = self.config["gen_api_key"]
            if key:
                return await utils.answer(
                    message,
                    self.strings["api_show"].format(key=self._mask_key(key)),
                )
            return await utils.answer(message, self.strings["api_not_set"])

        if arg.lower() in {"clear", "reset", "none"}:
            self.config["gen_api_key"] = None
            return await utils.answer(message, self.strings["api_cleared"])

        self.config["gen_api_key"] = arg
        return await utils.answer(message, self.strings["api_saved"])

    async def gsyscmd(self, message):
        """[text] - save/show system prompt for Gen-API"""
        arg = (utils.get_args_raw(message) or "").strip()
        if not arg:
            prompt = self.config["gen_system_prompt"] or DEFAULT_SYSTEM_PROMPT
            return await utils.answer(
                message,
                self.strings["system_show"].format(
                    prompt=utils.escape_html(prompt),
                ),
            )

        if arg.lower() in {"reset", "default"}:
            self.config["gen_system_prompt"] = DEFAULT_SYSTEM_PROMPT
            return await utils.answer(message, self.strings["system_reset"])

        self.config["gen_system_prompt"] = arg
        return await utils.answer(message, self.strings["system_saved"])

    async def gcmd(self, message):
        """text - ask Gen-API"""
        prompt = (utils.get_args_raw(message) or "").strip()

        if not prompt:
            reply = await message.get_reply_message()
            if reply and getattr(reply, "raw_text", None):
                prompt = reply.raw_text.strip()

        if not prompt:
            return await utils.answer(
                message,
                self.strings["no_query"].format(prefix=self.get_prefix()),
            )

        api_key = self.config["gen_api_key"]
        if not api_key:
            return await utils.answer(
                message,
                self.strings["no_api_key"].format(prefix=self.get_prefix()),
            )

        system_prompt = self.config["gen_system_prompt"] or DEFAULT_SYSTEM_PROMPT

        try:
            reply_text = await self._call_gen_api(prompt, api_key, system_prompt)
        except ValueError:
            return await utils.answer(message, self.strings["g_empty"])
        except Exception as e:
            logger.exception("Failed to query Gen-API")
            return await utils.answer(
                message,
                self.strings["g_failed"].format(
                    error=utils.escape_html(str(e)),
                ),
            )

        safe_reply = utils.escape_html(reply_text.strip())
        if not safe_reply:
            return await utils.answer(message, self.strings["g_empty"])

        return await utils.answer(
            message,
            self.strings["g_result"].format(response=safe_reply),
        )