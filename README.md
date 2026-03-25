# freelance.ua Telegram bot

Скрипт проверяет публичные заказы на `freelance.ua` без входа в аккаунт, шлет уведомления в Telegram и позволяет настраивать фильтры прямо из чата с телефона.

## Что нужно

- `Python 3`
- Telegram-бот
- ваш `chat_id`

## Быстрый старт

1. Скопируйте `config.example.json` в `config.json`.
2. Заполните:
   - `telegram_bot_token`
   - `telegram_chat_id`
   - при желании `allowed_chat_ids`
   - списки `include_keywords` и `exclude_keywords`
   - списки `include_categories` и `exclude_categories`
   - `min_price_uah` и `max_price_uah`
   - при желании `require_all_keywords`
3. Проверьте Telegram:

```bash
python3 freelance_ua_notifier.py --config config.json --test-telegram
```

4. Проверьте фильтрацию без отправки сообщений:

```bash
python3 freelance_ua_notifier.py --config config.json --once --dry-run
```

5. Запустите бота и мониторинг:

```bash
python3 freelance_ua_notifier.py --config config.json
```

По умолчанию скрипт проверяет заказы каждые `180` секунд.
По умолчанию бот проверяет заказы каждые `60` секунд и просматривает `3` страницы ленты.

## Управление с телефона

После запуска откройте вашего Telegram-бота и используйте:

- `/start` или `/menu` — показать главное меню
- `Статус` — текущие фильтры
- `Ключевые слова` — include, exclude и режим `И/ИЛИ`
- `Категории` — настройка категорий и специализаций
- `Бюджет` — минимальный и максимальный бюджет, быстрые диапазоны и кнопки `+/-`
- `Пауза` — временно отключить уведомления
- `Проверить сейчас` — показать текущие совпадения сразу

Для ввода значений бот сам подсказывает формат. Обычно это список через запятую, например:

```text
python, telegram, parser
```

Для категорий теперь есть два варианта:

- `Выбрать include категории` / `Выбрать exclude категории` — точный выбор из реальных категорий и специализаций `freelance.ua`
- `Задать include категории` / `Задать exclude категории` — ручной ввод, если нужно вставить список сразу

Для бюджета есть три варианта:

- `Быстрый выбор бюджета` — готовые диапазоны
- кнопки `Мин +/-` и `Макс +/-` — быстро подвигать границы
- `Задать бюджет` — ввести числа вручную

## Настройка фильтров

Чтобы увидеть категории и специализации прямо как на `freelance.ua`:

```bash
python3 freelance_ua_notifier.py --config config.json --list-categories
```

Потом копируйте нужные названия в `config.json`.

Пример:

```json
"include_categories": [
  "Программирование",
  "Веб-программирование",
  "Разработка CRM и ERP"
],
"exclude_categories": [
  "Дизайн"
],
"min_price_uah": 1000,
"max_price_uah": 30000,
"require_all_keywords": false
```

Как работают фильтры:

- `include_keywords`: хотя бы одно слово должно встретиться в заголовке, описании или категории.
- `exclude_keywords`: если слово встретилось, заявка отбрасывается.
- `include_categories`: хотя бы одна категория или специализация должна совпасть.
- `exclude_categories`: исключает ненужные категории.
- `min_price_uah` и `max_price_uah`: бюджетный диапазон.
- `require_all_keywords`: если `true`, заявка должна содержать все слова из `include_keywords`.
- `allowed_chat_ids`: если нужно, можно перечислить дополнительные chat id, которым разрешено управлять ботом.
- `pages_to_scan`: сколько страниц ленты заказов бот просматривает при каждой проверке.

## Важное поведение

- Скрипт не входит в аккаунт и читает только публичные страницы.
- На первом запуске он запоминает уже найденные подходящие заказы и не шлет по ним уведомления.
- Потом присылает только новые совпадения.

Если хотите на первом запуске получить уведомления и по уже существующим подходящим заказам:

```bash
python3 freelance_ua_notifier.py --config config.json --once --notify-existing-on-first-run
```

## Как узнать `chat_id`

1. Создайте бота через `@BotFather`.
2. Напишите вашему боту любое сообщение.
3. Откройте в браузере:

```text
https://api.telegram.org/bot<ВАШ_ТОКЕН>/getUpdates
```

4. Найдите значение `chat -> id`.

## Как запускать постоянно

Надежный вариант для macOS через `launchd`:

```bash
python3 freelance_ua_notifier.py --config config.json --install-launch-agent
```

После этого мониторинг стартует сам и будет автоматически подниматься после перезагрузки.

Удалить автозапуск:

```bash
python3 freelance_ua_notifier.py --uninstall-launch-agent
```

Логи пишутся в файл [notifier.log](/Users/andrey/Documents/Новая папка/notifier.log).

Простой ручной вариант:

```bash
nohup python3 freelance_ua_notifier.py --config config.json > notifier.log 2>&1 &
```

Остановить можно так:

```bash
pkill -f freelance_ua_notifier.py
```

## Oracle Cloud

Для переноса на Oracle Cloud подготовлены файлы:

- [oracle/freelance-ua-bot.service](/Users/andrey/Documents/Новая папка/oracle/freelance-ua-bot.service)
- [oracle/config.oracle.example.json](/Users/andrey/Documents/Новая папка/oracle/config.oracle.example.json)
- [oracle/install_oracle.sh](/Users/andrey/Documents/Новая папка/oracle/install_oracle.sh)

На сервере бот удобнее всего запускать через `systemd`.

## Koyeb

Для Koyeb проект уже подготовлен:

- [Dockerfile](/Users/andrey/Documents/Новая папка/Dockerfile)
- [.dockerignore](/Users/andrey/Documents/Новая папка/.dockerignore)

Бот умеет:

- брать настройки из переменных окружения
- поднимать маленький HTTP health endpoint на порту из `PORT`
- хранить состояние в `STATE_PATH`

Минимальные переменные в Koyeb:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Рекомендуемые переменные:

- `POLL_INTERVAL_SECONDS=60`
- `PAGES_TO_SCAN=3`
- `INCLUDE_KEYWORDS=python,telegram,bot,парсер,parser,django,fastapi,api`
- `EXCLUDE_KEYWORDS=дизайн,логотип,smm`
- `INCLUDE_CATEGORIES=Программирование,Веб-программирование`
- `MIN_PRICE_UAH=500`
- `MAX_PRICE_UAH=` оставить пустым, если верхней границы нет

Как деплоить в Koyeb:

1. Загрузите проект в GitHub без `config.json`.
2. В Koyeb создайте `Web Service` из GitHub-репозитория.
3. Koyeb сам увидит [Dockerfile](/Users/andrey/Documents/Новая папка/Dockerfile).
4. Добавьте env vars из списка выше.
5. `Exposed port` оставьте по `PORT`.
6. После деплоя бот начнет long polling и будет доступен health-check.

Локально проверить env-режим можно так:

```bash
TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=123 python3 freelance_ua_notifier.py --config config.example.json --once --dry-run
```

## Безопасность

- Скрипт не входит в ваш аккаунт на `freelance.ua`.
- Бот читает RSS и несколько страниц самой ленты заказов, чтобы снизить риск пропуска новых публикаций.
- `config.json`, файл состояния и `launchd` plist автоматически переводятся в режим доступа только для вашего пользователя.
- Бот принимает команды только от `telegram_chat_id` и `allowed_chat_ids`.
- Если токен Telegram когда-либо попадал в переписку или на чужой экран, перевыпустите его через `@BotFather` и обновите `telegram_bot_token` в `config.json`.
