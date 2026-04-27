# HAPilot — Telegram bot для Home Assistant

Универсальный Telegram-бот, автоматически читает все areas/devices/entities из вашего HA и формирует меню на лету. Без per-instance настройки — работает на любом инстансе.

## Главное преимущество

**HA не нужен публичный доступ.** Бот сам ходит к `api.telegram.org` (long-poll), а к HA — через локальный supervisor API. Никаких портов наружу, не нужны домен / TLS / VPN. Работает за NAT и серым IP.

## Возможности

- Автодискавери комнат, устройств, сенсоров, сцен, скриптов
- Меню «По комнатам» с правильными иконками (🍳 кухня, 🛁 ванная, …)
- Авто-grid кнопок (1–3 в ряд по длине названий)
- Иконки + человеческие лейблы для бинарных датчиков (🚪 Закрыта / 🔥 ДЫМ! / 💧 Сухо / …)
- Раздел «Алерты» — активные тревоги, persistent notifications, недоступные устройства
- Quick actions: квитировать дым, перекрыть/открыть воду
- Камеры — снимок прямо в чат, заменяет текущее меню
- Edit-mode для админа — настройка видимости устройств в боте
- Whitelist user_ids — доступ только разрешённым

## Установка

1. Settings → Add-ons → Add-on Store → ⋮ → Repositories
2. Add: `https://github.com/grizlygreen/hapilot-addons`
3. Найти HAPilot → Install
4. Configuration:
   ```yaml
   telegram_bot_token: "ВАШ_ТОКЕН_ОТ_BOTFATHER"
   allowed_user_ids:
     - 123456789
   instance_name: "Дом"
   ```
5. Start

## Получение Telegram bot token

1. Открыть `@BotFather` в Telegram
2. `/newbot` → имя → username
3. Сохранить выданный токен

## Получение своего Telegram user_id

1. Открыть `@userinfobot` в Telegram
2. Он вернёт ваш `id`

## Конфигурация

| Опция | Тип | Описание |
|-------|-----|----------|
| `telegram_bot_token` | string | Токен бота от @BotFather |
| `allowed_user_ids` | list of int | Кто может пользоваться ботом |
| `admin_user_ids` | list of int | Кто видит «⚙ Настройки». По умолчанию = первый из allowed_user_ids |
| `instance_name` | string | Имя дома, отображается в боте (по умолчанию "Дом") |
| `confirm_critical` | bool | Запрашивать подтверждение для замков и тревог |
| `cache_ttl_seconds` | int | Как часто бот опрашивает HA (30-600 сек) |
| `log_level` | enum | trace/debug/info/warning/error |

## Поддержка

См. репозиторий: https://github.com/grizlygreen/hapilot-addons

## Лицензия

MIT
