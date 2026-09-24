# VPS and bot setup (owner only)

Everything here is done by the owner. Contributors never get SSH access, tokens or keys. Commands assume a fresh Ubuntu 24.04 VPS.

## 0. Before anything
- A **separate, small** VPS with nothing else on it.
- Take a snapshot right after the setup below, so you can roll back.

## 1. Server basics
```bash
# as root
adduser --disabled-password --gecos "" deploy
apt-get update && apt-get -y upgrade
apt-get -y install ca-certificates curl ufw fail2ban uidmap dbus-user-session
# SSH: keys only, no root login
sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/; s/^#\?PermitRootLogin .*/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl restart ssh
```

## 2. Docker without root rights for the deploy user
Putting `deploy` in the `docker` group equals giving it root. Use rootless Docker instead:
```bash
curl -fsSL https://get.docker.com | sh
systemctl disable --now docker.service docker.socket
loginctl enable-linger deploy
sudo -iu deploy dockerd-rootless-setuptool.sh install
sudo -iu deploy sh -c 'echo "export DOCKER_HOST=unix:///run/user/$(id -u)/docker.sock" >> ~/.bashrc'
```
(Rootless mode ignores `cpus`/`pids_limit` unless cgroup v2 delegation is on. Ubuntu 24.04 has cgroup v2; if limits are ignored, see Docker's "rootless" docs about `systemd` delegation.)

## 3. Deploy key (make it on your own machine, not on the server)
```bash
ssh-keygen -t ed25519 -N "" -C "photo-check-bot deploy" -f ./deploy_key
```
On the server, put the **public** key with the `restrict` option (no forwarding, no agent, no pty):
```bash
sudo -iu deploy sh -c 'mkdir -p ~/.ssh && chmod 700 ~/.ssh'
echo 'restrict ssh-ed25519 AAAA...your-public-key... photo-check-bot deploy' | sudo -iu deploy tee -a /home/deploy/.ssh/authorized_keys
sudo -iu deploy chmod 600 /home/deploy/.ssh/authorized_keys
```

## 4. Firewall
SSH has to stay reachable from GitHub Actions runners, whose IPs change, so it is keys-only with fail2ban.
```bash
ufw default deny incoming
ufw default deny outgoing
ufw allow in 22/tcp
ufw allow out 53
ufw allow out 443/tcp
ufw allow out 80/tcp     # apt and Docker installs; remove after setup if you like
ufw enable
```
Outgoing SMTP (25) and everything else stays closed, so the box can't be used for spam. Note: with rootless Docker, container traffic goes through the deploy user's network namespace and obeys these host rules. A per-domain allowlist (only `api.telegram.org` plus the model API) is the next step: an egress proxy such as squid with a domain ACL. Add it together with the model API.

## 5. Telegram bot
1. In @BotFather: `/newbot`. Keep the token to yourself.
2. If the token ever leaks: `/revoke` in @BotFather. The old token dies at once.

## 6. Secrets in GitHub (only in the `production` environment)
Run these on your machine. Each command prompts for the value, so it never shows up in shell history or chat:
```bash
R=ITSalt/photo-check-bot
gh secret set VPS_HOST           --env production -R $R
gh secret set VPS_USER           --env production -R $R    # deploy
gh secret set VPS_SSH_KEY        --env production -R $R < ./deploy_key
ssh-keyscan -t ed25519 <VPS_IP> | gh secret set VPS_KNOWN_HOSTS --env production -R $R
gh secret set TELEGRAM_BOT_TOKEN --env production -R $R
rm ./deploy_key
```
Never create repository-level secrets: every branch's workflows can read those.

## 7. Kill switch
- Stop the bot: `ssh deploy@<VPS_IP> 'cd ~/app && docker compose down'`
- Kill the token: `/revoke` in @BotFather.
- Stop deploys: remove the collaborator or disable the `deploy` workflow in the repo's Actions tab.

## 8. Later, before the paid version
- Model API key with a **hard monthly spending limit** set at the provider.
- The egress proxy from step 4.
- A privacy note in `/start` and a personal-data policy if the bot ever stores anything beyond the Telegram user id and a counter.
