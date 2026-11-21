# Postiz on an always-on cloud VM (posts go out with the laptop off)

**Why.** Postiz is what actually publishes at the scheduled time. While it runs on the laptop, the
laptop and Docker must be on at every post time. Moving Postiz to a free, always-on VM removes
that: Media OS (still on the laptop) hands each approved post to Postiz when you schedule it, and
the VM publishes it on time whether the laptop is on or not.

**What changes and what doesn't**

| | Before | After |
|---|---|---|
| Publishing at post time | needs the laptop + Docker on | the VM does it, laptop can be off |
| Generating, reviewing, approving, scheduling | laptop | laptop (unchanged) |
| Status sync, stats, Telegram alerts | laptop | laptop; they catch up when it's next on |
| Image hosting | GitHub Pages | GitHub Pages (unchanged) |
| Who can reach Postiz | only the laptop | only your own devices, over Tailscale; nothing is public |

Phase 2 (optional, later): the VM has room for the whole Media OS stack too, which would make
alerts and the Telegram review buttons work with the laptop off. Not covered here.

**Cost.** ₹0. Oracle Cloud's *Always Free* tier includes ARM VMs (up to 4 cores and 24 GB RAM in
total). Tailscale's personal plan is free.

> ⚠️ **Read before signing up.** Oracle asks for a card when you create the account, to verify
> identity; Always Free resources aren't charged, and the account stays on the free tier unless you
> upgrade it. Oracle may reclaim an Always Free VM that stays idle (under ~20 % CPU, network *and*
> memory for 7 days); Postiz + Temporal + Elasticsearch use well over 20 % of the 12 GB below, so it
> shouldn't count as idle, but keep the nightly backup (step 8) so a reclaimed VM is a 20-minute
> rebuild, not a loss.

Everything that needs your accounts or passwords is marked **(you)**: Media OS never creates
accounts or types credentials.

---

## 1. Create the VM (you)

1. Sign up at oracle.com/cloud/free and pick a **home region** near India (e.g. Mumbai or
   Hyderabad); Always Free resources only exist in the home region.
2. Compute → Instances → **Create instance**:
   - Image: **Canonical Ubuntu 24.04** (aarch64).
   - Shape: **Ampere VM.Standard.A1.Flex**, **2 OCPU, 12 GB** RAM.
   - Networking: the default VCN with a public IP (needed for SSH and updates; Postiz itself is
     never exposed).
   - SSH keys: upload your public key (or let Oracle generate one and keep the private key safe).
   - Advanced → Management → **Initialization script**: paste `cloud-init.yaml` from this folder.
3. "Out of host capacity" is common for free ARM VMs: retry later, or pick another availability
   domain in the same region.

## 2. Join Tailscale (you)

1. Install Tailscale on the **laptop** (and your phone if you like) from tailscale.com and sign in.
2. SSH to the VM (`ssh ubuntu@<public-ip>`), then run `sudo tailscale up` and open the link it
   prints to add the VM to your tailnet.
3. In the Tailscale admin console: rename the VM to `postiz`, and enable **MagicDNS** and
   **HTTPS certificates** (DNS page). The VM is now `postiz.<your-tailnet>.ts.net`.

## 3. Export the local Postiz (laptop)

Do this when **nothing is scheduled** in Postiz (the export refuses otherwise): Temporal, which
holds scheduled jobs, starts fresh on the VM. Posts already published keep their ids, so Media OS
keeps reading their stats.

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Media OS Agent\media-intelligence-os\infra\cloud\export-postiz.ps1"
```

It writes `postiz-db.dump` and `postiz-uploads.tgz` to `C:\Media OS Agent\postiz-export`. Both
contain account tokens: private, and deleted after step 9.

## 4. Copy to the VM (laptop, over Tailscale)

```powershell
scp "C:\Media OS Agent\postiz-runtime\docker-compose.yaml" "C:\Media OS Agent\postiz-runtime\.env" "C:\Media OS Agent\postiz-export\postiz-db.dump" "C:\Media OS Agent\postiz-export\postiz-uploads.tgz" "C:\Media OS Agent\media-intelligence-os\infra\cloud\postiz-cloud.override.yaml" "C:\Media OS Agent\media-intelligence-os\infra\cloud\restore-postiz.sh" "C:\Media OS Agent\media-intelligence-os\infra\cloud\backup-postiz.sh" ubuntu@postiz:/opt/postiz/
```

(If the compose file references other files next to it, such as `dynamicconfig/`, copy those too
with `scp -r`.)

## 5. Restore and start Postiz (VM)

```bash
cd /opt/postiz
echo "POSTIZ_PUBLIC_URL=https://postiz.<your-tailnet>.ts.net" >> .env
chmod 600 .env postiz-db.dump postiz-uploads.tgz
bash restore-postiz.sh
```

`restore-postiz.sh` pulls the images (all have ARM builds; if one doesn't, the pull says
"no matching manifest" and nothing is changed), restores the database and uploads, and starts the
stack.

## 6. Give it an HTTPS name on your tailnet (VM)

```bash
sudo tailscale serve --bg --https=443 http://127.0.0.1:4007
```

Open `https://postiz.<your-tailnet>.ts.net` on the laptop: you should see your Postiz, signed in
as before, with Instagram and the Facebook Page connected. **(you)** If Postiz ever asks you to
reconnect a channel, add `https://postiz.<your-tailnet>.ts.net/integrations/social/instagram`
(and `/facebook`) to the Meta app's *Valid OAuth Redirect URIs* first.

## 7. Point Media OS at the VM (laptop)

Add to `C:\Media OS Agent\media-os-runtime\.env`:

```
POSTIZ_API_BASE=https://postiz.<your-tailnet>.ts.net
POSTIZ_BASE_URL=https://postiz.<your-tailnet>.ts.net
POSTIZ_REMOTE_HOSTS=postiz.<your-tailnet>.ts.net
```

`POSTIZ_REMOTE_HOSTS` is the allow-list: Media OS only sends the Postiz API key to a host named
there, over https. Keep `POSTIZ_API_KEY` as it is (it moved with the database). Then recreate the
containers so they read the new values:

```powershell
cd "C:\Media OS Agent\media-intelligence-os"
docker compose --env-file "C:\Media OS Agent\media-os-runtime\.env" -f infra/docker/compose.app.yml up -d --wait
```

Check the worker can reach the VM (prints the connected channels' names):

```powershell
docker exec media-os-worker python -c "import os; from media_os_api.publish.postiz import PostizClient as P; print([i.name for i in P(base_url=os.environ['POSTIZ_API_BASE'], api_key=os.environ['POSTIZ_API_KEY']).integrations()])"
```

If it can't resolve the name, Docker isn't using Windows' Tailscale DNS: add the VM to the
containers' hosts with a small override file (`extra_hosts: ["postiz.<tailnet>.ts.net:100.x.y.z"]`
for `media-os-worker` and `media-os-api`, with the VM's Tailscale IP from the admin console).

With `POSTIZ_REMOTE_HOSTS` set, the Windows self-healer stops watching the local Postiz
containers and no longer starts Docker before each post (the VM posts them). Stop the local
Postiz, keeping its data for a rollback:

```powershell
cd "C:\Media OS Agent\postiz-runtime"; docker compose stop
```

## 8. Nightly backups (VM)

```bash
(crontab -l 2>/dev/null; echo "30 21 * * * bash /opt/postiz/backup-postiz.sh") | crontab -
```

Dumps go to `/var/backups/postiz` (03:00 IST, 14 days kept). Copy one to the laptop now and then:
`scp ubuntu@postiz:/var/backups/postiz/<file> "C:\Media OS Agent\media-os-backups\"`.

## 9. Test, then tidy up

1. Schedule a post from Media OS for ~30 minutes ahead, then shut the laptop. It should publish,
   and when the laptop is next on, the dashboard shows it as Published with its link.
2. Delete `C:\Media OS Agent\postiz-export` and the dump/archive in `/opt/postiz`.

## Rollback

Remove the three lines from step 7, start the local Postiz again
(`cd "C:\Media OS Agent\postiz-runtime"; docker compose start`) and recreate the Media OS
containers. Posts scheduled on the VM in the meantime exist only there: re-schedule them first.
