#!/usr/bin/env bash
#
# Резервные копии EI_doc: база (pg_dump) и загруженные файлы (том media).
#
#   deploy/backup.sh run             снять копию сейчас
#   deploy/backup.sh check           проверить, что последняя копия восстанавливается
#   deploy/backup.sh list            какие копии есть
#   deploy/backup.sh restore <папка> восстановить базу и файлы из копии (спросит)
#   deploy/backup.sh install-cron    каждую ночь копия, раз в квартал проверка
#
# Копии лежат в BACKUP_DIR (по умолчанию /var/backups/ei-doc), хранятся
# BACKUP_KEEP_DAYS дней (14). Если в .env задан RESTIC_REPOSITORY, каждая
# копия ещё уходит restic'ом в объектное хранилище (S3 / Yandex Object
# Storage): RESTIC_PASSWORD, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
# необязательно AWS_DEFAULT_REGION. Копия только на том же диске — это
# защита от ошибки, но не от потери сервера: restic настройте до запуска.
#
# Проверка восстановления разворачивает дамп во временную базу в том же
# контейнере postgres, сверяет число поверок и протоколов и удаляет её —
# боевая база не трогается.

set -euo pipefail

cd "$(dirname "$0")/.."

APP="ei-doc"
ENV_FILE=".env"
MEDIA_VOLUME="${COMPOSE_PROJECT_NAME:-ei-doc}_media"

die() { printf '\n%s backup: %s\n' "$APP" "$1" >&2; exit 1; }
say() { printf '%s\n' "$1"; }

env_get() {
  [ -f "$ENV_FILE" ] || return 0
  { grep -E "^$1=" "$ENV_FILE" || true; } | tail -n1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'
}

DB_NAME="$(env_get DB_NAME)"; DB_NAME="${DB_NAME:-ei_doc}"
DB_USER="$(env_get DB_USER)"; DB_USER="${DB_USER:-ei_doc}"
BACKUP_DIR="${BACKUP_DIR:-$(env_get BACKUP_DIR)}"; BACKUP_DIR="${BACKUP_DIR:-/var/backups/ei-doc}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-$(env_get BACKUP_KEEP_DAYS)}"; KEEP_DAYS="${KEEP_DAYS:-14}"

SUDO=""
if [ "$(id -u)" != "0" ] && ! docker info >/dev/null 2>&1; then
  SUDO="sudo"
fi

# Команды внутри контейнера postgres. BACKUP_PG_EXEC переопределяет обёртку
# (пустая строка — выполнять локально): так скрипт проверяется без Docker.
if [ -n "${BACKUP_PG_EXEC+x}" ]; then
  read -r -a PG_EXEC <<< "$BACKUP_PG_EXEC"
else
  PG_EXEC=(docker compose exec -T postgres)
  [ -z "$SUDO" ] || PG_EXEC=("$SUDO" "${PG_EXEC[@]}")
fi
pg() { "${PG_EXEC[@]}" "$@"; }

with_media() {
  [ "${BACKUP_SKIP_MEDIA:-}" = "1" ] && return 1
  $SUDO docker volume inspect "$MEDIA_VOLUME" >/dev/null 2>&1
}

latest() {
  find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name '20*' 2>/dev/null | sort | tail -n1
}

restic_enabled() { [ -n "$(env_get RESTIC_REPOSITORY)" ]; }

restic_run() {
  $SUDO docker run --rm \
    -e RESTIC_REPOSITORY="$(env_get RESTIC_REPOSITORY)" \
    -e RESTIC_PASSWORD="$(env_get RESTIC_PASSWORD)" \
    -e AWS_ACCESS_KEY_ID="$(env_get AWS_ACCESS_KEY_ID)" \
    -e AWS_SECRET_ACCESS_KEY="$(env_get AWS_SECRET_ACCESS_KEY)" \
    -e AWS_DEFAULT_REGION="$(env_get AWS_DEFAULT_REGION)" \
    -v "$BACKUP_DIR:/backups:ro" \
    -v "$APP-restic-cache:/root/.cache/restic" \
    restic/restic:0.17.3 "$@"
}

cmd_run() {
  local stamp target
  stamp="$(date +%Y%m%d-%H%M%S)"
  target="$BACKUP_DIR/$stamp"
  mkdir -p "$target"
  chmod 700 "$BACKUP_DIR"

  say "== база $DB_NAME → $target/db.dump"
  pg pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc --no-owner > "$target/db.dump.part"
  mv "$target/db.dump.part" "$target/db.dump"
  [ -s "$target/db.dump" ] || die "дамп пустой — копия не снята"

  if with_media; then
    say "== файлы ($MEDIA_VOLUME) → $target/media.tar.gz"
    $SUDO docker run --rm -v "$MEDIA_VOLUME:/media:ro" alpine:3.20 \
      tar czf - -C /media . > "$target/media.tar.gz"
  else
    say "== файлы пропущены (тома $MEDIA_VOLUME нет)"
  fi

  (cd "$target" && sha256sum ./* > SHA256SUMS)
  git rev-parse HEAD > "$target/commit" 2>/dev/null || true

  say "== ротация: удаляю копии старше $KEEP_DAYS дн."
  find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name '20*' -mtime "+$KEEP_DAYS" -exec rm -rf {} +

  if restic_enabled; then
    say "== restic → $(env_get RESTIC_REPOSITORY)"
    restic_run snapshots >/dev/null 2>&1 || restic_run init
    restic_run backup --tag "$APP" --host "$APP" "/backups/$stamp"
    restic_run forget --tag "$APP" --keep-daily 7 --keep-weekly 5 --keep-monthly 12 --prune
  else
    say "!! RESTIC_REPOSITORY не задан — копия только на этом диске"
  fi

  say "готово: $target ($(du -sh "$target" | cut -f1))"
}

cmd_check() {
  local dir="${1:-$(latest)}" check_db="${DB_NAME}_restore_check"
  [ -n "$dir" ] && [ -f "$dir/db.dump" ] || die "копий нет в $BACKUP_DIR — сначала deploy/backup.sh run"

  say "== контрольные суммы $dir"
  (cd "$dir" && sha256sum -c --quiet SHA256SUMS) || die "файлы копии повреждены"

  say "== восстанавливаю во временную базу $check_db"
  pg dropdb -U "$DB_USER" --if-exists "$check_db"
  pg createdb -U "$DB_USER" "$check_db"
  trap 'pg dropdb -U "$DB_USER" --if-exists "'"$check_db"'" >/dev/null 2>&1 || true' EXIT
  pg pg_restore -U "$DB_USER" -d "$check_db" --no-owner --exit-on-error < "$dir/db.dump"

  local migrations verifications protocols
  migrations="$(pg psql -U "$DB_USER" -d "$check_db" -tAc 'select count(*) from django_migrations')"
  verifications="$(pg psql -U "$DB_USER" -d "$check_db" -tAc 'select count(*) from verification_verification')"
  protocols="$(pg psql -U "$DB_USER" -d "$check_db" -tAc 'select count(*) from verification_protocol')"
  [ "${migrations:-0}" -gt 0 ] || die "в восстановленной базе нет миграций"

  if [ -f "$dir/media.tar.gz" ]; then
    gzip -t "$dir/media.tar.gz" || die "архив файлов повреждён"
  fi

  say "восстановление проходит: миграций $migrations, поверок $verifications, протоколов $protocols"
  if [ -n "${BACKUP_CHECK_STAMP:-}" ] || [ -w "$BACKUP_DIR" ]; then
    printf '%s %s поверок=%s протоколов=%s\n' "$(date -Is)" "$(basename "$dir")" "$verifications" "$protocols" \
      >> "$BACKUP_DIR/restore-checks.log"
  fi
}

cmd_list() {
  find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name '20*' 2>/dev/null | sort | while read -r dir; do
    printf '%s  %s\n' "$(du -sh "$dir" | cut -f1)" "$dir"
  done
  [ -f "$BACKUP_DIR/restore-checks.log" ] && { say ""; say "последние проверки:"; tail -n3 "$BACKUP_DIR/restore-checks.log"; }
  if restic_enabled; then say ""; restic_run snapshots --tag "$APP"; fi
}

cmd_restore() {
  local dir="${1:-}"
  [ -n "$dir" ] && [ -f "$dir/db.dump" ] || die "укажите папку копии: deploy/backup.sh restore $BACKUP_DIR/<дата>"
  (cd "$dir" && sha256sum -c --quiet SHA256SUMS) || die "файлы копии повреждены"

  say "Будет ЗАМЕНЕНА боевая база $DB_NAME содержимым $dir."
  say "Перед этим снимается свежая копия текущего состояния."
  if [ "${BACKUP_YES:-}" != "1" ]; then
    read -r -p "Напишите «восстановить», чтобы продолжить: " answer
    [ "$answer" = "восстановить" ] || die "отменено"
  fi

  cmd_run
  $SUDO docker compose stop web worker beat assistant-worker 2>/dev/null || true
  pg pg_restore -U "$DB_USER" -d "$DB_NAME" --clean --if-exists --no-owner < "$dir/db.dump"
  if [ -f "$dir/media.tar.gz" ] && with_media; then
    $SUDO docker run --rm -i -v "$MEDIA_VOLUME:/media" alpine:3.20 \
      sh -c 'rm -rf /media/* && tar xzf - -C /media' < "$dir/media.tar.gz"
  fi
  $SUDO docker compose up -d
  say "восстановлено из $dir"
}

cmd_install_cron() {
  local repo cron="/etc/cron.d/$APP-backup"
  repo="$(pwd)"
  $SUDO tee "$cron" >/dev/null <<CRON
# EI_doc: ночная копия и ежеквартальная проверка восстановления (deploy/backup.sh)
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
17 3 * * * root cd $repo && deploy/backup.sh run >> /var/log/$APP-backup.log 2>&1
43 4 1 1,4,7,10 * root cd $repo && deploy/backup.sh check >> /var/log/$APP-backup.log 2>&1
CRON
  $SUDO chmod 644 "$cron"
  say "расписание записано в $cron; журнал — /var/log/$APP-backup.log"
}

case "${1:-}" in
  run)          cmd_run ;;
  check)        cmd_check "${2:-}" ;;
  list)         cmd_list ;;
  restore)      cmd_restore "${2:-}" ;;
  install-cron) cmd_install_cron ;;
  *)            sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//' ;;
esac
