# Paper trader on Oracle Cloud (Always Free)

Runs the same paper trader as GitHub Actions, but as one all-day process on a VM with a
fixed IP. **Paper only** - nothing here places orders. Live since 2026-10-09; GitHub Actions is
the manual fallback only (two traders must never write the same journal).

1. Create the VM (Oracle console): Ubuntu 24.04, shape VM.Standard.A1.Flex, 1 OCPU / 6 GB,
   home region Mumbai or Hyderabad, upload your SSH public key. If Ampere is "Out of host
   capacity", use the Always Free VM.Standard.E2.1.Micro (1 GB; setup.sh adds swap) or upgrade
   to Pay As You Go and retry Ampere.
2. Give it a static IP: Networking -> IP management -> Reserved public IPs -> Reserve, then
   attach it to the VM's VNIC (Compute -> Instance -> Attached VNICs -> IPv4 addresses).
3. SSH in (`ssh ubuntu@<ip>`) and run:
   `curl -fsSL https://raw.githubusercontent.com/ashwani3011/trade/claude/adoring-planck-88ci7e/deploy/oracle/setup.sh -o setup.sh && bash setup.sh`
4. Follow the printed steps: add the deploy key (write access), fill `~/.paper-trade.env`,
   run the read-only `check`.
5. Switch-over (done 2026-10-08): GitHub weekday crons and Claude start routines off, then
   `sudo systemctl enable --now paper-trade.timer paper-push.timer`.

Logs: `journalctl -u paper-trade -f`. Stop: `sudo systemctl disable --now paper-trade.timer paper-push.timer`.
