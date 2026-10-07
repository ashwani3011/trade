#!/usr/bin/env bash
# One-time setup of the paper trader on an Oracle Cloud (or any Ubuntu) VM.
# Run as the default 'ubuntu' user:
#   curl -fsSL https://raw.githubusercontent.com/ashwani3011/trade/claude/adoring-planck-88ci7e/deploy/oracle/setup.sh -o setup.sh && bash setup.sh
# Paper trading only: this installs the same trader GitHub Actions runs. It places no orders.
set -euo pipefail
main() {  # wrapped so piping into bash still reads the whole script before running it
REPO=ashwani3011/trade
BRANCH=claude/adoring-planck-88ci7e
DIR="$HOME/trade"
ENV_FILE="$HOME/.paper-trade.env"

sudo apt-get update -qq </dev/null
sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get install -y -qq git python3 python3-venv python3-pip tzdata </dev/null
sudo timedatectl set-timezone Asia/Kolkata

# small machines (e.g. the 1 GB AMD micro): add 2 GB swap so pandas never runs out of memory
if [ "$(awk '/MemTotal/ {print $2}' /proc/meminfo)" -lt 2000000 ] && ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap -q /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

# deploy key so the server can push results (state/, reports/) back to GitHub
mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
[ -f "$HOME/.ssh/trade_deploy" ] || ssh-keygen -q -t ed25519 -N "" -C "paper-trade@oracle" -f "$HOME/.ssh/trade_deploy"
# GitHub over port 443 (works even where port 22 is blocked); accept GitHub's host key on first use
touch "$HOME/.ssh/config" && chmod 600 "$HOME/.ssh/config"
sed -i '/^Host github.com$/,/^$/d' "$HOME/.ssh/config"
cat >> "$HOME/.ssh/config" <<CFG
Host github.com
  HostName ssh.github.com
  Port 443
  User git
  IdentityFile ~/.ssh/trade_deploy
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new

CFG

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
}
main "$@"
