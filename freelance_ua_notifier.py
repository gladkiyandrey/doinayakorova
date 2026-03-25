#!/usr/bin/env python3
import argparse
import json
import os
import plistlib
import re
import threading
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.example.json")
DEFAULT_STATE_PATH = Path(__file__).with_name(".freelance_ua_seen.json")
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) CodexFreelanceUANotifier/2.0"
DEFAULT_LABEL = "ua.freelance.notifier"
DEFAULT_RUNTIME_LINK = Path.home() / ".freelance_ua_notifier_runtime"
MAX_SEEN_GUIDS = 2000

MAIN_MENU = [
    ["Статус", "Проверить"],
    ["Категории", "Пауза"],
    ["Помощь", "Меню"],
]
KEYWORDS_MENU = [
    ["Показать ключевые", "Настроить include"],
    ["Настроить exclude", "Режим И/ИЛИ"],
    ["Очистить ключевые", "Назад"],
]
CATEGORIES_MENU = [
    ["Показать категории", "Список с сайта"],
    ["Выбрать include", "Выбрать exclude"],
    ["Ввести include", "Ввести exclude"],
    ["Очистить категории", "Назад"],
]
BUDGET_MENU = [
    ["Показать бюджет", "Готовые диапазоны"],
    ["Мин +500", "Мин -500"],
    ["Макс +500", "Макс -500"],
    ["Мин +1000", "Макс +1000"],
    ["Ввести вручную", "Сбросить"],
    ["Назад"],
]
QUICK_BUDGET_MENU = [
    ["До 1000", "1000-5000"],
    ["5000-15000", "15000-30000"],
    ["От 30000", "Любой бюджет"],
    ["Назад"],
]


@dataclass
class Order:
    guid: str
    title: str
    link: str
    description: str
    category: str
    pub_date: Optional[datetime]
    price_uah: Optional[int]


@dataclass
class SiteCategory:
    name: str
    specializations: List[str]


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def fetch_text(url: str, timeout: int = 25) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def ensure_private_file(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def env_str(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value if value else None


def env_int(name: str) -> Optional[int]:
    value = env_str(name)
    if value is None:
        return None
    return int(value)


def env_bool(name: str) -> Optional[bool]:
    value = env_str(name)
    if value is None:
        return None
    return value.lower() in {"1", "true", "yes", "on"}


def env_list(name: str) -> Optional[List[str]]:
    value = env_str(name)
    if value is None:
        return None
    if value.startswith("["):
        parsed = json.loads(value)
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [part.strip() for part in re.split(r"[,;\n]+", value) if part.strip()]


def config_from_env(base_config: dict) -> dict:
    config = dict(base_config)
    mapping = {
        "TELEGRAM_BOT_TOKEN": ("telegram_bot_token", env_str),
        "TELEGRAM_CHAT_ID": ("telegram_chat_id", env_str),
        "FEED_URL": ("feed_url", env_str),
        "ORDERS_URL": ("orders_url", env_str),
        "POLL_INTERVAL_SECONDS": ("poll_interval_seconds", env_int),
        "PAGES_TO_SCAN": ("pages_to_scan", env_int),
        "INCLUDE_KEYWORDS": ("include_keywords", env_list),
        "EXCLUDE_KEYWORDS": ("exclude_keywords", env_list),
        "INCLUDE_CATEGORIES": ("include_categories", env_list),
        "EXCLUDE_CATEGORIES": ("exclude_categories", env_list),
        "REQUIRE_ALL_KEYWORDS": ("require_all_keywords", env_bool),
        "MIN_PRICE_UAH": ("min_price_uah", env_int),
        "MAX_PRICE_UAH": ("max_price_uah", env_int),
        "ALLOWED_CHAT_IDS": ("allowed_chat_ids", env_list),
    }
    for env_name, (config_key, parser) in mapping.items():
        value = parser(env_name)
        if value is not None:
            config[config_key] = value
    return config


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"ok\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def maybe_start_health_server() -> Optional[HTTPServer]:
    port_value = env_str("PORT")
    if port_value is None:
        return None
    server = HTTPServer(("0.0.0.0", int(port_value)), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def parse_feed(feed_xml: str) -> List[Order]:
    root = ET.fromstring(feed_xml)
    orders: List[Order] = []
    for item in root.findall("./channel/item"):
        pub_date = None
        raw_pub_date = (item.findtext("pubDate") or "").strip()
        if raw_pub_date:
            try:
                pub_date = parsedate_to_datetime(raw_pub_date)
            except (TypeError, ValueError):
                pub_date = None

        order = Order(
            guid=(item.findtext("guid") or item.findtext("link") or "").strip(),
            title=(item.findtext("title") or "").strip(),
            link=(item.findtext("link") or "").strip(),
            description=(item.findtext("description") or "").strip(),
            category=(item.findtext("category") or "").strip(),
            pub_date=pub_date,
            price_uah=None,
        )
        if order.guid:
            orders.append(order)
    return orders


def parse_prices_from_orders_page(html: str) -> Dict[str, Optional[int]]:
    prices: Dict[str, Optional[int]] = {}
    pattern = re.compile(
        r'<li class="j-order .*?>.*?<header class="l-project-title">.*?<a href="(?P<link>https://freelance\.ua/[^"]+)"[^>]*>.*?</a>.*?'
        r'<div class="l-project-head flex-price-tag">\s*(?:<span class="l-price">(?P<price>[^<]+)</span>)?',
        re.S,
    )
    for match in pattern.finditer(html):
        link = unescape(match.group("link")).strip()
        raw_price = unescape((match.group("price") or "").strip())
        prices[link] = extract_price_uah(raw_price)
    return prices


def parse_orders_from_orders_page(html: str) -> List[Order]:
    orders: List[Order] = []
    pattern = re.compile(
        r'<li class="j-order .*?>.*?<header class="l-project-title">.*?<a href="(?P<link>https://freelance\.ua/[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
        r'<div class="l-project-head flex-price-tag">\s*(?:<span class="l-price">(?P<price>[^<]+)</span>)?.*?</div>\s*'
        r'<article><p>(?P<description>.*?)</p>',
        re.S,
    )
    for match in pattern.finditer(html):
        title = clean_html_text(match.group("title"))
        description = clean_html_text(match.group("description"))
        link = clean_html_text(match.group("link"))
        orders.append(
            Order(
                guid=link,
                title=title,
                link=link,
                description=description,
                category="",
                pub_date=None,
                price_uah=extract_price_uah(unescape((match.group("price") or "").strip())),
            )
        )
    return orders


def extract_price_uah(raw_price: str) -> Optional[int]:
    if not raw_price:
        return None
    digits = re.sub(r"[^\d]", "", raw_price)
    return int(digits) if digits else None


def normalize_text(value: str) -> str:
    return " ".join(unescape(value).lower().split())


def normalize_list(values: Sequence[str]) -> List[str]:
    return [normalize_text(x) for x in values if str(x).strip()]


def split_user_list(text: str) -> List[str]:
    parts = re.split(r"[,;\n]+", text)
    return [part.strip() for part in parts if part.strip()]


def clean_html_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    return " ".join(unescape(text).split())


def parse_site_categories(html: str) -> List[SiteCategory]:
    categories: List[SiteCategory] = []
    block_match = re.search(
        r'<ul class="l-left-categories l-inside visible-md visible-lg">(?P<body>.*?)</ul><!-- Mobile Categories -->',
        html,
        re.S,
    )
    if not block_match:
        return categories

    category_pattern = re.compile(
        r'<li data-id="[^"]+"><span><i class="fa fa-circle-o j-cat-check"></i> <span class="j-cat-title">(?P<name>.*?)</span></span>\s*'
        r'<ul class="hidden">(?P<specs>.*?)</ul></li>',
        re.S,
    )
    spec_pattern = re.compile(r'<span class="j-spec"[^>]*><i class="fa fa-square-o"></i> (?P<name>.*?)</span>', re.S)

    for match in category_pattern.finditer(block_match.group("body")):
        category_name = clean_html_text(match.group("name"))
        specs = [clean_html_text(spec.group("name")) for spec in spec_pattern.finditer(match.group("specs"))]
        categories.append(SiteCategory(name=category_name, specializations=specs))
    return categories


def collect_site_categories(config: dict) -> List[SiteCategory]:
    html = fetch_text(config.get("orders_url") or "https://freelance.ua/ru/orders/")
    return parse_site_categories(html)


def print_categories(config: dict) -> int:
    categories = collect_site_categories(config)
    if not categories:
        print("Не удалось получить категории.")
        return 1

    for category in categories:
        print(category.name)
        for spec in category.specializations:
            print(f"  - {spec}")
    return 0


def collect_orders(config: dict) -> List[Order]:
    orders_by_link: Dict[str, Order] = {}

    try:
        feed_xml = fetch_text(config.get("feed_url") or "https://freelance.ua/ru/orders/rss")
        for order in parse_feed(feed_xml):
            orders_by_link[order.link] = order
    except (HTTPError, URLError, TimeoutError):
        pass

    base_orders_url = config.get("orders_url") or "https://freelance.ua/ru/orders/"
    pages_to_scan = max(1, int(config.get("pages_to_scan", 3)))
    for page in range(1, pages_to_scan + 1):
        page_url = base_orders_url if page == 1 else f"{base_orders_url}?page={page}&t=1"
        try:
            html = fetch_text(page_url)
        except (HTTPError, URLError, TimeoutError):
            continue
        for parsed_order in parse_orders_from_orders_page(html):
            existing = orders_by_link.get(parsed_order.link)
            if existing is None:
                orders_by_link[parsed_order.link] = parsed_order
                continue
            if not existing.title and parsed_order.title:
                existing.title = parsed_order.title
            if not existing.description and parsed_order.description:
                existing.description = parsed_order.description
            if existing.price_uah is None and parsed_order.price_uah is not None:
                existing.price_uah = parsed_order.price_uah

    return list(orders_by_link.values())


def order_matches(order: Order, settings: dict) -> bool:
    haystack = normalize_text(" ".join([order.title, order.description, order.category]))
    include_keywords = normalize_list(settings.get("include_keywords", []))
    exclude_keywords = normalize_list(settings.get("exclude_keywords", []))
    include_categories = normalize_list(settings.get("include_categories", []))
    exclude_categories = normalize_list(settings.get("exclude_categories", []))
    require_all_keywords = bool(settings.get("require_all_keywords", False))

    use_keyword_fallback = not include_categories
    if use_keyword_fallback and include_keywords and require_all_keywords and not all(keyword in haystack for keyword in include_keywords):
        return False
    if use_keyword_fallback and include_keywords and not require_all_keywords and not any(keyword in haystack for keyword in include_keywords):
        return False
    if exclude_keywords and any(keyword in haystack for keyword in exclude_keywords):
        return False

    category_text = normalize_text(order.category)
    if include_categories and not any(category in category_text for category in include_categories):
        return False
    if exclude_categories and any(category in category_text for category in exclude_categories):
        return False

    min_price = settings.get("min_price_uah")
    max_price = settings.get("max_price_uah")
    if min_price is not None:
        if order.price_uah is None or order.price_uah < int(min_price):
            return False
    if max_price is not None and order.price_uah is not None and order.price_uah > int(max_price):
        return False
    return True


def format_price(price_uah: Optional[int]) -> str:
    if price_uah is None:
        return "не указан"
    return f"{price_uah} грн"


def format_order_message(order: Order) -> str:
    pub_date = "неизвестно"
    if order.pub_date is not None:
        pub_date = order.pub_date.strftime("%Y-%m-%d %H:%M")

    description = " ".join(order.description.split())
    if len(description) > 450:
        description = description[:447].rstrip() + "..."

    return "\n".join(
        [
            "Новый заказ на freelance.ua",
            "",
            f"Название: {order.title}",
            f"Категория: {order.category or 'не указана'}",
            f"Бюджет: {format_price(order.price_uah)}",
            f"Дата: {pub_date}",
            "",
            f"Описание: {description or 'нет описания'}",
            "",
            order.link,
        ]
    )


def build_reply_markup(keyboard: List[List[str]], resize: bool = True) -> str:
    return json.dumps({"keyboard": keyboard, "resize_keyboard": resize}, ensure_ascii=False)


def telegram_api(config: dict, method: str, data: Optional[dict] = None, timeout: int = 25) -> dict:
    token = str(config["telegram_bot_token"])
    url = f"https://api.telegram.org/bot{token}/{method}"
    encoded = None
    headers = {"User-Agent": USER_AGENT}
    if data is not None:
        encoded = urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = Request(url, data=encoded, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(body)
    if not parsed.get("ok"):
        raise RuntimeError(f"Telegram API error: {parsed}")
    return parsed


def send_telegram_message(config: dict, chat_id: str, text: str, keyboard: Optional[List[List[str]]] = None) -> None:
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
    if keyboard is not None:
        payload["reply_markup"] = build_reply_markup(keyboard)
    telegram_api(config, "sendMessage", payload)


def set_telegram_commands(config: dict) -> None:
    commands = json.dumps(
        [
            {"command": "start", "description": "Запустить меню"},
            {"command": "menu", "description": "Показать меню"},
            {"command": "status", "description": "Показать текущие фильтры"},
            {"command": "help", "description": "Помощь по настройке"},
            {"command": "check", "description": "Проверить заказы сейчас"},
            {"command": "pause", "description": "Поставить уведомления на паузу"},
            {"command": "resume", "description": "Снять паузу"},
            {"command": "cancel", "description": "Отменить текущий ввод"},
        ],
        ensure_ascii=False,
    )
    telegram_api(config, "setMyCommands", {"commands": commands})


def get_updates(config: dict, offset: int, timeout: int = 20) -> List[dict]:
    payload = {
        "offset": str(offset),
        "timeout": str(timeout),
        "allowed_updates": json.dumps(["message"]),
    }
    result = telegram_api(config, "getUpdates", payload, timeout=timeout + 5)
    return result.get("result", [])


def default_chat_settings(config: dict) -> dict:
    return {
        "include_keywords": list(config.get("include_keywords", [])),
        "exclude_keywords": list(config.get("exclude_keywords", [])),
        "include_categories": list(config.get("include_categories", [])),
        "exclude_categories": list(config.get("exclude_categories", [])),
        "require_all_keywords": bool(config.get("require_all_keywords", False)),
        "min_price_uah": None,
        "max_price_uah": None,
        "paused": False,
    }


def default_state() -> dict:
    return {"telegram_offset": 0, "chats": {}}


def load_state(path: Path) -> dict:
    if not path.exists():
        return default_state()
    try:
        state = load_json(path)
    except Exception:
        return default_state()
    if "chats" not in state:
        state["chats"] = {}
    if "telegram_offset" not in state:
        state["telegram_offset"] = 0
    return state


def save_state(path: Path, state: dict) -> None:
    save_json(path, state)
    ensure_private_file(path)


def get_allowed_chat_ids(config: dict) -> List[str]:
    allowed = [str(config.get("telegram_chat_id", "")).strip()]
    allowed.extend(str(item).strip() for item in config.get("allowed_chat_ids", []) if str(item).strip())
    return sorted({item for item in allowed if item})


def get_chat_state(state: dict, chat_id: str, config: dict) -> dict:
    chats = state.setdefault("chats", {})
    if chat_id not in chats:
        chats[chat_id] = {
            "settings": default_chat_settings(config),
            "seen_guids": [],
            "pending_action": None,
            "category_picker": {"target": None, "selected_category": None},
        }
    chat_state = chats[chat_id]
    chat_state.setdefault("settings", default_chat_settings(config))
    chat_state.setdefault("seen_guids", [])
    chat_state.setdefault("pending_action", None)
    chat_state.setdefault("category_picker", {"target": None, "selected_category": None})
    return chat_state


def menu_with_pause(chat_state: dict) -> List[List[str]]:
    menu = [row[:] for row in MAIN_MENU]
    menu[1][1] = "Возобновить" if chat_state["settings"].get("paused") else "Пауза"
    return menu


def short_list(items: Sequence[str]) -> str:
    return ", ".join(items) if items else "не заданы"


def format_budget(settings: dict) -> str:
    min_price = settings.get("min_price_uah")
    max_price = settings.get("max_price_uah")
    if min_price is None and max_price is None:
        return "любой"
    if min_price is not None and max_price is not None:
        return f"от {min_price} до {max_price} грн"
    if min_price is not None:
        return f"от {min_price} грн"
    return f"до {max_price} грн"


def normalize_budget_bounds(settings: dict) -> None:
    min_price = settings.get("min_price_uah")
    max_price = settings.get("max_price_uah")
    if min_price is not None:
        settings["min_price_uah"] = max(0, int(min_price))
    if max_price is not None:
        settings["max_price_uah"] = max(0, int(max_price))
    min_price = settings.get("min_price_uah")
    max_price = settings.get("max_price_uah")
    if min_price is not None and max_price is not None and min_price > max_price:
        settings["min_price_uah"], settings["max_price_uah"] = max_price, min_price


def shift_budget(settings: dict, field: str, delta: int) -> None:
    current = settings.get(field)
    if current is None:
        current = 0
    settings[field] = max(0, int(current) + delta)
    normalize_budget_bounds(settings)


def apply_quick_budget(settings: dict, preset: str) -> None:
    mapping = {
        "до 1000": (None, 1000),
        "1000-5000": (1000, 5000),
        "5000-15000": (5000, 15000),
        "15000-30000": (15000, 30000),
        "от 30000": (30000, None),
        "любой бюджет": (None, None),
    }
    min_price, max_price = mapping[preset]
    settings["min_price_uah"] = min_price
    settings["max_price_uah"] = max_price


def format_status(chat_state: dict) -> str:
    settings = chat_state["settings"]
    pause_status = "включена" if settings.get("paused") else "выключена"
    include_categories = settings.get("include_categories", [])
    categories_line = short_list(include_categories) if include_categories else "все категории"
    keyword_mode = "по категориям" if include_categories else "по ключевым словам"
    keywords_line = short_list(settings.get("include_keywords", []))
    return "\n".join(
        [
            "Текущие настройки",
            "",
            f"Уведомления: {pause_status}",
            f"Режим поиска: {keyword_mode}",
            "",
            "Категории",
            f"include: {categories_line}",
            f"exclude: {short_list(settings.get('exclude_categories', []))}",
            "",
            "Ключевые слова",
            f"include: {keywords_line}",
            f"exclude: {short_list(settings.get('exclude_keywords', []))}",
            "",
            f"Сохранено подходящих заказов: {len(chat_state.get('seen_guids', []))}",
        ]
    )


def category_help_text(config: dict) -> str:
    categories = collect_site_categories(config)
    preview_lines = []
    for category in categories[:8]:
        preview_lines.append(f"- {category.name}")
        for spec in category.specializations[:4]:
            preview_lines.append(f"  {spec}")
    return "\n".join(preview_lines)


def set_pending(chat_state: dict, action: Optional[str]) -> None:
    chat_state["pending_action"] = action


def reset_category_picker(chat_state: dict) -> None:
    chat_state["category_picker"] = {"target": None, "selected_category": None}


def append_unique(target_list: List[str], value: str) -> None:
    if value not in target_list:
        target_list.append(value)


def categories_root_keyboard(categories: List[SiteCategory]) -> List[List[str]]:
    keyboard: List[List[str]] = []
    for category in categories:
        keyboard.append([category.name])
    keyboard.append(["Назад"])
    return keyboard


def category_specs_keyboard(category: SiteCategory) -> List[List[str]]:
    keyboard: List[List[str]] = [["Добавить всю категорию"]]
    for spec in category.specializations:
        keyboard.append([spec])
    keyboard.append(["К списку категорий", "Назад"])
    return keyboard


def find_category_by_name(categories: List[SiteCategory], text: str) -> Optional[SiteCategory]:
    normalized = normalize_text(text)
    for category in categories:
        if normalize_text(category.name) == normalized:
            return category
    return None


def find_spec_in_category(category: SiteCategory, text: str) -> Optional[str]:
    normalized = normalize_text(text)
    for spec in category.specializations:
        if normalize_text(spec) == normalized:
            return spec
    return None


def open_category_picker(config: dict, chat_id: str, chat_state: dict, target: str) -> None:
    categories = collect_site_categories(config)
    picker = chat_state["category_picker"]
    picker["target"] = target
    picker["selected_category"] = None
    send_telegram_message(
        config,
        chat_id,
        f"Выберите категорию для списка {target}. Это точные названия с freelance.ua.",
        keyboard=categories_root_keyboard(categories),
    )


def handle_category_picker_message(config: dict, chat_id: str, chat_state: dict, text: str) -> bool:
    picker = chat_state.get("category_picker") or {}
    target = picker.get("target")
    if not target:
        return False

    categories = collect_site_categories(config)
    selected_name = picker.get("selected_category")
    if normalize_text(text) == "назад":
        reset_category_picker(chat_state)
        handle_categories_menu(config, chat_id)
        return True
    if normalize_text(text) == "к списку категорий":
        picker["selected_category"] = None
        send_telegram_message(config, chat_id, "Выберите категорию.", keyboard=categories_root_keyboard(categories))
        return True

    if not selected_name:
        category = find_category_by_name(categories, text)
        if category is None:
            send_telegram_message(config, chat_id, "Выберите категорию кнопкой из списка.", keyboard=categories_root_keyboard(categories))
            return True
        picker["selected_category"] = category.name
        send_telegram_message(
            config,
            chat_id,
            f"Категория: {category.name}\nТеперь выберите точную специализацию или добавьте всю категорию.",
            keyboard=category_specs_keyboard(category),
        )
        return True

    category = find_category_by_name(categories, selected_name)
    if category is None:
        reset_category_picker(chat_state)
        handle_categories_menu(config, chat_id)
        return True

    settings_key = "include_categories" if target == "include" else "exclude_categories"
    if normalize_text(text) == "добавить всю категорию":
        append_unique(chat_state["settings"][settings_key], category.name)
        send_telegram_message(
            config,
            chat_id,
            f"Добавлено в {target}: {category.name}",
            keyboard=category_specs_keyboard(category),
        )
        return True

    spec = find_spec_in_category(category, text)
    if spec is not None:
        append_unique(chat_state["settings"][settings_key], spec)
        send_telegram_message(
            config,
            chat_id,
            f"Добавлено в {target}: {spec}",
            keyboard=category_specs_keyboard(category),
        )
        return True

    send_telegram_message(
        config,
        chat_id,
        "Выберите специализацию кнопкой из списка.",
        keyboard=category_specs_keyboard(category),
    )
    return True


def parse_budget_input(text: str) -> Tuple[Optional[int], Optional[int]]:
    stripped = normalize_text(text)
    if stripped in {"любой", "any", "none", "сброс"}:
        return None, None
    parts = re.findall(r"\d+", stripped)
    if not parts:
        raise ValueError("Нужно прислать числа. Пример: 1000 15000")
    if len(parts) == 1:
        return int(parts[0]), None
    return int(parts[0]), int(parts[1])


def apply_pending_action(config: dict, chat_id: str, chat_state: dict, text: str) -> Optional[str]:
    action = chat_state.get("pending_action")
    if not action:
        return None

    if normalize_text(text) in {"назад", "/cancel", "cancel", "меню", "/start", "/menu", "статус", "помощь"}:
        set_pending(chat_state, None)
        return "Ввод отменен."

    settings = chat_state["settings"]
    values = split_user_list(text)

    if action == "set_include_keywords":
        settings["include_keywords"] = values
        set_pending(chat_state, None)
        return f"Include обновлен: {short_list(values)}"
    if action == "set_exclude_keywords":
        settings["exclude_keywords"] = values
        set_pending(chat_state, None)
        return f"Exclude обновлен: {short_list(values)}"
    if action == "set_include_categories":
        settings["include_categories"] = values
        set_pending(chat_state, None)
        return f"Include категории обновлены: {short_list(values)}"
    if action == "set_exclude_categories":
        settings["exclude_categories"] = values
        set_pending(chat_state, None)
        return f"Exclude категории обновлены: {short_list(values)}"
    if action == "set_budget":
        try:
            min_price, max_price = parse_budget_input(text)
        except ValueError:
            return "Нужен бюджет в формате: 1000 15000. Одно число = только минимум. Для отмены нажмите Назад."
        settings["min_price_uah"] = min_price
        settings["max_price_uah"] = max_price
        normalize_budget_bounds(settings)
        set_pending(chat_state, None)
        return f"Бюджет обновлен: {format_budget(settings)}"

    set_pending(chat_state, None)
    return "Неизвестное действие. Попробуйте еще раз."


def send_main_menu(config: dict, chat_id: str, chat_state: dict, intro: Optional[str] = None) -> None:
    text = intro or "\n".join(
        [
            "Главное меню",
            "",
            "Здесь можно быстро проверить заказы, настроить категории и поставить уведомления на паузу.",
        ]
    )
    send_telegram_message(config, chat_id, text, keyboard=menu_with_pause(chat_state))


def handle_keywords_menu(config: dict, chat_id: str, chat_state: dict) -> None:
    send_telegram_message(
        config,
        chat_id,
        "\n".join(
            [
                "Ключевые слова",
                "",
                "Include ищутся в названии, описании и категории заказа.",
                "Exclude отсекают ненужные заявки.",
            ]
        ),
        keyboard=KEYWORDS_MENU,
    )


def handle_categories_menu(config: dict, chat_id: str) -> None:
    send_telegram_message(
        config,
        chat_id,
        "\n".join(
            [
                "Категории",
                "",
                "Лучше использовать выбор из списка сайта.",
                "Ручной ввод тоже доступен, если нужно вставить сразу несколько значений.",
            ]
        ),
        keyboard=CATEGORIES_MENU,
    )


def handle_budget_menu(config: dict, chat_id: str) -> None:
    send_telegram_message(
        config,
        chat_id,
        "\n".join(
            [
                "Бюджет",
                "",
                "Можно выбрать готовый диапазон, двигать минимум и максимум кнопками или ввести значения вручную.",
            ]
        ),
        keyboard=BUDGET_MENU,
    )


def current_matches_for_chat(orders: List[Order], chat_state: dict) -> List[Order]:
    return [order for order in orders if order_matches(order, chat_state["settings"])]


def send_manual_check(config: dict, chat_id: str, chat_state: dict, orders: List[Order]) -> None:
    matches = current_matches_for_chat(orders, chat_state)
    if not matches:
        send_telegram_message(config, chat_id, "Сейчас подходящих заказов нет.", keyboard=menu_with_pause(chat_state))
        return

    send_telegram_message(
        config,
        chat_id,
        f"Сейчас найдено {len(matches)} подходящих заказов. Показываю до 3 последних.",
        keyboard=menu_with_pause(chat_state),
    )
    for order in matches[:3]:
        send_telegram_message(config, chat_id, format_order_message(order))


def process_message(config: dict, state: dict, chat_id: str, text: str) -> List[str]:
    allowed = get_allowed_chat_ids(config)
    replies: List[str] = []
    if chat_id not in allowed:
        return replies

    chat_state = get_chat_state(state, chat_id, config)
    if handle_category_picker_message(config, chat_id, chat_state, text):
        return ["save"]
    pending_reply = apply_pending_action(config, chat_id, chat_state, text)
    if pending_reply is not None:
        send_telegram_message(config, chat_id, pending_reply, keyboard=menu_with_pause(chat_state))
        return ["save"]

    normalized = normalize_text(text)
    settings = chat_state["settings"]

    if normalized in {"/start", "/menu", "меню"}:
        send_main_menu(config, chat_id, chat_state, intro="Бот готов. Все настройки доступны прямо в Telegram.")
        return ["save"]
    if normalized in {"/help", "помощь"}:
        send_telegram_message(
            config,
            chat_id,
            "\n".join(
                [
                    "Как пользоваться ботом",
                    "",
                    "Категории: лучше выбирать из списка сайта.",
                    "Если категории не заданы, бот показывает все новые заказы.",
                    "Кнопка Проверить показывает текущие совпадения сразу.",
                    "Пауза: временно отключает уведомления.",
                    "Отмена ввода: /cancel или Назад.",
                ]
            ),
            keyboard=menu_with_pause(chat_state),
        )
        return ["save"]
    if normalized in {"/status", "статус"}:
        send_telegram_message(config, chat_id, format_status(chat_state), keyboard=menu_with_pause(chat_state))
        return ["save"]
    if normalized in {"/pause", "пауза"}:
        settings["paused"] = True
        send_telegram_message(config, chat_id, "Уведомления поставлены на паузу.", keyboard=menu_with_pause(chat_state))
        return ["save"]
    if normalized in {"/resume", "возобновить"}:
        settings["paused"] = False
        send_telegram_message(config, chat_id, "Уведомления снова включены.", keyboard=menu_with_pause(chat_state))
        return ["save"]
    if normalized in {"/cancel", "назад"}:
        set_pending(chat_state, None)
        send_main_menu(config, chat_id, chat_state, intro="Вернулись в главное меню.")
        return ["save"]
    if normalized in {"ключевые слова", "ключевые"}:
        send_telegram_message(
            config,
            chat_id,
            "Фильтр по ключевым словам сейчас отключен. Бот ориентируется на категории или показывает все новые заказы, если категории не заданы.",
            keyboard=menu_with_pause(chat_state),
        )
        return ["save"]
    if normalized == "категории":
        handle_categories_menu(config, chat_id)
        return ["save"]
    if normalized == "показать категории":
        send_telegram_message(
            config,
            chat_id,
            "\n".join(
                [
                    "Категории",
                    f"Include: {short_list(settings.get('include_categories', []))}",
                    f"Exclude: {short_list(settings.get('exclude_categories', []))}",
                ]
            ),
            keyboard=CATEGORIES_MENU,
        )
        return ["save"]
    if normalized in {"список категорий", "список с сайта"}:
        send_telegram_message(
            config,
            chat_id,
            "Категории и специализации с сайта:\n\n" + category_help_text(config),
            keyboard=CATEGORIES_MENU,
        )
        return ["save"]
    if normalized in {"выбрать include категории", "выбрать include"}:
        open_category_picker(config, chat_id, chat_state, "include")
        return ["save"]
    if normalized in {"выбрать exclude категории", "выбрать exclude"}:
        open_category_picker(config, chat_id, chat_state, "exclude")
        return ["save"]
    if normalized in {"задать include категории", "ввести include"}:
        set_pending(chat_state, "set_include_categories")
        send_telegram_message(
            config,
            chat_id,
            "Отправьте include категории или специализации через запятую.",
            keyboard=CATEGORIES_MENU,
        )
        return ["save"]
    if normalized in {"задать exclude категории", "ввести exclude"}:
        set_pending(chat_state, "set_exclude_categories")
        send_telegram_message(
            config,
            chat_id,
            "Отправьте exclude категории или специализации через запятую.",
            keyboard=CATEGORIES_MENU,
        )
        return ["save"]
    if normalized == "очистить категории":
        settings["include_categories"] = []
        settings["exclude_categories"] = []
        send_telegram_message(config, chat_id, "Фильтр категорий очищен.", keyboard=CATEGORIES_MENU)
        return ["save"]
    if normalized == "бюджет":
        send_telegram_message(
            config,
            chat_id,
            "Фильтр по бюджету сейчас отключен. Бот не отсекает заказы по сумме.",
            keyboard=menu_with_pause(chat_state),
        )
        return ["save"]
    if normalized in {"/check", "проверить сейчас", "проверить"}:
        return ["manual_check", "save"]

    send_telegram_message(
        config,
        chat_id,
        "Не понял сообщение. Используйте меню ниже или /help.",
        keyboard=menu_with_pause(chat_state),
    )
    return ["save"]


def process_updates(config: dict, state: dict, state_path: Path) -> List[str]:
    actions: List[str] = []
    updates = get_updates(config, int(state.get("telegram_offset", 0)))
    if not updates:
        return actions

    for update in updates:
        state["telegram_offset"] = max(int(state.get("telegram_offset", 0)), int(update["update_id"]) + 1)
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        text = str(message.get("text") or "").strip()
        chat_id = str(chat.get("id") or "")
        if not text or not chat_id:
            continue
        actions.extend(process_message(config, state, chat_id, text))

    save_state(state_path, state)
    return actions


def trim_seen_guids(guids: List[str]) -> List[str]:
    if len(guids) <= MAX_SEEN_GUIDS:
        return guids
    return guids[-MAX_SEEN_GUIDS:]


def notify_new_orders(config: dict, state: dict, orders: List[Order], manual_chat_id: Optional[str] = None) -> None:
    for chat_id in get_allowed_chat_ids(config):
        chat_state = get_chat_state(state, chat_id, config)
        settings = chat_state["settings"]
        if settings.get("paused") and manual_chat_id != chat_id:
            continue

        matches = current_matches_for_chat(orders, chat_state)
        seen = set(chat_state.get("seen_guids", []))

        if manual_chat_id == chat_id:
            send_manual_check(config, chat_id, chat_state, orders)
            continue

        if not seen:
            chat_state["seen_guids"] = trim_seen_guids([order.guid for order in matches])
            continue

        fresh_matches = [order for order in matches if order.guid not in seen]
        for order in fresh_matches:
            send_telegram_message(config, chat_id, format_order_message(order))
            seen.add(order.guid)
        chat_state["seen_guids"] = trim_seen_guids(list(seen))


def run_once(config: dict, state_path: Path, dry_run: bool = False) -> int:
    state = load_state(state_path)
    orders = collect_orders(config)
    if dry_run:
        for chat_id in get_allowed_chat_ids(config):
            chat_state = get_chat_state(state, chat_id, config)
            matches = current_matches_for_chat(orders, chat_state)
            print("=" * 80)
            print(f"Chat {chat_id}: найдено {len(matches)} совпадений")
            for order in matches[:5]:
                print(format_order_message(order))
                print("-" * 60)
        return 0

    notify_new_orders(config, state, orders)
    save_state(state_path, state)
    return 0


def test_telegram(config: dict) -> int:
    temp_state = {"settings": default_chat_settings(config), "seen_guids": [], "pending_action": None}
    send_main_menu(config, str(config["telegram_chat_id"]), temp_state, intro="Тестовое сообщение. Главное меню уже подключено.")
    print("Тестовое сообщение отправлено.")
    return 0


def default_launch_agent_paths() -> Tuple[Path, Path]:
    home = Path.home()
    return home / "Library" / "LaunchAgents" / f"{DEFAULT_LABEL}.plist", DEFAULT_RUNTIME_LINK / "notifier.log"


def ensure_runtime_link(script_dir: Path) -> Path:
    if DEFAULT_RUNTIME_LINK.is_symlink() or DEFAULT_RUNTIME_LINK.exists():
        if DEFAULT_RUNTIME_LINK.resolve() == script_dir.resolve():
            return DEFAULT_RUNTIME_LINK
        if DEFAULT_RUNTIME_LINK.is_symlink():
            DEFAULT_RUNTIME_LINK.unlink()
        else:
            raise RuntimeError(f"Путь {DEFAULT_RUNTIME_LINK} уже занят. Удалите его вручную и повторите.")
    DEFAULT_RUNTIME_LINK.symlink_to(script_dir, target_is_directory=True)
    return DEFAULT_RUNTIME_LINK


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def write_runner_script(script_path: Path, config_path: Path) -> Path:
    runner_path = script_path.parent / "run_notifier.sh"
    python_path = Path(sys.executable).resolve()
    runner_path.write_text(
        "\n".join(
            [
                "#!/bin/zsh",
                "set -euo pipefail",
                f"cd {shell_quote(str(script_path.parent))}",
                f"exec {shell_quote(str(python_path))} {shell_quote(str(script_path))} --config {shell_quote(str(config_path))}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    os.chmod(runner_path, 0o700)
    return runner_path


def build_launch_agent(script_path: Path, log_path: Path) -> dict:
    runner_path = script_path.parent / "run_notifier.sh"
    return {
        "Label": DEFAULT_LABEL,
        "ProgramArguments": ["/bin/zsh", str(runner_path)],
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(script_path.parent),
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "ProcessType": "Background",
    }


def install_launch_agent(config_path: Path) -> int:
    script_path = Path(__file__).resolve()
    runtime_dir = ensure_runtime_link(script_path.parent)
    runtime_script = runtime_dir / script_path.name
    runtime_config = runtime_dir / config_path.name
    plist_path, log_path = default_launch_agent_paths()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    write_runner_script(runtime_script, runtime_config)
    plist = build_launch_agent(runtime_script, log_path)
    with plist_path.open("wb") as fh:
        plistlib.dump(plist, fh)
    ensure_private_file(plist_path)
    log_path.touch(exist_ok=True)
    ensure_private_file(log_path)
    os.system(f"launchctl bootout gui/$(id -u) '{plist_path}' >/dev/null 2>&1 || true")
    exit_code = os.system(f"launchctl bootstrap gui/$(id -u) '{plist_path}'")
    if exit_code != 0:
        print(f"Plist создан: {plist_path}")
        print("Не удалось автоматически загрузить сервис. Загрузите вручную:")
        print(f"launchctl bootstrap gui/$(id -u) '{plist_path}'")
        return 1
    print(f"Автозапуск установлен: {plist_path}")
    print(f"Лог: {log_path}")
    return 0


def uninstall_launch_agent() -> int:
    plist_path, _ = default_launch_agent_paths()
    if plist_path.exists():
        os.system(f"launchctl bootout gui/$(id -u) '{plist_path}' >/dev/null 2>&1 || true")
        plist_path.unlink(missing_ok=True)
    if DEFAULT_RUNTIME_LINK.is_symlink():
        DEFAULT_RUNTIME_LINK.unlink()
    print("Автозапуск удален.")
    return 0


def validate_config(config: dict, require_telegram: bool = True) -> None:
    required_fields = []
    if require_telegram:
        required_fields = ["telegram_bot_token", "telegram_chat_id"]
    missing = [field for field in required_fields if not str(config.get(field, "")).strip()]
    if missing:
        raise ValueError(f"Missing required config values: {', '.join(missing)}")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Telegram bot for freelance.ua monitoring")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to JSON config")
    parser.add_argument("--state", default=str(DEFAULT_STATE_PATH), help="Path to state JSON")
    parser.add_argument("--once", action="store_true", help="Run a single check and exit")
    parser.add_argument("--dry-run", action="store_true", help="Print matches instead of sending messages")
    parser.add_argument("--test-telegram", action="store_true", help="Send a test message with menu")
    parser.add_argument("--list-categories", action="store_true", help="Print categories and specializations from freelance.ua")
    parser.add_argument("--install-launch-agent", action="store_true", help="Install launchd service")
    parser.add_argument("--uninstall-launch-agent", action="store_true", help="Remove launchd service")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    config_path = Path(args.config).expanduser().resolve()
    state_override = env_str("STATE_PATH")
    state_path = Path(state_override or args.state).expanduser().resolve()

    if args.uninstall_launch_agent:
        return uninstall_launch_agent()

    try:
        config = load_json(config_path) if config_path.exists() else {}
        config = config_from_env(config)
        needs_telegram = not args.dry_run or args.test_telegram or args.install_launch_agent
        validate_config(config, require_telegram=needs_telegram)
    except Exception as exc:
        print(f"Ошибка конфига: {exc}", file=sys.stderr)
        return 1

    if config_path.exists():
        ensure_private_file(config_path)

    if args.list_categories:
        try:
            return print_categories(config)
        except Exception as exc:
            print(f"Не удалось получить категории: {exc}", file=sys.stderr)
            return 1

    if args.install_launch_agent:
        return install_launch_agent(config_path)

    if args.test_telegram:
        try:
            return test_telegram(config)
        except Exception as exc:
            print(f"Не удалось отправить тестовое сообщение: {exc}", file=sys.stderr)
            return 1

    if args.once:
        try:
            return run_once(config, state_path, dry_run=args.dry_run)
        except Exception as exc:
            print(f"Ошибка проверки заказов: {exc}", file=sys.stderr)
            return 1

    state = load_state(state_path)
    maybe_start_health_server()
    try:
        set_telegram_commands(config)
    except Exception as exc:
        print(f"[warn] Не удалось обновить команды бота: {exc}", file=sys.stderr)

    orders_interval = int(config.get("poll_interval_seconds", 180))
    next_orders_check = 0.0

    print(f"Запущен Telegram-бот и мониторинг freelance.ua. Интервал заказов: {orders_interval} сек.")
    while True:
        try:
            actions = process_updates(config, state, state_path)
            if "manual_check" in actions:
                orders = collect_orders(config)
                for chat_id in get_allowed_chat_ids(config):
                    chat_state = get_chat_state(state, chat_id, config)
                    if "manual_check" in actions:
                        send_manual_check(config, chat_id, chat_state, orders)
                save_state(state_path, state)
            now = time.monotonic()
            if now >= next_orders_check:
                orders = collect_orders(config)
                notify_new_orders(config, state, orders)
                save_state(state_path, state)
                next_orders_check = now + orders_interval
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(f"[warn] {exc}", file=sys.stderr)
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
