# Paper trader on Oracle Cloud (Always Free)

Runs the same paper trader as GitHub Actions, but as one all-day process on a VM with a
fixed IP. **Paper only** - nothing here places orders. Not enabled until we switch over from
GitHub (two traders must never write the same journal).

1. Create the VM (Oracle console): Ubuntu 24.04, shape VM.Standard.A1.Flex, 1 OCPU / 6 GB,
   home region Mumbai or Hyderabad, upload your SSH public key.
2. Give it a static IP: Networking -> IP management -> Reserved public IPs -> Reserve, then
   attach it to the VM's VNIC (Compute -> Instance -> Attached VNICs -> IPv4 addresses).
3. SSH in (`ssh ubuntu@<ip>`) and run:
   `curl -fsSL https://raw.githubusercontent.com/ashwani3011/trade/claude/adoring-planck-88ci7e/deploy/oracle/setup.sh | bash`
4. Follow the printed steps: add the deploy key (write access), fill `~/.paper-trade.env`,
   run the read-only `check`.
5. Switch-over (done together with Claude): disable the GitHub schedule/routines, then
   `sudo systemctl enable --now paper-trade.timer paper-push.timer`.

Logs: `journalctl -u paper-trade -f`. Stop: `sudo systemctl disable --now paper-trade.timer paper-push.timer`.
