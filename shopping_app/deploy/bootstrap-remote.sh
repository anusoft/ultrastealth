#!/usr/bin/env bash
set -euo pipefail

SHOPPING_ROOT=${SHOPPING_ROOT:-/home/anu/shopping}
APP_ROOT=${SHOPPING_APP_ROOT:-${SHOPPING_ROOT}/app}
SHOPPING_HOME=/var/lib/shopping
BUN=${SHOPPING_HOME}/.bun/bin/bun

if ! id shopping >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "${SHOPPING_HOME}" \
    --shell /usr/sbin/nologin shopping
fi

apt-get update
apt-get install -y acl python3-venv zstd
if ! command -v pg_dump >/dev/null 2>&1; then
  apt-get install -y postgresql-client-17
fi

if [[ "${SHOPPING_ROOT}" == /home/* ]]; then
  setfacl -m u:shopping:--x "$(dirname "${SHOPPING_ROOT}")"
fi

install -d -o root -g root -m 0755 "${SHOPPING_ROOT}" "${APP_ROOT}"
for directory in data partial exports logs state; do
  install -d -o shopping -g shopping -m 0750 "${SHOPPING_ROOT}/${directory}"
done
install -d -o root -g shopping -m 0750 "${SHOPPING_ROOT}/imports"

if [[ ! -x "${BUN}" ]]; then
  runuser -u shopping -- env HOME="${SHOPPING_HOME}" \
    bash -c 'curl -fsSL https://bun.sh/install | bash -s -- bun-v1.3.14'
fi

install -o root -g root -m 0644 \
  "${APP_ROOT}/shopping_app/deploy/package.json" "${APP_ROOT}/package.json"
install -o root -g root -m 0644 \
  "${APP_ROOT}/shopping_app/deploy/bun.lock" "${APP_ROOT}/bun.lock"
install -d -o shopping -g shopping -m 0755 "${APP_ROOT}/node_modules"
chown -R shopping:shopping "${APP_ROOT}/node_modules"
runuser -u shopping -- env HOME="${SHOPPING_HOME}" \
  PATH="${SHOPPING_HOME}/.bun/bin:${PATH}" "${BUN}" install \
  --cwd "${APP_ROOT}" --production --no-progress --frozen-lockfile
runuser -u shopping -- env HOME="${SHOPPING_HOME}" \
  PATH="${SHOPPING_HOME}/.bun/bin:${PATH}" "${BUN}" run \
  --cwd "${APP_ROOT}/node_modules/scrapling-js" build

zstd --version
pg_dump --version | grep -E '^pg_dump \(PostgreSQL\) 17\.'
python3 -m venv "${APP_ROOT}/.venv"
"${APP_ROOT}/.venv/bin/python" -m pip install --disable-pip-version-check \
  --requirement "${APP_ROOT}/shopping_app/requirements.txt"

runuser -u postgres -- psql -X --set ON_ERROR_STOP=1 --dbname postgres <<'SQL'
DO $roles$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'shopping_owner') THEN
    CREATE ROLE shopping_owner NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'shopping') THEN
    CREATE ROLE shopping LOGIN;
  END IF;
END
$roles$;
SQL

if ! runuser -u postgres -- psql -X -Atqc \
  "SELECT 1 FROM pg_database WHERE datname = 'shopping'" postgres | grep -qx 1; then
  runuser -u postgres -- createdb --owner=shopping_owner shopping
fi
runuser -u postgres -- psql -X --set ON_ERROR_STOP=1 --dbname postgres \
  --command 'GRANT CONNECT ON DATABASE shopping TO shopping'

cd "${APP_ROOT}"
runuser -u postgres -- env \
  PYTHONPATH="${APP_ROOT}" \
  SHOPPING_DATABASE_URL='dbname=shopping host=/var/run/postgresql' \
  "${APP_ROOT}/.venv/bin/python" -m shopping_app.cli migrate

install -o root -g root -m 0644 \
  "${APP_ROOT}/shopping_app/systemd/shopping-scheduler.service" \
  /etc/systemd/system/shopping-scheduler.service
install -o root -g root -m 0644 \
  "${APP_ROOT}/shopping_app/systemd/shopping-scheduler.timer" \
  /etc/systemd/system/shopping-scheduler.timer
install -o root -g root -m 0644 \
  "${APP_ROOT}/shopping_app/systemd/shopping-crawl@.service" \
  /etc/systemd/system/shopping-crawl@.service

chown -R root:root "${APP_ROOT}"
chmod -R go-w "${APP_ROOT}"
systemctl daemon-reload

cd "${APP_ROOT}"
runuser -u shopping -- env HOME="${SHOPPING_HOME}" \
  "${BUN}" -e 'await import("scrapling-js")'

runuser -u shopping -- env \
  PYTHONPATH="${APP_ROOT}" \
  SHOPPING_DATABASE_URL='dbname=shopping host=/var/run/postgresql' \
  "${APP_ROOT}/.venv/bin/python" -m shopping_app.cli health
