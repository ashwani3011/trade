#!/usr/bin/env bash
# One-time setup of the paper trader on an Oracle Cloud (or any Ubuntu) VM.
# Run as the default 'ubuntu' user:
#   curl -fsSL https://raw.githubusercontent.com/ashwani3011/trade/claude/adoring-planck-88ci7e/deploy/oracle/setup.sh | bash
# Paper trading only: this installs the same trader GitHub Actions runs. It places no orders.
set -euo pipefail
REPO=ashwani3011/trade
BRANCH=claude/adoring-planck-88ci7e
DIR="$HOME/trade"
ENV_FILE="$HOME/.paper-trade.env"

sudo apt-get update -qq
sudo apt-get install -y -qq git python3 python3-venv python3-pip tzdata
sudo timedatectl set-timezone Asia/Kolkata

# deploy key so the server can push results (state/, reports/) back to GitHub
mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
[ -f "$HOME/.ssh/trade_deploy" ] || ssh-keygen -q -t ed25519 -N "" -C "paper-trade@oracle" -f "$HOME/.ssh/trade_deploy"
grep -q "Host github.com" "$HOME/.ssh/config" 2>/dev/null || cat >> "$HOME/.ssh/config" <<CFG
Host github.com
  IdentityFile ~/.ssh/trade_deploy
  IdentitiesOnly yes
CFG
ssh-keyscan -q github.com >> "$HOME/.ssh/known_hosts" 2>/dev/null

[ -d "$DIR/.git" ] || git clone -q -b "$BRANCH" "https://github.com/$REPO.git" "$DIR"
cd "$DIR"
git remote set-url origin "git@github.com:$REPO.git"
git config user.name "paper-trade-oracle"
git config user.email "paper-trade-oracle@users.noreply.github.com"
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

# secrets file: you fill it in; never commit or print it
if [ ! -f "$ENV_FILE" ]; then
  printf 'DHAN_CLIENT_ID=\nDHAN_PIN=\nDHAN_TOTP_SECRET=\nPYTHONUNBUFFERED=1\n' > "$ENV_FILE"
fi
chmod 600 "$ENV_FILE"

# systemd units (installed but NOT enabled: GitHub Actions still runs the trader)
sudo sed "s#__HOME__#$HOME#g" deploy/oracle/paper-trade.service | sudo tee /etc/systemd/system/paper-trade.service >/dev/null
sudo cp deploy/oracle/paper-trade.timer deploy/oracle/paper-push.timer /etc/systemd/system/
sudo sed "s#__HOME__#$HOME#g" deploy/oracle/paper-push.service | sudo tee /etc/systemd/system/paper-push.service >/dev/null
sudo systemctl daemon-reload

echo
echo "== Done. Next steps =="
echo "1. Add this deploy key on GitHub: repo Settings -> Deploy keys -> Add key, tick 'Allow write access':"
cat "$HOME/.ssh/trade_deploy.pub"
echo "2. Fill in your Dhan credentials:  nano $ENV_FILE"
echo "3. Test (read-only, safe any time):  cd $DIR && set -a && . $ENV_FILE && set +a && .venv/bin/python -m algo.cli check"
echo "4. Do NOT enable the timers yet - tell Claude, who will switch GitHub off first (two traders must never run together)."
