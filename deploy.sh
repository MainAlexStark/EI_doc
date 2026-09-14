#!/usr/bin/env bash
#
# Развёртывание EI_doc на VPS и его обновление — тем же способом, что и
# MCP-серверы (ozon-seller-mcp, k8s-mcp): общий Caddy на машину, домен
# вписывается в его Caddyfile по маркерам, сервис ходит к нему по имени
# контейнера внутри mcp-network.
#
#   ./deploy.sh eidoc.example.com    развернуть или перенастроить на домен
#   ./deploy.sh update               обновить до свежего коммита
#
# Секреты (SECRET_KEY, пароль БД) придумываются один раз и не
# перевыпускаются при повторном запуске. Обновление откатывается само,
# если новая версия не отвечает на /healthz.

APP="ei-doc"
UPSTREAM="ei-doc-web"   # container_name web-сервиса в docker-compose.yml
PORT="8000"
APP_DNS_HINT="(например eidoc.example.com)"

USAGE_EXTRA='
При первом развёртывании создайте администратора:
  docker compose exec web python manage.py createsuperuser

Ключи ФГИС «Аршин» (ARSHIN_PUBLIC_KEY / ARSHIN_PRIVATE_KEY) для первого
релиза не обязательны — впишите их в .env позже, когда решится, какой
именно это доступ (см. docs/architecture.md).'

set -euo pipefail

cd "$(dirname "$0")"

ENV_FILE=".env"
INFRA_DIR="/opt/infrastructure"
CADDYFILE="${INFRA_DIR}/Caddyfile"

SUDO=""
if [ "$(id -u)" != "0" ] && ! docker info >/dev/null 2>&1; then
  SUDO="sudo"
fi

die()  { printf '\n%s: %s\n' "$APP" "$1" >&2; exit 1; }
say()  { printf '%s\n' "$1"; }
step() { printf '\n== %s\n' "$1"; }

gen_secret() {
  local n="${1:-32}"
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$n" | cut -c "1-$((n * 2))"
  else
    LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c "$((n * 2))"
  fi
}

env_get() {
  [ -f "$ENV_FILE" ] || return 0
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -1
}

env_set() {
  local key="$1" value="$2"
  touch "$ENV_FILE"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

# env_is_unset — пусто или всё ещё заглушка из .env.example
# («замените-меня»). Первый прогон копирует .env.example в .env, и там
# это значение уже непустое — проверка на "-n" его бы не заметила, и
# секрет НЕ придумался бы, оставшись тем же текстом на всех развёртываниях.
env_is_unset() {
  local value
  value="$(env_get "$1")"
  [ -z "$value" ] || [ "$value" = "замените-меня" ]
}

require_tools() {
  command -v docker >/dev/null 2>&1 || die \
    "docker не установлен. На Debian/Ubuntu: curl -fsSL https://get.docker.com | sh"
  $SUDO docker compose version >/dev/null 2>&1 || die \
    "нет плагина docker compose (docker-compose-plugin)"
  command -v curl >/dev/null 2>&1 || die "нужен curl"
  command -v git  >/dev/null 2>&1 || die "нужен git"
}

ensure_network() {
  if ! $SUDO docker network inspect mcp-network >/dev/null 2>&1; then
    say "создаю сеть mcp-network"
    $SUDO docker network create mcp-network >/dev/null
  fi
}

ensure_caddy() {
  if [ ! -f "${INFRA_DIR}/docker-compose.yml" ]; then
    say "поднимаю общий Caddy в ${INFRA_DIR}"
    $SUDO mkdir -p "$INFRA_DIR"
    $SUDO tee "${INFRA_DIR}/docker-compose.yml" >/dev/null <<'CADDY_COMPOSE'
# Общий обратный прокси для всех сервисов этой машины (MCP-серверы,
# EI_doc, …). Создан автоматически deploy.sh одного из них.
services:
  caddy:
    image: caddy:2-alpine
    container_name: caddy
    restart: unless-stopped

    ports:
      - "80:80"
      - "443:443"

    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy-data:/data
      - caddy-config:/config

    networks:
      - mcp-network

    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

networks:
  mcp-network:
    external: true

volumes:
  caddy-data:
  caddy-config:
CADDY_COMPOSE
  fi

  [ -f "$CADDYFILE" ] || $SUDO tee "$CADDYFILE" >/dev/null <<'CADDYFILE_HEAD'
# Общий Caddyfile этой машины. Блоки между маркерами
# «# >>> имя» и «# <<< имя» пишет deploy.sh соответствующего сервиса —
# правьте их там, иначе следующее развёртывание перезапишет правку.
CADDYFILE_HEAD
}

caddy_vhost() {
  local tmp
  tmp="$(mktemp)"

  if [ -f "$CADDYFILE" ]; then
    $SUDO awk -v app="$APP" '
      $0 == "# >>> " app { skip = 1 }
      skip != 1 { print }
      $0 == "# <<< " app { skip = 0 }
    ' "$CADDYFILE" > "$tmp"
  fi

  {
    printf '# >>> %s\n' "$APP"
    printf '%s {\n' "$DOMAIN"
    printf '    reverse_proxy %s:%s {\n' "$UPSTREAM" "$PORT"
    printf '        transport http {\n'
    printf '            response_header_timeout 120s\n'
    printf '        }\n'
    printf '    }\n'
    printf '}\n'
    printf '# <<< %s\n' "$APP"
  } >> "$tmp"

  $SUDO cp "$tmp" "$CADDYFILE"
  rm -f "$tmp"

  $SUDO docker compose -f "${INFRA_DIR}/docker-compose.yml" up -d >/dev/null
  $SUDO docker exec caddy caddy reload --config /etc/caddy/Caddyfile >/dev/null 2>&1 \
    || $SUDO docker restart caddy >/dev/null
}

wait_health() {
  local i code
  printf 'жду ответа по https://%s/healthz ' "$DOMAIN"
  for i in $(seq 1 45); do
    code="$(curl -fsS -m 5 -o /dev/null -w '%{http_code}' "https://${DOMAIN}/healthz" 2>/dev/null || true)"
    if [ "$code" = "200" ]; then
      printf ' есть\n'
      return 0
    fi
    printf '.'
    sleep 2
  done
  printf ' нет\n'
  return 1
}

compose_up() {
  if [ -n "$SUDO" ]; then
    sudo docker compose up -d --build
  else
    docker compose up -d --build
  fi
}

domain_from_env() {
  env_get "ALLOWED_HOSTS" | cut -d',' -f1
}

print_info() {
  cat <<INFO

  Адрес:         https://${DOMAIN}/
  Админка:       https://${DOMAIN}/admin/
  Схема API:     https://${DOMAIN}/api/docs/

Пароль БД и SECRET_KEY лежат в ${ENV_FILE} (права 600) — тот же файл,
что использует локальная разработка без Docker.
${USAGE_EXTRA}
INFO
}

cmd_install() {
  require_tools

  step "окружение"
  [ -f "$ENV_FILE" ] || {
    cp ".env.example" "$ENV_FILE"
    say "создан $ENV_FILE из примера"
  }
  chmod 600 "$ENV_FILE"

  env_set "DEBUG" "False"
  env_set "ALLOWED_HOSTS" "$DOMAIN"
  env_set "CSRF_TRUSTED_ORIGINS" "https://${DOMAIN}"

  env_set "DB_HOST" "postgres"
  env_set "DB_PORT" "5432"
  [ -n "$(env_get "DB_NAME")" ] || env_set "DB_NAME" "ei_doc"
  [ -n "$(env_get "DB_USER")" ] || env_set "DB_USER" "ei_doc"
  env_set "REDIS_URL" "redis://redis:6379/0"

  env_is_unset "SECRET_KEY" && {
    env_set "SECRET_KEY" "$(gen_secret 32)"
    say "сгенерирован SECRET_KEY"
  }
  env_is_unset "DB_PASSWORD" && {
    env_set "DB_PASSWORD" "$(gen_secret 16)"
    say "сгенерирован пароль БД"
  }

  step "инфраструктура"
  ensure_network
  ensure_caddy
  caddy_vhost
  say "домен ${DOMAIN} → ${UPSTREAM}:${PORT}"

  step "сервер"
  compose_up

  step "проверка"
  if ! wait_health; then
    say ""
    say "Сервер не ответил. Частые причины, по убыванию:"
    say "  1. DNS: ${DOMAIN} ещё не указывает на этот VPS (проверьте: dig +short ${DOMAIN})"
    say "  2. порты 80 и 443 закрыты — Let's Encrypt не смог выдать сертификат"
    say "  3. сервер упал на старте (миграции, коллектстатик) — смотрите ./deploy.sh logs"
    exit 1
  fi

  print_info
}

cmd_update() {
  require_tools
  [ -f "$ENV_FILE" ] || die "сервер здесь не развёрнут: сначала ./deploy.sh <домен>"

  DOMAIN="$(domain_from_env)"
  [ -n "$DOMAIN" ] || die "в $ENV_FILE не задан ALLOWED_HOSTS"

  local before
  before="$(git rev-parse HEAD)"

  step "обновление исходников"
  git pull --ff-only

  if [ "$(git rev-parse HEAD)" = "$before" ]; then
    say "уже последняя версия — пересобираю на всякий случай"
  fi

  step "пересборка"
  compose_up

  step "проверка"
  if wait_health; then
    say ""
    say "Обновлено до $(git rev-parse --short HEAD): $(git log -1 --pretty=%s)"
    return 0
  fi

  say ""
  say "Новая версия не отвечает — откатываюсь на ${before:0:12}"
  git reset --hard "$before" >/dev/null
  compose_up
  if wait_health; then
    die "обновление не удалось, вернулся прежний сервер. Смотрите ./deploy.sh logs"
  fi
  die "не отвечает и прежняя версия — дело не в коде. Смотрите ./deploy.sh logs"
}

cmd_status() {
  $SUDO docker compose ps
  DOMAIN="$(domain_from_env)"
  if [ -n "$DOMAIN" ]; then
    printf '\nhttps://%s/healthz → %s\n' "$DOMAIN" \
      "$(curl -fsS -m 5 -o /dev/null -w '%{http_code}' "https://${DOMAIN}/healthz" 2>/dev/null || echo "нет ответа")"
  fi
}

cmd_logs()    { $SUDO docker compose logs -f --tail 200; }
cmd_restart() { $SUDO docker compose restart; }
cmd_down()    { $SUDO docker compose down; }

cmd_secrets() {
  [ -f "$ENV_FILE" ] || die "сервер здесь не развёрнут"
  DOMAIN="$(domain_from_env)"
  print_info
}

usage() {
  cat <<USAGE
${APP} — развёртывание на VPS

  ./deploy.sh <домен>     развернуть или перенастроить на этот домен
  ./deploy.sh update      обновить до свежего коммита (с откатом при неудаче)
  ./deploy.sh status      что запущено и отвечает ли сервер
  ./deploy.sh logs        журнал сервера
  ./deploy.sh secrets     показать, где лежат пароли и адрес
  ./deploy.sh restart     перезапустить
  ./deploy.sh down        остановить

Перед первым запуском заведите A-запись ${APP_DNS_HINT} на адрес этого VPS.
${USAGE_EXTRA}
USAGE
}

case "${1:-}" in
  ""|-h|--help|help) usage ;;
  update)  cmd_update ;;
  status)  cmd_status ;;
  logs)    cmd_logs ;;
  secrets) cmd_secrets ;;
  restart) cmd_restart ;;
  down)    cmd_down ;;
  *)
    case "$1" in
      *.*) DOMAIN="$1" ;;
      *)   die "«$1» не похоже на домен и не является командой. ./deploy.sh --help" ;;
    esac
    cmd_install
    ;;
esac
