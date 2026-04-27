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
   ```
   telegram_bot_token: ВАШ_ТОКЕН_ОТ_BOTFATHER
   allowed_user_ids: 123456789
   admin_user_ids: 123456789          (или несколько через запятую)
   instance_name: Дом
   ```
   Несколько id через запятую: `123456789,987654321`
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
| `allowed_user_ids` | string | Telegram user_id-ы через запятую: `27059994,123456` |
| `admin_user_ids` | string | Кто видит «⚙ Настройки». По умолчанию = первый из allowed. Админ неявно allowed. |
| `instance_name` | string | Имя дома, отображается в боте (по умолчанию "Дом") |
| `confirm_critical` | bool | Запрашивать подтверждение для замков и тревог |
| `cache_ttl_seconds` | int | Как часто бот опрашивает HA (30-600 сек) |
| `log_level` | enum | trace/debug/info/warning/error |
| `proxy_url` | string | Прокси к api.telegram.org если Telegram заблокирован у вашего провайдера. Поддерживается HTTP/HTTPS/SOCKS5. Примеры: `http://user:pass@1.2.3.4:8080`, `socks5://1.2.3.4:1080` |

## Если Telegram заблокирован у провайдера (РФ и др.)

Бот не сможет достучаться до `api.telegram.org` напрямую. Варианты:

1. **proxy_url в конфиге аддона** — простейший путь.  
   `socks5://your-vps:1080` или `http://your-vps:8080`

2. **Прозрачная маршрутизация на хосте** — направить весь трафик HA через VPN  
   (NetBird/WireGuard/etc). Тогда `proxy_url` не нужен, бот сам пойдёт через
   зашифрованный туннель.

3. **MTProto-прокси Telegram** — НЕ поддерживается напрямую, нужно дополнительная
   обвязка (gateway). Вариант (1) или (2) удобнее.

## Поддержка

См. репозиторий: https://github.com/grizlygreen/hapilot-addons

## Лицензия

MIT
