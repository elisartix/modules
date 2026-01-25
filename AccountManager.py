# meta developer: @elisartix

import asyncio
import contextlib
import hashlib
import os
import pathlib
import time

import requests
import tempfile
from telethon import functions, types
from telethon.utils import get_display_name

from .. import loader, utils


@loader.tds
class AccountManagerMod(loader.Module):
    """Control multiple linked accounts from one place"""

    strings = {
        "name": "AccountManager",
        "no_accounts": "<b>No accounts found.</b>",
        "list_header": "<b>Linked accounts:</b>\n{rows}",
        "list_row": "{idx}. {title} — <code>{user_id}</code>{main}",
        "no_username": "no username",
        "main_tag": " (main)",
        "say_usage": (
            "<b>Usage:</b> <code>{prefix}say 1 text [count]</code>\n"
            "<i>Last argument is optional and sets how many times to repeat.</i>"
        ),
        "say_missing_text": "<b>Provide text after the account number.</b>",
        "say_not_found": "<b>Account #{num} not found.</b>",
        "say_sent": "<b>Sent from account #{num} x{count}.</b>",
        "say_not_found_selector": "<b>Account <code>{selector}</code> not found.</b>",
        "report_usage": (
            "<b>Usage:</b> <code>{prefix}report [Reason]</code> in reply to a message\n"
            "<i>Reasons: Spam, Violence, Pornography, ChildAbuse, Copyright, Other.</i>\n"
            "<i>No args = Spam and Other.</i>"
        ),
        "report_need_reply": "<b>Reply to a message you want to report.</b>",
        "report_not_in_chat": "<b>Account #{num} is not in chat and could not join.</b>",
        "report_done": "<b>Reports sent: {ok}/{total} accounts, reasons: {reasons}.</b>",
        "join_usage": (
            "<b>Usage:</b> <code>{prefix}join [account]</code> (current chat)\n"
            "<i>No args = all accounts. Account can be number, user id or username.</i>"
        ),
        "join_ok": "<b>Joined: {ok}/{total} accounts.</b>",
        "join_fail": "<b>Account {label} failed to join.</b>",
        "spam_usage": (
            "<b>Usage:</b> <code>{prefix}spamacc [account] text count [-s]</code>\n"
            "<i>Account optional (number/id/username). Without account -> all.</i>"
        ),
        "spam_bad_count": "<b>Count must be a number.</b>",
    }

    strings_ru = {
        "no_accounts": "<b>Аккаунты не найдены.</b>",
        "list_header": "<b>Подключённые аккаунты:</b>\n{rows}",
        "list_row": "{idx}. {title} — <code>{user_id}</code>{main}",
        "no_username": "без username",
        "main_tag": " (основной)",
        "say_usage": (
            "<b>Использование:</b> <code>{prefix}say 1 текст [кол-во]</code>\n"
            "<i>Последний аргумент необязателен и задаёт количество повторов.</i>"
        ),
        "say_missing_text": "<b>Укажи текст после номера аккаунта.</b>",
        "say_not_found": "<b>Аккаунт №{num} не найден.</b>",
        "say_sent": "<b>Отправил от аккаунта №{num} x{count}.</b>",
        "say_not_found_selector": "<b>Аккаунт <code>{selector}</code> не найден.</b>",
        "report_usage": (
            "<b>Использование:</b> <code>{prefix}report [Причина]</code> в ответ на сообщение\n"
            "<i>Причины: Spam, Violence, Pornography, ChildAbuse, Copyright, Other.</i>\n"
            "<i>Без аргументов = Spam и Other.</i>"
        ),
        "report_need_reply": "<b>Сделай реплай на сообщение, которое нужно пожаловаться.</b>",
        "report_not_in_chat": "<b>Аккаунт №{num} не в чате и не удалось добавить.</b>",
        "report_done": "<b>Жалобы отправлены: {ok}/{total} аккаунтов, причины: {reasons}.</b>",
        "join_usage": (
            "<b>Использование:</b> <code>{prefix}join [аккаунт]</code> (текущий чат)\n"
            "<i>Без аргументов — все аккаунты. Аккаунт: номер, user id или username.</i>"
        ),
        "join_ok": "<b>Добавлены: {ok}/{total} аккаунтов.</b>",
        "join_fail": "<b>Не удалось добавить аккаунт {label}.</b>",
        "spam_usage": (
            "<b>Использование:</b> <code>{prefix}spamacc [аккаунт] текст кол-во [-s]</code>\n"
            "<i>Аккаунт опционален (номер/id/username). Без аккаунта — все.</i>"
        ),
        "spam_bad_count": "<b>Количество должно быть числом.</b>",
    }

    def __init__(self):
        self._accounts_cache = []
        self._accounts_cache_ts = 0.0
        self._reason_map = {
            "spam": (types.InputReportReasonSpam, "Spam"),
            "violence": (types.InputReportReasonViolence, "Violence"),
            "pornography": (types.InputReportReasonPornography, "Pornography"),
            "childabuse": (types.InputReportReasonChildAbuse, "ChildAbuse"),
            "copyright": (types.InputReportReasonCopyright, "Copyright"),
            "other": (types.InputReportReasonOther, "Other"),
        }
        self._update_url = (
            "https://raw.githubusercontent.com/elisartix/modules/main/AccountManager.py"
        )
        self._update_interval = 600
        self._update_task = None
        self._update_lock = asyncio.Lock()

    def _resolve_account(self, selector, accounts):
        if not selector:
            return None

        raw = selector
        sel = selector.lower().lstrip("@")

        # Number position (1-based)
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(accounts):
                return idx - 1

        # By user id
        if raw.lstrip("-").isdigit():
            uid = int(raw)
            for i, acc in enumerate(accounts):
                if getattr(acc["user"], "id", None) == uid:
                    return i

        # By username
        for i, acc in enumerate(accounts):
            uname = getattr(acc["user"], "username", None)
            if uname and uname.lower() == sel:
                return i

        return None

    def _extract_silent(self, args):
        silent = False
        if args:
            last = args[-1].lower().rstrip(".")
            if last in {"-s", "s"}:
                args = args[:-1]
                silent = True
        return args, silent

    async def _fetch_remote(self):
        loop = asyncio.get_running_loop()
        def _do():
            r = requests.get(self._update_url, timeout=20)
            r.raise_for_status()
            tmp = tempfile.NamedTemporaryFile(delete=False)
            try:
                tmp.write(r.content)
                tmp.flush()
                return tmp.name, r.text
            finally:
                tmp.close()

        return await loop.run_in_executor(None, _do)

    async def _check_self_update(self):
        async with self._update_lock:
            tmp_path = None
            try:
                tmp_path, remote = await self._fetch_remote()
            except Exception:
                return

            local_path = pathlib.Path(__file__).resolve()
            try:
                local = local_path.read_text(encoding="utf-8")
            except Exception:
                if tmp_path:
                    with contextlib.suppress(Exception):
                        os.remove(tmp_path)
                return

            if hashlib.sha256(remote.encode()).hexdigest() == hashlib.sha256(
                local.encode()
            ).hexdigest():
                if tmp_path:
                    with contextlib.suppress(Exception):
                        os.remove(tmp_path)
                return

            try:
                local_path.write_text(remote, encoding="utf-8")
            except Exception:
                if tmp_path:
                    with contextlib.suppress(Exception):
                        os.remove(tmp_path)
                return

            if tmp_path:
                with contextlib.suppress(Exception):
                    os.remove(tmp_path)

            with contextlib.suppress(Exception):
                await self.allmodules.reload_module(self)

    async def _auto_update_loop(self):
        while True:
            await asyncio.sleep(self._update_interval)
            await self._check_self_update()

    async def accupdcmd(self, message):
        """Force update this module from remote"""
        await self._check_self_update()
        with contextlib.suppress(Exception):
            await message.delete()

    async def client_ready(self, client, db):
        self._client = client
        self._db = db
        await self._refresh_accounts(force=True)
        if not self._update_task:
            self._update_task = asyncio.create_task(self._auto_update_loop())
            asyncio.create_task(self._check_self_update())

    async def _refresh_accounts(self, force: bool = False):
        if (
            not force
            and self._accounts_cache
            and time.monotonic() - self._accounts_cache_ts < 60
        ):
            return self._accounts_cache

        clients = getattr(self, "allclients", None) or [self._client]
        accounts = []

        for c in clients:
            try:
                me = await c.get_me()
            except Exception:
                continue

            accounts.append({"client": c, "user": me})

        self._accounts_cache = accounts
        self._accounts_cache_ts = time.monotonic()
        return accounts

    def _format_title(self, user):
        name = get_display_name(user) or "Unknown"
        username = (
            f"@{user.username}"
            if getattr(user, "username", None)
            else self.strings("no_username")
        )
        return f"{utils.escape_html(name)} ({utils.escape_html(username)})"

    async def listacccmd(self, message):
        """List connected accounts with their numbers"""
        args = utils.get_args(message)
        args, silent = self._extract_silent(args)
        accounts = await self._refresh_accounts(force=True)

        if not accounts:
            if silent:
                with contextlib.suppress(Exception):
                    await message.delete()
                return await utils.answer(message, self.strings("no_accounts"))
            return await utils.answer(message, self.strings("no_accounts"))

        rows = []
        for idx, acc in enumerate(accounts, start=1):
            user = acc["user"]
            rows.append(
                self.strings("list_row").format(
                    idx=idx,
                    title=self._format_title(user),
                    user_id=user.id,
                    main=self.strings("main_tag") if acc["client"] == self._client else "",
                )
            )

        if silent:
            with contextlib.suppress(Exception):
                await message.delete()
            return

        return await utils.answer(
            message,
            self.strings("list_header").format(rows="\n".join(rows)),
        )

    async def saycmd(self, message):
        """{account} text [count] - send a message from selected account (number/id/username)"""
        args = utils.get_args(message)
        args, silent = self._extract_silent(args)
        if len(args) < 2:
            return await utils.answer(
                message,
                self.strings("say_usage").format(prefix=self.get_prefix()),
            )

        acc_selector = args[0]
        text_parts = args[1:]
        repeat = 1

        if len(text_parts) > 1 and text_parts[-1].isdigit():
            repeat = max(1, int(text_parts[-1]))
            text_parts = text_parts[:-1]

        text = " ".join(text_parts).strip()
        if not text:
            return await utils.answer(message, self.strings("say_missing_text"))

        accounts = await self._refresh_accounts()
        idx = self._resolve_account(acc_selector, accounts)
        if idx is None:
            return await utils.answer(
                message,
                self.strings("say_not_found_selector").format(selector=acc_selector),
            )

        target_client = accounts[idx]["client"]
        reply = await message.get_reply_message()
        reply_to = reply.id if reply else None

        for _ in range(repeat):
            await target_client.send_message(
                message.chat_id,
                text,
                reply_to=reply_to,
            )
            if repeat > 3:
                await asyncio.sleep(0.35)

        return await utils.answer(
            message,
            self.strings("say_sent").format(
                num=idx + 1,
                count=repeat,
            ),
        ) if not silent else await message.delete()

    async def _export_invite_link(self, chat):
        if not self._client:
            return None
        try:
            peer = await self._client.get_input_entity(chat)
            res = await self._client(
                functions.messages.ExportChatInviteRequest(peer=peer)
            )
            return getattr(res, "link", None)
        except Exception:
            return None

    async def _join_with_link(self, client, link: str):
        if not link:
            return False

        try:
            if "joinchat" in link or "+" in link:
                join_hash = link.rsplit("/", 1)[-1].replace("+", "")
                await client(functions.messages.ImportChatInviteRequest(hash=join_hash))
            else:
                slug = link.rsplit("/", 1)[-1]
                await client(functions.channels.JoinChannelRequest(channel=slug))
            return True
        except Exception:
            return False

    async def _ensure_in_chat(self, client, chat, user_entity):
        try:
            await client.get_permissions(chat, "me")
            return True
        except Exception:
            pass

        # Try joining by public username if available
        username = getattr(chat, "username", None)
        if username:
            try:
                await client(functions.channels.JoinChannelRequest(channel=username))
                return True
            except Exception:
                pass

        # Try joining via exported invite link using main account
        invite_link = await self._export_invite_link(chat)
        if invite_link:
            joined = await self._join_with_link(client, invite_link)
            if joined:
                try:
                    await client.get_permissions(chat, "me")
                    return True
                except Exception:
                    pass

        # Try inviting with the main client if it has rights
        if self._client and self._client != client:
            try:
                await self._client.get_permissions(chat, "me")
                try:
                    await self._client(
                        functions.channels.InviteToChannelRequest(
                            channel=chat,
                            users=[user_entity],
                        )
                    )
                    await client.get_permissions(chat, "me")
                    return True
                except Exception:
                    pass
            except Exception:
                pass

        return False

    def _parse_reasons(self, args):
        if not args:
            return [
                (types.InputReportReasonSpam, "Spam"),
                (types.InputReportReasonOther, "Other"),
            ]

        reason_key = args[0].lower()
        mapped = self._reason_map.get(reason_key)
        if mapped:
            return [mapped]

        return [
            (types.InputReportReasonSpam, "Spam"),
            (types.InputReportReasonOther, "Other"),
        ]

    async def reportcmd(self, message):
        """[Reason] (reply) - send report from all accounts on the replied message"""
        reply = await message.get_reply_message()
        if not reply:
            return await utils.answer(message, self.strings("report_need_reply"))

        args = utils.get_args(message)
        args, silent = self._extract_silent(args)
        reasons = self._parse_reasons(args)

        chat = await message.get_chat()
        accounts = await self._refresh_accounts()
        success = 0

        for idx, acc in enumerate(accounts, start=1):
            c = acc["client"]
            user_entity = acc["user"]

            in_chat = await self._ensure_in_chat(c, chat, user_entity)
            if not in_chat:
                await utils.answer(
                    message,
                    self.strings("report_not_in_chat").format(num=idx),
                )
                continue

            try:
                peer = await c.get_input_entity(chat)
            except Exception:
                await utils.answer(
                    message,
                    self.strings("report_not_in_chat").format(num=idx),
                )
                continue

            for reason_cls, reason_name in reasons:
                try:
                    await c(
                        functions.messages.ReportRequest(
                            peer=peer,
                            id=[reply.id],
                            reason=reason_cls(),
                            message=reason_name,
                        )
                    )
                except Exception:
                    pass

            success += 1
            if len(accounts) > 2:
                await asyncio.sleep(0.25)

        reasons_label = ", ".join({name for _, name in reasons})
        if silent:
            with contextlib.suppress(Exception):
                await message.delete()
            return

        return await utils.answer(
            message,
            self.strings("report_done").format(
                ok=success, total=len(accounts), reasons=reasons_label
            ),
        )

    async def joincmd(self, message):
        """[account] - join the current chat with selected or all accounts"""
        args = utils.get_args(message)
        args, silent = self._extract_silent(args)
        accounts = await self._refresh_accounts()
        if not accounts:
            return await utils.answer(message, self.strings("no_accounts"))

        chat = await message.get_chat()

        selected_accounts = accounts
        label_map = {i: str(i + 1) for i in range(len(accounts))}

        if args:
            idx = self._resolve_account(args[0], accounts)
            if idx is None:
                return await utils.answer(
                    message,
                    self.strings("say_not_found_selector").format(selector=args[0]),
                )
            selected_accounts = [accounts[idx]]
            label_map = {0: args[0]}

        ok = 0
        for i, acc in enumerate(selected_accounts):
            joined = await self._ensure_in_chat(acc["client"], chat, acc["user"])
            if joined:
                ok += 1
            else:
                if not silent:
                    await utils.answer(
                        message,
                        self.strings("join_fail").format(
                            label=label_map.get(i, str(i + 1))
                        ),
                    )
            if len(selected_accounts) > 2:
                await asyncio.sleep(0.2)

        if silent:
            with contextlib.suppress(Exception):
                await message.delete()
            return

        return await utils.answer(
            message,
            self.strings("join_ok").format(ok=ok, total=len(selected_accounts)),
        )

    async def spamacccmd(self, message):
        """[account] text count [-s] - spam from all or selected account"""
        args = utils.get_args(message)
        args, silent = self._extract_silent(args)

        if len(args) < 2:
            return await utils.answer(
                message, self.strings("spam_usage").format(prefix=self.get_prefix())
            )

        accounts = await self._refresh_accounts()
        if not accounts:
            return await utils.answer(message, self.strings("no_accounts"))

        # Determine count (last arg)
        if not args[-1].isdigit():
            return await utils.answer(message, self.strings("spam_bad_count"))
        count = max(1, int(args[-1]))

        acc_idx = None
        text_parts = args[:-1]

        if len(text_parts) >= 2:
            maybe_acc = text_parts[0]
            idx = self._resolve_account(maybe_acc, accounts)
            if idx is not None:
                acc_idx = idx
                text_parts = text_parts[1:]

        if not text_parts:
            return await utils.answer(
                message, self.strings("spam_usage").format(prefix=self.get_prefix())
            )

        text = " ".join(text_parts)
        reply = await message.get_reply_message()
        reply_to = reply.id if reply else None

        target_accounts = (
            [accounts[acc_idx]] if acc_idx is not None else accounts
        )

        # Interleave across accounts: 1,2,3,1,2,... total messages == count
        for i in range(count):
            acc = target_accounts[i % len(target_accounts)]
            try:
                await acc["client"].send_message(
                    message.chat_id,
                    text,
                    reply_to=reply_to,
                )
            except Exception:
                pass
            if len(target_accounts) > 1:
                await asyncio.sleep(0.15)
            if count > 3 and i % len(target_accounts) == len(target_accounts) - 1:
                await asyncio.sleep(0.1)

        if silent:
            with contextlib.suppress(Exception):
                await message.delete()
            return

        sent_from = (
            self._format_title(accounts[acc_idx]["user"])
            if acc_idx is not None
            else "all"
        )
        return await utils.answer(
            message,
            f"<b>Spam sent from {sent_from} x{count}.</b>",
        )
