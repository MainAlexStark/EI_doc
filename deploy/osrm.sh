#!/usr/bin/env bash
#
# Подготовка карты для OSRM (маршруты «Выездов» по дорогам).
#
#   deploy/osrm.sh volga-fed-district    регион с download.geofabrik.de/russia/
#
# Скачивает .osm.pbf, готовит граф (extract → partition → customize) в томе
# osrmdata и перезапускает сервис osrm. Повторный запуск — обновление карты.
# Приволжский ФО — ~700 МБ, подготовка — 10–20 минут и до 6 ГБ памяти.

set -euo pipefail
cd "$(dirname "$0")/.."

REGION="${1:-volga-fed-district}"
URL="https://download.geofabrik.de/russia/${REGION}-latest.osm.pbf"
IMAGE="ghcr.io/project-osrm/osrm-backend:v5.27.1"
VOLUME="${COMPOSE_PROJECT_NAME:-ei-doc}_osrmdata"

SUDO=""
if [ "$(id -u)" != "0" ] && ! docker info >/dev/null 2>&1; then
  SUDO="sudo"
fi

$SUDO docker volume create "$VOLUME" >/dev/null
echo "== скачиваю $URL"
$SUDO docker run --rm -v "$VOLUME:/data" alpine:3.20 \
  sh -c "wget -q -O /data/region.osm.pbf '$URL'"
echo "== готовлю граф"
$SUDO docker run --rm -v "$VOLUME:/data" "$IMAGE" osrm-extract -p /opt/car.lua /data/region.osm.pbf
$SUDO docker run --rm -v "$VOLUME:/data" "$IMAGE" osrm-partition /data/region.osrm
$SUDO docker run --rm -v "$VOLUME:/data" "$IMAGE" osrm-customize /data/region.osrm
$SUDO docker compose --profile routing up -d osrm
echo "готово. В .env: LOGISTICS_OSRM_URL=http://osrm:5000 и ./deploy.sh update"
