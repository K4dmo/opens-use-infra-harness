#!/bin/bash
# OpenSUSE install helper. Run as root from the cloned repo.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="${INSTALL_DIR:-/opt/infra-harness}"
DATA_DIR="${DATA_DIR:-/var/lib/infra-harness}"
ENV_FILE="${ENV_FILE:-/etc/infra-harness.env}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

# SLES/openSUSE useradd --system does not create a matching group (unlike Debian -U).
# systemd Group=infra-agent and chown user:group both need the group to exist.
if ! getent group infra-agent >/dev/null; then
  groupadd --system infra-agent
fi
if ! id -u infra-agent >/dev/null 2>&1; then
  nologin=/usr/sbin/nologin
  [[ -x "$nologin" ]] || nologin=/sbin/nologin
  [[ -x "$nologin" ]] || nologin=/bin/false
  useradd --system --gid infra-agent --home "$DATA_DIR" --shell "$nologin" infra-agent
fi

mkdir -p "$INSTALL_DIR" "$DATA_DIR/backups"
cp -a "$ROOT/src" "$ROOT/prompts" "$ROOT/pyproject.toml" "$ROOT/setup.py" "$ROOT/requirements.txt" "$INSTALL_DIR/"
chown -R infra-agent:infra-agent "$INSTALL_DIR" "$DATA_DIR"

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ROOT/config.example.env" "$ENV_FILE"
  sed -i "s|^DATA_DIR=.*|DATA_DIR=$DATA_DIR|" "$ENV_FILE"
  chmod 0600 "$ENV_FILE"
  echo "Edit $ENV_FILE (OPENROUTER_API_KEY, DISCORD_WEBHOOK_URL) before starting."
fi

python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install -U pip setuptools
"$INSTALL_DIR/.venv/bin/pip" install -e "$INSTALL_DIR"
chown -R infra-agent:infra-agent "$INSTALL_DIR/.venv"

install -m 0440 "$ROOT/deploy/sudoers" /etc/sudoers.d/infra-harness
visudo -c -f /etc/sudoers.d/infra-harness

install -m 0644 "$ROOT/deploy/infra-harness.service" /etc/systemd/system/infra-harness.service
install -m 0644 "$ROOT/deploy/infra-harness.timer" /etc/systemd/system/infra-harness.timer
install -m 0644 "$ROOT/deploy/infra-harness-loop.service" /etc/systemd/system/infra-harness-loop.service
systemctl daemon-reload

echo "Installed. Fill $ENV_FILE, keep DRY_RUN=true, then:"
echo "  systemctl enable --now infra-harness.timer"
echo "Or a single test window:"
echo "  sudo -u infra-agent PYTHONPATH=$INSTALL_DIR/src $INSTALL_DIR/.venv/bin/python -m harness --once"
