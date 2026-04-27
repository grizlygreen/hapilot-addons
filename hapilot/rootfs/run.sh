#!/usr/bin/with-contenv bashio
set -e

bashio::log.info "Starting HAPilot…"

export TG_BOT_TOKEN="$(bashio::config 'telegram_bot_token')"
export INSTANCE_NAME="$(bashio::config 'instance_name')"
export CONFIRM_CRITICAL="$(bashio::config 'confirm_critical')"
export CACHE_TTL_SECONDS="$(bashio::config 'cache_ttl_seconds')"
export ALLOWED_USER_IDS="$(bashio::config 'allowed_user_ids')"
export ADMIN_USER_IDS="$(bashio::config 'admin_user_ids')"
export LOG_LEVEL="$(bashio::config 'log_level' | tr '[:lower:]' '[:upper:]')"
export TG_PROXY_URL="$(bashio::config 'proxy_url')"

if [ -n "$TG_PROXY_URL" ]; then
    bashio::log.info "Using proxy: $TG_PROXY_URL"
fi

# HA Core REST API через Supervisor proxy
export HA_URL="http://supervisor/core"
export HA_TOKEN="${SUPERVISOR_TOKEN}"

# Visibility persistence — в /addon_configs/<slug>/ который примаунтен как map
export VISIBILITY_PATH="/config/visibility.json"

if [ -z "$TG_BOT_TOKEN" ]; then
    bashio::log.fatal "telegram_bot_token не задан в Configuration"
    exit 1
fi

if [ -z "$ALLOWED_USER_IDS" ]; then
    bashio::log.warning "allowed_user_ids пуст — никто не сможет пользоваться ботом"
fi

bashio::log.info "instance: ${INSTANCE_NAME}"
bashio::log.info "allowed_users: ${ALLOWED_USER_IDS}"
bashio::log.info "admin_users: ${ADMIN_USER_IDS:-(default: первый из allowed)}"

cd /app
exec python3 -m src.main
