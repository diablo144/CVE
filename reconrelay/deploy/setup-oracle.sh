#!/usr/bin/env bash
# Initial host setup for Ubuntu on Oracle Cloud Always Free (Ampere A1 or AMD).
# Run as a sudo-capable user. Review before executing; this installs Docker,
# Compose, Nginx and Certbot but does not open Oracle's cloud security lists.
set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
  echo "Run as your normal sudo-capable user, not directly as root." >&2
  exit 1
fi

sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg nginx certbot python3-certbot-nginx

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

. /etc/os-release
printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu %s stable\n' \
  "$(dpkg --print-architecture)" "${VERSION_CODENAME}" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
sudo systemctl enable --now docker nginx

cat <<'MSG'
Docker, Compose, Nginx and Certbot are installed.
Next steps (not automated):
  1. Put reconrelay/ on the instance and create reconrelay/.env from .env.example.
  2. Set a strong RR_JWT_SECRET, a distinct RR_API_KEYS value and your exact
     RR_AUTHORIZED_SCOPES. Keep RR_ALLOW_ANY_SCOPE and RR_ALLOW_ACTIVE_MODULES false.
  3. Point a DNS A/AAAA record at the instance and replace recon.example.com in
     deploy/nginx.conf. Allow TCP 80/443 in BOTH Oracle Security Lists/NSGs and
     the instance firewall; do not open Redis (6379) or API port (8000).
  4. Issue a certificate with: sudo certbot --nginx -d YOUR_DOMAIN
  5. Install deploy/nginx.conf at /etc/nginx/sites-available/reconrelay, enable
     it, run `sudo nginx -t && sudo systemctl reload nginx`.
  6. Log out/in so the docker group applies, then in reconrelay run:
       docker compose up -d --build
  7. Check `docker compose ps` and `curl -fsS https://YOUR_DOMAIN/health`.
  8. Configure the extension with the same API key and verify the server health.

The Oracle Console firewall is separate from the Ubuntu host firewall. This
script does not change either firewall rule set. Docker/Compose runtime was not
available in the authoring environment; image build and live deployment remain
unverified here.
MSG
