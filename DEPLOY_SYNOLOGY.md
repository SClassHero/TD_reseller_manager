# Synology DS218+ Deployment Guide

This guide deploys a clean Inventory App v5 instance on a Synology DS218+ running DSM 7.

The recommended path is Container Manager/Docker because it keeps Python dependencies isolated
from DSM and makes rollback simple.

DSM itself commonly uses ports `5000` and `5001`, so this guide publishes the app on NAS port
`5080` while keeping Flask on port `5000` inside the container.

## What Gets Persisted

The container stores runtime data in a mounted `data/` folder:

```text
data/
├── inventory.db          # created automatically on first start
├── .secret_key           # created automatically on first start
├── backups/              # app backup ZIP files
└── uploads/products/     # product photos
```

In the container, `data/uploads/products/` is mounted into `/app/static/uploads/products`
so uploaded product photos remain persistent and are still served by Flask's static route.

Do not copy your Windows `inventory.db`, `.secret_key`, `backups/`, or test upload folders if you want a clean NAS install.

## Files Used

- `Dockerfile`
- `docker-compose.synology.yml`
- `.dockerignore`

## Step 1: Confirm Container Support

Your DS218+ is an Intel 64-bit Plus model, so it is the right hardware class for Docker/Container Manager.

In DSM:

1. Open **Package Center**.
2. Search for **Container Manager**.
3. If DSM is older and you do not see it, search for **Docker**.
4. Install the package.

If neither package appears, update DSM if possible. If it still does not appear, use the fallback `start_app.sh`
method in `USERGUIDE.md`, but Container Manager is strongly preferred.

## Step 2: Copy a Clean App Folder to the NAS

Create this folder in File Station:

```text
/volume1/docker/inventory_app_v5/
```

Copy the app code into that folder, but exclude:

```text
inventory.db
.secret_key
backups/
data/
__pycache__/
pytest-cache-files-*/
_scenario_uploads_*/
_probe_upload_*/
_inv_test_backups_*/
*.log
```

Then create this empty folder:

```text
/volume1/docker/inventory_app_v5/data/
```

## Step 3: Create the Container Manager Project

In DSM:

1. Open **Container Manager**.
2. Go to **Project**.
3. Click **Create**.
4. Project name: `inventory-app`
5. Path: `/volume1/docker/inventory_app_v5`
6. Choose **Create docker-compose.yml** or upload a compose file.
7. Paste the contents of `docker-compose.synology.yml`.
8. Build and start the project.

After it starts, open:

```text
http://NAS-IP:5080
```

Default admin login:

```text
username: admin
password: admin123
```

Immediately change the password in **Settings** and generate a recovery code.

## Step 4: LAN Access

For home use, bookmark:

```text
http://NAS-IP:5080
```

If your router supports DHCP reservation, reserve the NAS IP address so the bookmark does not break.

## Step 5: Remote Access with Tailscale

Current deployment route: Tailscale. It works without router port forwarding,
which is important when the ISP modem cannot forward inbound ports.

1. Install **Tailscale** on the Synology NAS from Package Center.
2. Open Tailscale on the NAS and sign in.
3. Install Tailscale on your phone/laptop and sign in to the same account.
4. Find the NAS Tailscale IP address, usually `100.x.y.z`.
5. When away from home, turn on Tailscale and open:

```text
http://100.x.y.z:5080
```

QuickConnect can still be useful for DSM itself, but it does not expose this
custom Flask/Container Manager app. Synology DDNS/reverse proxy can be revisited
later only if the network allows inbound HTTPS traffic.

## Step 6: Backup

The app's built-in backups are stored here:

```text
/volume1/docker/inventory_app_v5/data/backups/
```

Also include this whole folder in Synology Hyper Backup:

```text
/volume1/docker/inventory_app_v5/data/
```

## Troubleshooting

**Login succeeds but the next page sends you back to login:**

- Rebuild the Container Manager project with the latest `Dockerfile` and `app.py`.
- The container should run one Gunicorn worker and use `/data/.secret_key` for a persistent shared session key.
- If this happened on an older build, stop the project, rebuild it, start it again, then log in fresh.

**Can't reach the app from phone on home WiFi:**

- Open `http://NAS-IP:5080`.
- Confirm the NAS IP has not changed.
- Check that DSM firewall allows port `5080`.

## Rollback

Container rollback is simple:

1. Stop the `inventory-app` project in Container Manager.
2. Restore or replace the app code folder.
3. Keep `data/` if you want to keep the live database.
4. Start the project again.

To reset to a totally clean app, stop the project and rename `data/` to `data_old/`, then start the project again.
The app will create a fresh database.
