# HAPilot Add-ons Repository

Universal Telegram bot for Home Assistant — add-on edition.

## Установка репозитория в Home Assistant

1. Settings → Add-ons → ⋮ → Repositories
2. Добавить URL: `https://github.com/grizlygreen/hapilot-addons`
3. Refresh
4. Появится **HAPilot** в списке add-on'ов
5. Install → Configuration → ввести Telegram bot token + user_ids → Start

## Add-ons in this repository

- **HAPilot** — Telegram-бот с авто-discovery всех areas/devices/entities из HA. Меню формируется на лету. Подходит для любого инстанса HA без per-instance настройки.

## Killer feature

HA не нужен публичный доступ. Бот делает long-poll к Telegram (исходящий HTTPS), HA ходит через `http://supervisor/core/api`. Никаких портов наружу, ни TLS, ни VPN. Работает за NAT/CGNAT.
