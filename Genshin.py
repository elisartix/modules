# meta developer: @elisartix
# requires: aiohttp pillow

from __future__ import annotations

import asyncio
import io
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from typing import Any

import aiohttp
from PIL import Image
from PIL import ImageDraw
from PIL import ImageFilter
from PIL import ImageFont
from telethon.tl.types import Message

from .. import loader, utils

logger = logging.getLogger(__name__)

UID_RE = re.compile(r"^\d{9,10}$")
DB_UID_MAP_KEY = "saved_uids_v1"
DB_DEFAULT_UID_KEY = "default_uid_v1"
ELEMENT_LABELS = {
    "Fire": "Пиро",
    "Electric": "Электро",
    "Water": "Гидро",
    "Grass": "Дендро",
    "Wind": "Анемо",
    "Rock": "Гео",
    "Ice": "Крио",
}

ENKA_BASE_URL = "https://enka.network/api/uid"
CHAR_META_URL = "https://raw.githubusercontent.com/EnkaNetwork/API-docs/master/store/characters.json"
LOC_URL = "https://raw.githubusercontent.com/EnkaNetwork/API-docs/master/store/loc.json"
LOCAL_CHAR_PATH = Path("API-docs-master/store/characters.json")
LOCAL_LOC_PATH = Path("API-docs-master/store/loc.json")

ASC_TO_MAX_LEVEL = {0: 20, 1: 40, 2: 50, 3: 60, 4: 70, 5: 80, 6: 90}
SLOT_ORDER = {
    "EQUIP_BRACER": 0,
    "EQUIP_NECKLACE": 1,
    "EQUIP_SHOES": 2,
    "EQUIP_RING": 3,
    "EQUIP_DRESS": 4,
}
SLOT_LABEL = {
    "EQUIP_BRACER": "Цветок",
    "EQUIP_NECKLACE": "Перо",
    "EQUIP_SHOES": "Пески",
    "EQUIP_RING": "Кубок",
    "EQUIP_DRESS": "Корона",
}

FIGHT_LABELS = {
    "FIGHT_PROP_MAX_HP": "Макс. HP",
    "FIGHT_PROP_HP": "HP",
    "FIGHT_PROP_BASE_HP": "HP",
    "FIGHT_PROP_CUR_ATTACK": "Сила атаки",
    "FIGHT_PROP_ATTACK": "АТК",
    "FIGHT_PROP_BASE_ATTACK": "АТК",
    "FIGHT_PROP_BASE_ATK": "АТК",
    "FIGHT_PROP_CUR_DEFENSE": "Защита",
    "FIGHT_PROP_DEFENSE": "Защита",
    "FIGHT_PROP_BASE_DEFENSE": "Защита",
    "FIGHT_PROP_ELEMENT_MASTERY": "Мастерство стихий",
    "FIGHT_PROP_CRITICAL": "Ш.К.",
    "FIGHT_PROP_CRITICAL_HURT": "Крит. урон",
    "FIGHT_PROP_CHARGE_EFFICIENCY": "Восст. энергии",
    "FIGHT_PROP_PHYSICAL_ADD_HURT": "Физ. бонус урона",
    "FIGHT_PROP_FIRE_ADD_HURT": "Пиро бонус урона",
    "FIGHT_PROP_ELEC_ADD_HURT": "Электро бонус урона",
    "FIGHT_PROP_WATER_ADD_HURT": "Гидро бонус урона",
    "FIGHT_PROP_GRASS_ADD_HURT": "Дендро бонус урона",
    "FIGHT_PROP_WIND_ADD_HURT": "Анемо бонус урона",
    "FIGHT_PROP_ROCK_ADD_HURT": "Гео бонус урона",
    "FIGHT_PROP_ICE_ADD_HURT": "Крио бонус урона",
    "FIGHT_PROP_ATTACK_PERCENT": "Сила атаки %",
    "FIGHT_PROP_HP_PERCENT": "HP %",
    "FIGHT_PROP_DEFENSE_PERCENT": "Защита %",
    "FIGHT_PROP_HEAL_ADD": "Бонус лечения",
}
PERCENT_APPEND_PROPS = {
    "FIGHT_PROP_HP_PERCENT",
    "FIGHT_PROP_ATTACK_PERCENT",
    "FIGHT_PROP_DEFENSE_PERCENT",
    "FIGHT_PROP_CRITICAL",
    "FIGHT_PROP_CRITICAL_HURT",
    "FIGHT_PROP_CHARGE_EFFICIENCY",
    "FIGHT_PROP_HEAL_ADD",
    "FIGHT_PROP_PHYSICAL_ADD_HURT",
    "FIGHT_PROP_FIRE_ADD_HURT",
    "FIGHT_PROP_ELEC_ADD_HURT",
    "FIGHT_PROP_WATER_ADD_HURT",
    "FIGHT_PROP_GRASS_ADD_HURT",
    "FIGHT_PROP_WIND_ADD_HURT",
    "FIGHT_PROP_ROCK_ADD_HURT",
    "FIGHT_PROP_ICE_ADD_HURT",
}
ELEMENT_TO_BONUS = {
    "Fire": 40,
    "Electric": 41,
    "Water": 42,
    "Grass": 43,
    "Wind": 44,
    "Rock": 45,
    "Ice": 46,
}


class EnkaFetchError(Exception):
    pass


@dataclass(frozen=True)
class StatLine:
    label: str
    value: str


@dataclass(frozen=True)
class ArtifactData:
    slot: str
    name: str
    icon_url: str
    rarity: int
    level: int
    main_stat: StatLine
    sub_stats: tuple[StatLine, ...]


@dataclass(frozen=True)
class WeaponData:
    name: str
    icon_url: str
    rarity: int
    level: int
    refinement: int
    stat_lines: tuple[StatLine, ...]


@dataclass(frozen=True)
class CharacterOption:
    avatar_id: int
    name: str
    level: int
    constellation: int
    element: str


@dataclass(frozen=True)
class PlayerData:
    nickname: str
    uid: str
    adventure_rank: int
    world_level: int
    achievements: int
    abyss_floor: int
    abyss_level: int
    avatar_icon_url: str


@dataclass(frozen=True)
class EnkaProfile:
    uid: str
    player: PlayerData
    options: tuple[CharacterOption, ...]
    avatars_by_id: dict[int, dict[str, Any]]


@dataclass(frozen=True)
class CharacterCardData:
    player: PlayerData
    avatar_id: int
    name: str
    level: int
    max_level: int
    constellation: int
    friendship: int
    element: str
    avatar_icon_url: str
    splash_url: str
    stats: tuple[StatLine, ...]
    skills: tuple[StatLine, ...]
    weapon: WeaponData | None
    artifacts: tuple[ArtifactData, ...]


_CHAR_META: dict[str, Any] | None = None
_LOC_RU: dict[str, str] | None = None


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _enka_ui_url(icon: str) -> str:
    raw = str(icon or "").strip()
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    if raw.startswith("/ui/"):
        raw = raw[4:]
    if raw.endswith(".png"):
        raw = raw[:-4]
    return f"https://enka.network/ui/{raw}.png"


def _slug_from_side_icon(side_icon_name: str) -> str:
    icon = str(side_icon_name or "").strip()
    if icon.startswith("/ui/"):
        icon = icon[4:]
    if icon.endswith(".png"):
        icon = icon[:-4]
    return icon.replace("UI_AvatarIcon_Side_", "", 1)


def _format_stat_value(prop: str, value: float) -> str:
    if prop in PERCENT_APPEND_PROPS:
        return f"{value:.1f}%"
    return f"{round(value):,}".replace(",", " ")


def _format_fight_value(prop_id: int, value: float) -> str:
    if prop_id in {20, 22, 23, 30, 40, 41, 42, 43, 44, 45, 46}:
        return f"{value * 100:.1f}%"
    return f"{round(value):,}".replace(",", " ")


async def _fetch_json(session: aiohttp.ClientSession, url: str) -> Any:
    async with session.get(url) as resp:
        if resp.status != 200:
            raise EnkaFetchError(f"Не удалось загрузить метаданные: {url} (status={resp.status})")
        return await resp.json(content_type=None)


async def _load_meta(session: aiohttp.ClientSession) -> tuple[dict[str, Any], dict[str, str]]:
    global _CHAR_META, _LOC_RU
    if _CHAR_META is not None and _LOC_RU is not None:
        return _CHAR_META, _LOC_RU

    if LOCAL_CHAR_PATH.exists() and LOCAL_LOC_PATH.exists():
        try:
            import json

            _CHAR_META = json.loads(LOCAL_CHAR_PATH.read_text(encoding="utf-8"))
            raw_loc = json.loads(LOCAL_LOC_PATH.read_text(encoding="utf-8"))
            _LOC_RU = (raw_loc.get("ru") or {}) if isinstance(raw_loc, dict) else {}
            return _CHAR_META or {}, _LOC_RU or {}
        except Exception:
            pass

    chars, loc = await asyncio.gather(
        _fetch_json(session, CHAR_META_URL),
        _fetch_json(session, LOC_URL),
    )
    _CHAR_META = chars if isinstance(chars, dict) else {}
    _LOC_RU = (loc.get("ru") or {}) if isinstance(loc, dict) else {}
    return _CHAR_META, _LOC_RU


def _avatar_urls(char_meta: dict[str, Any], avatar_id: int) -> tuple[str, str]:
    meta = char_meta.get(str(avatar_id), {}) if isinstance(char_meta, dict) else {}
    side_icon = str(meta.get("SideIconName") or "")
    slug = _slug_from_side_icon(side_icon)
    if not slug:
        fallback = "https://enka.network/ui/UI_AvatarIcon_PlayerGirl.png"
        return fallback, fallback
    return _enka_ui_url(f"UI_AvatarIcon_{slug}"), _enka_ui_url(f"UI_Gacha_AvatarImg_{slug}")


def _name_by_id(char_meta: dict[str, Any], ru_loc: dict[str, str], avatar_id: int) -> str:
    meta = char_meta.get(str(avatar_id), {}) if isinstance(char_meta, dict) else {}
    name_hash = meta.get("NameTextMapHash")
    if name_hash is None:
        return str(avatar_id)
    return str(ru_loc.get(str(name_hash)) or avatar_id)


def _get_level(prop_map: dict[str, Any]) -> int:
    level_obj = prop_map.get("4001") or {}
    return _to_int(level_obj.get("val") or level_obj.get("ival") or 1, 1)


def _get_max_level(prop_map: dict[str, Any], level: int) -> int:
    asc_obj = prop_map.get("1002") or {}
    asc = _to_int(asc_obj.get("val") or asc_obj.get("ival"), 0)
    return max(ASC_TO_MAX_LEVEL.get(asc, 90), level)


def _build_stats(avatar: dict[str, Any], element: str) -> tuple[StatLine, ...]:
    fight = avatar.get("fightPropMap") or {}
    bonus_id = ELEMENT_TO_BONUS.get(element, 30)
    stat_order = [
        (2000, FIGHT_LABELS["FIGHT_PROP_MAX_HP"]),
        (2001, FIGHT_LABELS["FIGHT_PROP_CUR_ATTACK"]),
        (2002, FIGHT_LABELS["FIGHT_PROP_CUR_DEFENSE"]),
        (28, FIGHT_LABELS["FIGHT_PROP_ELEMENT_MASTERY"]),
        (20, FIGHT_LABELS["FIGHT_PROP_CRITICAL"]),
        (22, FIGHT_LABELS["FIGHT_PROP_CRITICAL_HURT"]),
        (23, FIGHT_LABELS["FIGHT_PROP_CHARGE_EFFICIENCY"]),
    ]
    bonus_prop = {
        30: "FIGHT_PROP_PHYSICAL_ADD_HURT",
        40: "FIGHT_PROP_FIRE_ADD_HURT",
        41: "FIGHT_PROP_ELEC_ADD_HURT",
        42: "FIGHT_PROP_WATER_ADD_HURT",
        43: "FIGHT_PROP_GRASS_ADD_HURT",
        44: "FIGHT_PROP_WIND_ADD_HURT",
        45: "FIGHT_PROP_ROCK_ADD_HURT",
        46: "FIGHT_PROP_ICE_ADD_HURT",
    }.get(bonus_id, "FIGHT_PROP_PHYSICAL_ADD_HURT")
    stat_order.append((bonus_id, FIGHT_LABELS.get(bonus_prop, "Бонус урона")))

    return tuple(
        StatLine(label=label, value=_format_fight_value(prop_id, _to_float(fight.get(str(prop_id)))))
        for prop_id, label in stat_order
    )


def _build_skills(avatar: dict[str, Any], avatar_id: int, char_meta: dict[str, Any]) -> tuple[StatLine, ...]:
    meta = char_meta.get(str(avatar_id), {}) if isinstance(char_meta, dict) else {}
    order = [str(i) for i in (meta.get("SkillOrder") or [])]
    levels = avatar.get("skillLevelMap") or {}
    extra = avatar.get("proudSkillExtraLevelMap") or {}
    labels = ("Обычная атака", "Элемент. навык", "Взрыв стихии")
    lines: list[StatLine] = []
    for idx, skill_id in enumerate(order[:3]):
        base = _to_int(levels.get(skill_id), 1)
        boost = _to_int(extra.get(skill_id), 0)
        lines.append(StatLine(labels[idx], str(base + boost)))
    return tuple(lines)


def _build_weapon(avatar: dict[str, Any]) -> WeaponData | None:
    ru_loc = _LOC_RU or {}
    for item in avatar.get("equipList") or []:
        if "weapon" not in item:
            continue
        flat = item.get("flat") or {}
        weapon = item.get("weapon") or {}
        name_hash = str(flat.get("nameTextMapHash") or "")
        name = str(ru_loc.get(name_hash) or name_hash or "Weapon")
        icon_url = _enka_ui_url(str(flat.get("icon") or ""))
        rarity = _to_int(flat.get("rankLevel"), 0)
        level = _to_int(weapon.get("level"), 1)
        affix_map = weapon.get("affixMap") or {}
        refinement = (_to_int(next(iter(affix_map.values())), 0) + 1) if isinstance(affix_map, dict) and affix_map else 1
        stat_lines: list[StatLine] = []
        for stat in flat.get("weaponStats") or []:
            prop = str(stat.get("appendPropId") or "")
            value = _to_float(stat.get("statValue"))
            label = FIGHT_LABELS.get(prop, prop)
            stat_lines.append(StatLine(label=label, value=_format_stat_value(prop, value)))
        return WeaponData(name=name, icon_url=icon_url, rarity=rarity, level=level, refinement=refinement, stat_lines=tuple(stat_lines))
    return None


def _build_artifacts(avatar: dict[str, Any]) -> tuple[ArtifactData, ...]:
    ru_loc = _LOC_RU or {}
    artifacts: list[tuple[int, ArtifactData]] = []
    for item in avatar.get("equipList") or []:
        reliquary = item.get("reliquary")
        if not isinstance(reliquary, dict):
            continue
        flat = item.get("flat") or {}
        equip_type = str(flat.get("equipType") or "")
        order = SLOT_ORDER.get(equip_type, 99)
        slot = SLOT_LABEL.get(equip_type, "Артефакт")
        name_hash = str(flat.get("nameTextMapHash") or "")
        name = str(ru_loc.get(name_hash) or name_hash or "Artifact")
        icon_url = _enka_ui_url(str(flat.get("icon") or ""))
        rarity = _to_int(flat.get("rankLevel"), 0)
        level = max(0, _to_int(reliquary.get("level"), 1) - 1)

        main_raw = flat.get("reliquaryMainstat") or {}
        main_prop = str(main_raw.get("mainPropId") or "")
        main_val = _to_float(main_raw.get("statValue"))
        main = StatLine(FIGHT_LABELS.get(main_prop, main_prop), _format_stat_value(main_prop, main_val))

        sub_stats: list[StatLine] = []
        for sub in flat.get("reliquarySubstats") or []:
            prop = str(sub.get("appendPropId") or "")
            val = _to_float(sub.get("statValue"))
            sub_stats.append(StatLine(FIGHT_LABELS.get(prop, prop), _format_stat_value(prop, val)))

        artifacts.append(
            (
                order,
                ArtifactData(
                    slot=slot,
                    name=name,
                    icon_url=icon_url,
                    rarity=rarity,
                    level=level,
                    main_stat=main,
                    sub_stats=tuple(sub_stats[:4]),
                ),
            )
        )
    artifacts.sort(key=lambda x: x[0])
    return tuple(item for _, item in artifacts)


async def fetch_enka_profile(uid: str) -> EnkaProfile:
    timeout = aiohttp.ClientTimeout(total=25)
    headers = {"User-Agent": "hikka-genshin-module/1.0"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        char_meta, ru_loc = await _load_meta(session)
        async with session.get(f"{ENKA_BASE_URL}/{uid}") as response:
            if response.status == 400:
                raise EnkaFetchError("Неверный формат UID.")
            if response.status == 404:
                raise EnkaFetchError("Игрок с таким UID не найден.")
            if response.status == 429:
                raise EnkaFetchError("Слишком много запросов к Enka. Попробуй позже.")
            if response.status >= 400:
                raise EnkaFetchError(f"Enka API status={response.status}")
            data = await response.json(content_type=None)

    avatars = data.get("avatarInfoList") or []
    if not avatars:
        raise EnkaFetchError("Витрина закрыта или в ней нет персонажей.")

    info = data.get("playerInfo") or {}
    profile_avatar_id = _to_int((info.get("profilePicture") or {}).get("avatarId"), 0)
    profile_icon, _ = _avatar_urls(char_meta, profile_avatar_id)
    player = PlayerData(
        nickname=str(info.get("nickname") or "Unknown"),
        uid=uid,
        adventure_rank=_to_int(info.get("level"), 0),
        world_level=_to_int(info.get("worldLevel"), 0),
        achievements=_to_int(info.get("finishAchievementNum"), 0),
        abyss_floor=_to_int(info.get("towerFloorIndex"), 0),
        abyss_level=_to_int(info.get("towerLevelIndex"), 0),
        avatar_icon_url=profile_icon,
    )

    options: list[CharacterOption] = []
    avatars_by_id: dict[int, dict[str, Any]] = {}
    for avatar in avatars:
        avatar_id = _to_int(avatar.get("avatarId"))
        if avatar_id <= 0:
            continue
        meta = char_meta.get(str(avatar_id), {}) if isinstance(char_meta, dict) else {}
        prop_map = avatar.get("propMap") or {}
        options.append(
            CharacterOption(
                avatar_id=avatar_id,
                name=_name_by_id(char_meta, ru_loc, avatar_id),
                level=_get_level(prop_map),
                constellation=len(avatar.get("talentIdList") or []),
                element=str(meta.get("Element") or ""),
            )
        )
        avatars_by_id[avatar_id] = avatar
    options.sort(key=lambda x: (-x.level, x.name))
    return EnkaProfile(uid=uid, player=player, options=tuple(options), avatars_by_id=avatars_by_id)


def build_character_card_data(profile: EnkaProfile, avatar_id: int) -> CharacterCardData:
    avatar = profile.avatars_by_id.get(avatar_id)
    if avatar is None:
        raise EnkaFetchError("Персонаж не найден в данных UID.")

    char_meta = _CHAR_META or {}
    ru_loc = _LOC_RU or {}
    meta = char_meta.get(str(avatar_id), {}) if isinstance(char_meta, dict) else {}
    prop_map = avatar.get("propMap") or {}

    level = _get_level(prop_map)
    max_level = _get_max_level(prop_map, level)
    constellation = len(avatar.get("talentIdList") or [])
    friendship = _to_int((avatar.get("fetterInfo") or {}).get("expLevel"), 0)
    element = str(meta.get("Element") or "")
    name = _name_by_id(char_meta, ru_loc, avatar_id)
    icon_url, splash_url = _avatar_urls(char_meta, avatar_id)

    weapon = _build_weapon(avatar)
    if weapon is not None and weapon.name.isdigit():
        # если нет локализации, оставляем нейтральное имя
        weapon = WeaponData(
            name="Weapon",
            icon_url=weapon.icon_url,
            rarity=weapon.rarity,
            level=weapon.level,
            refinement=weapon.refinement,
            stat_lines=weapon.stat_lines,
        )

    artifacts = _build_artifacts(avatar)
    normalized_arts: list[ArtifactData] = []
    for art in artifacts:
        art_name = art.name if not art.name.isdigit() else art.slot
        normalized_arts.append(
            ArtifactData(
                slot=art.slot,
                name=art_name,
                icon_url=art.icon_url,
                rarity=art.rarity,
                level=art.level,
                main_stat=art.main_stat,
                sub_stats=art.sub_stats,
            )
        )

    return CharacterCardData(
        player=profile.player,
        avatar_id=avatar_id,
        name=name,
        level=level,
        max_level=max_level,
        constellation=constellation,
        friendship=friendship,
        element=element,
        avatar_icon_url=icon_url,
        splash_url=splash_url,
        stats=_build_stats(avatar, element),
        skills=_build_skills(avatar, avatar_id, char_meta),
        weapon=weapon,
        artifacts=tuple(normalized_arts),
    )


def _safe_rarity(rarity: int) -> int:
    return max(1, min(5, int(rarity or 1)))


class Genshin(loader.Module):
    """Enka.Network карточки профиля и персонажей Genshin."""

    strings = {
        "name": "Genshin",
    }

    def __init__(self) -> None:
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "timeout",
                25,
                "Таймаут запросов к Enka/картинкам (сек)",
                validator=loader.validators.Integer(minimum=10, maximum=60),
            )
        )

    async def client_ready(self, client, db) -> None:
        self.client = client
        self.db = db

    def _uid_map(self) -> dict[str, str]:
        raw = self.db.get(self.strings["name"], DB_UID_MAP_KEY, {}) or {}
        if not isinstance(raw, dict):
            return {}
        fixed: dict[str, str] = {}
        for key, value in raw.items():
            alias = str(key).strip().lower()
            uid = str(value).strip()
            if alias and UID_RE.fullmatch(uid):
                fixed[alias] = uid
        return fixed

    def _save_uid_map(self, mapping: dict[str, str]) -> None:
        self.db.set(self.strings["name"], DB_UID_MAP_KEY, mapping)

    def _default_uid(self) -> str | None:
        value = str(self.db.get(self.strings["name"], DB_DEFAULT_UID_KEY, "") or "").strip()
        if UID_RE.fullmatch(value):
            return value
        return None

    def _set_default_uid(self, uid: str) -> None:
        self.db.set(self.strings["name"], DB_DEFAULT_UID_KEY, uid)

    def _resolve_uid_direct(self, token: str | None) -> str | None:
        if not token:
            return None
        value = token.strip()
        if UID_RE.fullmatch(value):
            return value
        return self._uid_map().get(value.lower())

    def _resolve_uid(self, token: str | None) -> str | None:
        direct = self._resolve_uid_direct(token)
        if direct:
            return direct
        return self._default_uid()

    @staticmethod
    def _gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
        width, height = size
        image = Image.new("RGBA", size)
        draw = ImageDraw.Draw(image)
        for y in range(height):
            p = y / max(1, height - 1)
            color = (
                int(top[0] + (bottom[0] - top[0]) * p),
                int(top[1] + (bottom[1] - top[1]) * p),
                int(top[2] + (bottom[2] - top[2]) * p),
                255,
            )
            draw.line((0, y, width, y), fill=color)
        return image

    @staticmethod
    def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
        preferred = ["DejaVuSans-Bold.ttf"] if bold else ["DejaVuSans.ttf"]
        for name in preferred + ["Arial.ttf"]:
            try:
                return ImageFont.truetype(name, size)
            except Exception:
                continue
        return ImageFont.load_default()

    @staticmethod
    def _crop_square(image: Image.Image, side: int) -> Image.Image:
        img = image.convert("RGBA")
        w, h = img.size
        if w == 0 or h == 0:
            return Image.new("RGBA", (side, side), (26, 36, 74, 255))
        scale = max(side / w, side / h)
        resized = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
        rw, rh = resized.size
        left = max(0, (rw - side) // 2)
        top = max(0, (rh - side) // 2)
        return resized.crop((left, top, left + side, top + side))

    @staticmethod
    def _fit_image(image: Image.Image, width: int, height: int) -> Image.Image:
        img = image.convert("RGBA")
        img.thumbnail((width, height), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        x = (width - img.width) // 2
        y = (height - img.height) // 2
        canvas.paste(img, (x, y), img)
        return canvas

    @staticmethod
    def _rounded_image(image: Image.Image, radius: int) -> Image.Image:
        img = image.convert("RGBA")
        if img.width == 0 or img.height == 0:
            return img
        mask = Image.new("L", img.size, 0)
        mdraw = ImageDraw.Draw(mask)
        mdraw.rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=max(0, radius), fill=255)
        out = Image.new("RGBA", img.size, (0, 0, 0, 0))
        out.paste(img, (0, 0), mask)
        return out

    @staticmethod
    def _circle_image(image: Image.Image) -> Image.Image:
        img = image.convert("RGBA")
        side = min(img.width, img.height)
        if side <= 0:
            return img
        left = (img.width - side) // 2
        top = (img.height - side) // 2
        square = img.crop((left, top, left + side, top + side))
        mask = Image.new("L", (side, side), 0)
        mdraw = ImageDraw.Draw(mask)
        mdraw.ellipse((0, 0, side - 1, side - 1), fill=255)
        out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        out.paste(square, (0, 0), mask)
        return out

    @staticmethod
    def _draw_glass_panel(
        canvas: Image.Image,
        box: tuple[int, int, int, int],
        radius: int,
        *,
        fill: tuple[int, int, int, int],
        outline: tuple[int, int, int, int],
        accent: tuple[int, int, int, int] | None = None,
        width: int = 2,
    ) -> None:
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
        top_highlight = min(box[3] - 6, box[1] + max(16, (box[3] - box[1]) // 3))
        if accent and len(accent) == 4 and accent[3] > 0 and top_highlight > box[1] + 6:
            draw.rounded_rectangle(
                (box[0] + 6, box[1] + 6, box[2] - 6, top_highlight),
                radius=max(10, radius - 10),
                fill=accent,
            )
        canvas.alpha_composite(layer)

    @staticmethod
    def _add_orbs(
        canvas: Image.Image,
        specs: Iterable[tuple[int, int, int, tuple[int, int, int], int]],
    ) -> None:
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        for x, y, radius, color, alpha in specs:
            draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                fill=(int(color[0]), int(color[1]), int(color[2]), max(0, min(255, alpha))),
            )
        canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(34)))

    @staticmethod
    def _fit_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
        if draw.textlength(text, font=font) <= max_width:
            return text
        ellipsis = "..."
        trimmed = text
        while trimmed and draw.textlength(trimmed + ellipsis, font=font) > max_width:
            trimmed = trimmed[:-1]
        return (trimmed.rstrip() + ellipsis) if trimmed else ellipsis

    async def _download_image(self, url: str, *, width: int, height: int, square: bool = False) -> Image.Image:
        fallback = Image.new("RGBA", (width, height), (30, 40, 74, 255))
        if not url:
            return fallback
        timeout = aiohttp.ClientTimeout(total=int(self.config["timeout"]))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return fallback
                    raw = await resp.read()
            image = Image.open(io.BytesIO(raw)).convert("RGBA")
        except Exception:
            return fallback
        if square:
            return self._crop_square(image, min(width, height)).resize((width, height), Image.Resampling.LANCZOS)
        return self._fit_image(image, width, height)

    @staticmethod
    def _to_png(image: Image.Image, name: str) -> io.BytesIO:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        buffer.name = name
        return buffer

    def _pick_character(self, profile: EnkaProfile, query: str) -> CharacterCardData | None:
        text = query.strip()
        if not text:
            return None
        if text.isdigit():
            avatar_id = int(text)
            if avatar_id in profile.avatars_by_id:
                return build_character_card_data(profile, avatar_id)
        lower = text.lower()
        exact = [opt for opt in profile.options if opt.name.lower() == lower]
        if exact:
            return build_character_card_data(profile, exact[0].avatar_id)
        partial = [opt for opt in profile.options if lower in opt.name.lower()]
        if partial:
            return build_character_card_data(profile, partial[0].avatar_id)
        return None

    async def _render_profile_card(self, profile: EnkaProfile) -> Image.Image:
        canvas = self._gradient((1280, 740), (27, 36, 74), (8, 14, 33))
        self._add_orbs(
            canvas,
            (
                (160, 120, 180, (86, 156, 255), 74),
                (1130, 90, 200, (120, 98, 255), 68),
                (1090, 640, 240, (72, 200, 192), 52),
                (250, 690, 180, (64, 120, 255), 46),
            ),
        )
        draw = ImageDraw.Draw(canvas)

        title_font = self._font(54, bold=True)
        subtitle_font = self._font(32)
        chip_font = self._font(28, bold=True)
        item_font = self._font(24)

        draw.rounded_rectangle((12, 12, 1268, 728), radius=52, outline=(65, 96, 176, 255), width=2)
        self._draw_glass_panel(
            canvas,
            (28, 24, 1252, 224),
            46,
            fill=(23, 33, 68, 232),
            outline=(130, 165, 255, 128),
            accent=None,
        )
        self._draw_glass_panel(
            canvas,
            (28, 242, 1252, 706),
            40,
            fill=(18, 27, 58, 228),
            outline=(110, 142, 225, 104),
            accent=None,
        )

        avatar = await self._download_image(profile.player.avatar_icon_url, width=154, height=154, square=True)
        avatar = self._circle_image(avatar)
        canvas.paste(avatar, (62, 48), avatar)
        draw.ellipse((58, 44, 220, 206), outline=(178, 214, 255, 255), width=4)
        draw.ellipse((66, 52, 212, 198), outline=(226, 238, 255, 255), width=2)

        nickname = self._fit_text(draw, profile.player.nickname, title_font, 470)
        draw.text((244, 54), nickname, font=title_font, fill=(247, 252, 255, 255))
        draw.text((244, 124), f"UID: {profile.uid}", font=subtitle_font, fill=(182, 200, 236, 255))

        chip_data = (
            f"AR {profile.player.adventure_rank} • WR {profile.player.world_level}",
            f"Достижения: {profile.player.achievements}",
            f"Бездна: {profile.player.abyss_floor}-{profile.player.abyss_level}",
        )
        for idx, text in enumerate(chip_data):
            y = 56 + idx * 50
            box = (736, y, 1222, y + 42)
            draw.rounded_rectangle(box, radius=21, fill=(38, 56, 104, 255), outline=(88, 118, 196, 255), width=2)
            draw.text((756, y + 7), text, font=chip_font, fill=(235, 244, 255, 255))

        draw.rounded_rectangle((48, 264, 372, 310), radius=22, fill=(48, 73, 132, 255), outline=(96, 128, 206, 255), width=2)
        draw.text((64, 272), "Персонажи в витрине", font=self._font(34, bold=True), fill=(245, 250, 255, 255))

        top_list = profile.options[:18]
        columns = 2
        rows_per_col = 9
        for idx, option in enumerate(top_list):
            col = idx // rows_per_col
            row = idx % rows_per_col
            x = 52 + col * 598
            y = 320 + row * 42
            element = ELEMENT_LABELS.get(option.element, option.element or "Стихия")
            line = f"{idx + 1}. {option.name} • ур.{option.level} • C{option.constellation} • {element}"
            line = self._fit_text(draw, line, item_font, 534)
            draw.rounded_rectangle((x, y, x + 566, y + 34), radius=17, fill=(30, 44, 86, 255), outline=(75, 106, 178, 255), width=1)
            draw.text((x + 14, y + 5), line, font=item_font, fill=(219, 231, 255, 255))

        if len(profile.options) > len(top_list):
            draw.text(
                (56, 676),
                f"И еще персонажей: {len(profile.options) - len(top_list)}",
                font=self._font(26),
                fill=(171, 189, 232, 255),
            )
        draw.text((930, 682), "enka.network • genshin module", font=self._font(22), fill=(150, 170, 216, 255))
        return canvas

    async def _render_character_card(self, card: CharacterCardData) -> Image.Image:
        canvas = self._gradient((1340, 920), (22, 30, 66), (8, 12, 30))
        self._add_orbs(
            canvas,
            (
                (190, 128, 200, (78, 152, 255), 72),
                (1210, 170, 240, (124, 100, 255), 66),
                (1130, 770, 280, (76, 196, 188), 56),
                (220, 850, 190, (62, 126, 255), 50),
            ),
        )
        draw = ImageDraw.Draw(canvas)

        title_font = self._font(66, bold=True)
        sub_font = self._font(32)
        stat_font = self._font(29)
        tiny_font = self._font(24)

        draw.rounded_rectangle((12, 12, 1328, 908), radius=56, outline=(64, 98, 176, 255), width=2)
        self._draw_glass_panel(
            canvas,
            (24, 20, 1316, 186),
            48,
            fill=(23, 33, 68, 232),
            outline=(128, 166, 255, 130),
            accent=None,
        )
        self._draw_glass_panel(
            canvas,
            (24, 202, 826, 896),
            40,
            fill=(18, 28, 58, 228),
            outline=(108, 142, 226, 108),
            accent=None,
        )
        self._draw_glass_panel(
            canvas,
            (840, 202, 1316, 896),
            40,
            fill=(18, 28, 56, 224),
            outline=(108, 142, 226, 100),
            accent=None,
        )

        avatar_task = asyncio.create_task(self._download_image(card.avatar_icon_url, width=130, height=130, square=True))
        weapon_task = asyncio.create_task(
            self._download_image(card.weapon.icon_url if card.weapon else "", width=118, height=118)
        )
        artifact_tasks = [
            asyncio.create_task(self._download_image(artifact.icon_url, width=78, height=78))
            for artifact in card.artifacts[:5]
        ]

        avatar = await avatar_task
        weapon_icon = await weapon_task
        artifact_icons = await asyncio.gather(*artifact_tasks) if artifact_tasks else []

        avatar = self._circle_image(avatar)
        canvas.paste(avatar, (54, 38), avatar)
        draw.ellipse((50, 34, 188, 172), outline=(182, 216, 255, 255), width=4)
        draw.ellipse((58, 42, 180, 164), outline=(226, 238, 255, 255), width=2)

        nickname = self._fit_text(draw, card.player.nickname, self._font(54, bold=True), 620)
        draw.text((206, 50), nickname, font=self._font(54, bold=True), fill=(245, 251, 255, 255))
        draw.text((204, 116), f"UID: {card.player.uid}", font=self._font(34), fill=(176, 191, 231, 255))
        draw.rounded_rectangle((910, 62, 1270, 108), radius=23, fill=(38, 56, 104, 255), outline=(88, 118, 196, 255), width=2)
        draw.text((932, 72), f"AR {card.player.adventure_rank} • WR {card.player.world_level}", font=self._font(31, bold=True), fill=(235, 242, 255, 255))

        draw.text((54, 220), card.name, font=title_font, fill=(250, 253, 255, 255))
        element = ELEMENT_LABELS.get(card.element, card.element or "Стихия")
        draw.text((54, 298), f"Уровень {card.level}/{card.max_level} • C{card.constellation} • Дружба {card.friendship}", font=sub_font, fill=(194, 220, 255, 255))
        elem_font = self._font(34, bold=True)
        elem_w = int(draw.textlength(element, font=elem_font))
        draw.rounded_rectangle((54, 344, 54 + elem_w + 40, 388), radius=22, fill=(45, 116, 110, 255), outline=(102, 178, 162, 255), width=2)
        draw.text((74, 350), element, font=elem_font, fill=(192, 255, 236, 255))

        stats = list(card.stats[:8])
        value_font = self._font(30, bold=True)
        compact_value_font = self._font(26, bold=True)
        value_box_right = 786
        value_box_min_width = 124
        value_box_max_width = 186
        for idx, stat in enumerate(stats):
            y = 396 + idx * 58
            draw.rounded_rectangle((50, y, 798, y + 50), radius=20, fill=(28, 40, 80, 255), outline=(77, 107, 180, 255), width=1)
            draw.text((70, y + 10), stat.label, font=stat_font, fill=(217, 231, 255, 255))
            text_w = float(draw.textlength(stat.value, font=value_font))
            bubble_width = int(max(value_box_min_width, min(value_box_max_width, text_w + 28)))
            value_box_left = value_box_right - bubble_width
            draw.rounded_rectangle((value_box_left, y + 7, value_box_right, y + 43), radius=16, fill=(56, 87, 150, 255))

            value_draw_font = value_font
            if text_w > bubble_width - 18:
                value_draw_font = compact_value_font
                text_w = float(draw.textlength(stat.value, font=value_draw_font))
            text_x = value_box_left + max(10.0, (bubble_width - text_w) / 2.0)
            draw.text((text_x, y + 9), stat.value, font=value_draw_font, fill=(248, 252, 255, 255))

        draw.text((854, 224), "Оружие", font=self._font(36, bold=True), fill=(242, 248, 255, 255))
        self._draw_glass_panel(
            canvas,
            (852, 260, 1304, 418),
            28,
            fill=(24, 37, 74, 204),
            outline=(118, 151, 232, 94),
            accent=None,
            width=1,
        )
        weapon_icon = self._rounded_image(weapon_icon, 24)
        canvas.paste(weapon_icon, (866, 278), weapon_icon)
        if card.weapon:
            weapon_name = self._fit_text(draw, card.weapon.name, self._font(28, bold=True), 304)
            draw.text((1000, 278), weapon_name, font=self._font(28, bold=True), fill=(245, 250, 255, 255))
            draw.text(
                (1000, 316),
                f"Ур. {card.weapon.level}/90 • R{card.weapon.refinement} • {_safe_rarity(card.weapon.rarity)}★",
                font=tiny_font,
                fill=(192, 209, 247, 255),
            )
            if card.weapon.stat_lines:
                stat0 = f"{card.weapon.stat_lines[0].label} = {card.weapon.stat_lines[0].value}"
                stat0 = self._fit_text(draw, stat0, tiny_font, 292)
                draw.text(
                    (1000, 350),
                    stat0,
                    font=tiny_font,
                    fill=(214, 228, 255, 255),
                )
            if len(card.weapon.stat_lines) > 1:
                stat1 = f"{card.weapon.stat_lines[1].label} = {card.weapon.stat_lines[1].value}"
                stat1 = self._fit_text(draw, stat1, tiny_font, 292)
                draw.text(
                    (1000, 382),
                    stat1,
                    font=tiny_font,
                    fill=(214, 228, 255, 255),
                )

        draw.text((854, 438), "Артефакты", font=self._font(36, bold=True), fill=(242, 248, 255, 255))
        y = 486
        for idx, artifact in enumerate(card.artifacts[:5]):
            draw.rounded_rectangle((852, y, 1304, y + 72), radius=22, fill=(26, 39, 78, 255), outline=(79, 109, 181, 255), width=1)
            if idx < len(artifact_icons):
                icon = self._rounded_image(artifact_icons[idx], 16)
                canvas.paste(icon, (864, y + 8), icon)
            line = f"{artifact.main_stat.value} ({artifact.main_stat.label})"
            line = self._fit_text(draw, line, tiny_font, 320)
            draw.text(
                (956, y + 10),
                line,
                font=tiny_font,
                fill=(240, 247, 255, 255),
            )
            art_meta = f"{artifact.slot}: +{artifact.level}  {_safe_rarity(artifact.rarity)}*"
            art_meta = self._fit_text(draw, art_meta, self._font(22), 320)
            draw.text((956, y + 40), art_meta, font=self._font(22), fill=(184, 204, 244, 255))
            y += 78

        draw.text((972, 870), "enka.network • @elisartix", font=self._font(22), fill=(145, 161, 204, 255))
        return canvas

    async def _send_profile(self, message: Message, profile: EnkaProfile) -> None:
        card_image = await self._render_profile_card(profile)
        png = self._to_png(card_image, f"enka_profile_{profile.uid}.png")
        await self.client.send_file(
            message.chat_id,
            file=png,
            caption=f"Профиль <b>{utils.escape_html(profile.player.nickname)}</b> • UID <code>{profile.uid}</code>",
            reply_to=message.id,
        )

    async def _send_character(self, message: Message, card: CharacterCardData) -> None:
        card_image = await self._render_character_card(card)
        png = self._to_png(card_image, f"enka_character_{card.player.uid}_{card.avatar_id}.png")
        await self.client.send_file(
            message.chat_id,
            file=png,
            caption=f"<b>{utils.escape_html(card.name)}</b> • UID <code>{card.player.uid}</code>",
            reply_to=message.id,
        )

    @loader.command()
    async def enuid(self, message: Message) -> None:
        """[uid] или [alias uid] - сохранить UID/алиас."""
        args = utils.get_args_raw(message).strip()
        mapping = self._uid_map()
        if not args:
            default_uid = self._default_uid()
            lines = ["<b>Сохраненные UID:</b>"]
            if default_uid:
                lines.append(f"• По умолчанию: <code>{default_uid}</code>")
            if mapping:
                for alias, uid in sorted(mapping.items()):
                    lines.append(f"• <code>{alias}</code> -> <code>{uid}</code>")
            if not default_uid and not mapping:
                lines.append("• Список пуст.")
            lines.append("\nПримеры:")
            lines.append("<code>.enuid 862278867</code>")
            lines.append("<code>.enuid asia 862278867</code>")
            return await utils.answer(message, "\n".join(lines))

        parts = args.split()
        if len(parts) == 1:
            uid = parts[0]
            if not UID_RE.fullmatch(uid):
                return await utils.answer(message, "UID должен содержать 9 или 10 цифр.")
            self._set_default_uid(uid)
            return await utils.answer(message, f"UID по умолчанию сохранен: <code>{uid}</code>")

        alias = parts[0].strip().lower()
        uid = parts[1].strip()
        if not alias:
            return await utils.answer(message, "Укажи alias.")
        if not UID_RE.fullmatch(uid):
            return await utils.answer(message, "UID должен содержать 9 или 10 цифр.")
        mapping[alias] = uid
        self._save_uid_map(mapping)
        self._set_default_uid(uid)
        await utils.answer(message, f"Сохранено: <code>{alias}</code> -> <code>{uid}</code>")

    @loader.command()
    async def endeluid(self, message: Message) -> None:
        """<alias> - удалить сохраненный alias UID."""
        alias = utils.get_args_raw(message).strip().lower()
        if not alias:
            return await utils.answer(message, "Использование: <code>.endeluid alias</code>")
        mapping = self._uid_map()
        if alias not in mapping:
            return await utils.answer(message, "Такой alias не найден.")
        uid = mapping.pop(alias)
        self._save_uid_map(mapping)
        await utils.answer(message, f"Удалено: <code>{alias}</code> ({uid})")

    @loader.command()
    async def enprofile(self, message: Message) -> None:
        """[uid|alias] - карточка профиля Enka."""
        uid = self._resolve_uid(utils.get_args_raw(message).strip() or None)
        if not uid:
            return await utils.answer(message, "Сначала задай UID: <code>.enuid 862278867</code>")
        status = await utils.answer(message, "⌛️ Загружаю профиль Enka...")
        try:
            profile = await fetch_enka_profile(uid)
            await self._send_profile(message, profile)
            await status.delete()
        except EnkaFetchError as exc:
            await utils.answer(status, f"Ошибка Enka: <code>{utils.escape_html(str(exc))}</code>")
        except Exception as exc:
            logger.exception("enprofile failed uid=%s", uid)
            await utils.answer(status, f"Ошибка: <code>{utils.escape_html(str(exc))}</code>")

    @loader.command()
    async def enchars(self, message: Message) -> None:
        """[uid|alias] - список персонажей витрины."""
        uid = self._resolve_uid(utils.get_args_raw(message).strip() or None)
        if not uid:
            return await utils.answer(message, "Сначала задай UID: <code>.enuid 862278867</code>")
        status = await utils.answer(message, "⌛️ Загружаю персонажей...")
        try:
            profile = await fetch_enka_profile(uid)
            lines = [f"<b>Персонажи UID {uid}:</b>"]
            for option in profile.options[:40]:
                lines.append(
                    f"• <code>{option.avatar_id}</code> — {utils.escape_html(option.name)} "
                    f"(ур. {option.level}, C{option.constellation})"
                )
            if len(profile.options) > 40:
                lines.append(f"... и еще {len(profile.options) - 40}")
            await utils.answer(status, "\n".join(lines))
        except EnkaFetchError as exc:
            await utils.answer(status, f"Ошибка Enka: <code>{utils.escape_html(str(exc))}</code>")
        except Exception as exc:
            logger.exception("enchars failed uid=%s", uid)
            await utils.answer(status, f"Ошибка: <code>{utils.escape_html(str(exc))}</code>")

    @loader.command()
    async def enchar(self, message: Message) -> None:
        """<имя|avatar_id> [uid|alias] - карточка конкретного персонажа."""
        args = utils.get_args_raw(message).strip()
        if not args:
            return await utils.answer(message, "Использование: <code>.enchar Флинс [uid|alias]</code>")

        parts = args.split()
        uid = None
        query_parts: Iterable[str] = parts
        if len(parts) >= 2:
            tail_uid = self._resolve_uid_direct(parts[-1])
            if tail_uid:
                uid = tail_uid
                query_parts = parts[:-1]
        if uid is None:
            uid = self._resolve_uid(None)
        query = " ".join(query_parts).strip()

        if not uid:
            return await utils.answer(message, "UID не найден. Задай его: <code>.enuid 862278867</code>")
        if not query:
            return await utils.answer(message, "Укажи имя персонажа или avatar_id.")

        status = await utils.answer(message, "⌛️ Генерирую карточку персонажа...")
        try:
            profile = await fetch_enka_profile(uid)
            card = self._pick_character(profile, query)
            if card is None:
                names = ", ".join(utils.escape_html(opt.name) for opt in profile.options[:12])
                return await utils.answer(
                    status,
                    "Персонаж не найден в витрине.\n"
                    f"Примеры из витрины: {names}",
                )
            await self._send_character(message, card)
            await status.delete()
        except EnkaFetchError as exc:
            await utils.answer(status, f"Ошибка Enka: <code>{utils.escape_html(str(exc))}</code>")
        except Exception as exc:
            logger.exception("enchar failed uid=%s query=%s", uid, query)
            await utils.answer(status, f"Ошибка: <code>{utils.escape_html(str(exc))}</code>")
