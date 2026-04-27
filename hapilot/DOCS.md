# HAPilot

Universal Telegram bot для Home Assistant с автодискавери.

## Быстрый старт

1. Создать бота через `@BotFather` (`/newbot`)
2. Узнать свой Telegram user_id через `@userinfobot`
3. В Configuration:
   ```yaml
   telegram_bot_token: "1234:ABC..."
   allowed_user_ids: [123456789]
   ```
4. Start
5. В Telegram: `/start` боту

## Меню бота

```
🏠 Дом
├─ 📍 По комнатам          ← все ваши areas с иконками
├─ 📋 По типам              (планируется)
├─ 🚨 Алерты                активные тревоги, уведомления
└─ ⚙ Настройки              (только админ — видимость)
```

## Edit-mode

Админ может скрыть/показать любое устройство в меню:
1. ⚙ Настройки → 👁 Включить настройку видимости
2. Зайти по комнатам, тыкнуть ✅/🙈 у устройств
3. Выключить edit-mode

Override сохраняется в `/config/visibility.json`.

## Безопасность

- Все запросы проверяют user_id против `allowed_user_ids`
- Действия для замков и критических устройств требуют подтверждения
- Логи в HA UI → Add-ons → HAPilot → Log

## Известные ограничения

- Один HA = один бот (один экземпляр аддона)
- Изменения структуры (новые areas/entities) подхватываются через `cache_ttl_seconds`
- Камеры: snapshot, видео-стрим не поддерживается (Telegram limit)

## Логи

```
Settings → Add-ons → HAPilot → Log
```

## Troubleshooting

**Бот не отвечает на `/start`:**
- Проверь Log аддона на ошибки
- Убедись что в `allowed_user_ids` твой user_id
- Проверь что bot token валиден

**`HA сейчас недоступен`:**
- Перезапусти аддон
- Если не помогает — проверь что у аддона есть `homeassistant_api: true`
