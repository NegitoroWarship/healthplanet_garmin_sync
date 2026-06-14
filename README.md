# healthplanet_garmin_sync

📖 **English** · [日本語版 README → README.ja.md](README.ja.md)

A bridge that asynchronously syncs the body weight stored in Tanita's **HealthPlanet**
to **Garmin Connect**. It runs **twice a day** (10:17 / 22:17 JST) on a homelab k3s
cluster and uploads to Garmin **only when there is a new measurement**.

```
HealthPlanet API (weight, tag 6021)
      │  OAuth2 + innerscan.json
      ▼
  Diff extraction (state.json: only measurements newer than the last synced one)
      │
      ▼
  FIT generation (fit_tool: weight_scale_message)
      │
      ▼
  Garmin Connect (python-garminconnect: POST the FIT to upload-service)
```

If there are zero new measurements, it exits immediately without even connecting to Garmin.

> Design details: [docs/DESIGN.md](docs/DESIGN.md). Repository conventions (e.g. "don't
> leave stale content behind"): [CLAUDE.md](CLAUDE.md).

## Structure

| File | Role |
|---|---|
| `src/healthplanet.py` | OAuth2 token refresh + fetch weight from innerscan |
| `src/fit_writer.py`   | weight → FIT conversion (`fit_tool`) |
| `src/garmin.py`       | Garmin login + FIT upload (`python-garminconnect`) |
| `src/state.py`        | persist/read the last synced measurement timestamp |
| `src/sync.py`         | overall orchestration (entry point) |
| `scripts/authorize_healthplanet.py` | first-time OAuth authorization (manual, once) |
| `k8s/`                | Namespace / PVC / Secret / CronJob |

Tokens (HealthPlanet & Garmin) and state are persisted under `DATA_DIR` (default `./data`,
a PVC at `/data` on k8s).

## Credentials

- **HealthPlanet**: register an app at <https://www.healthplanet.jp/apis/registinfo.do>
  and obtain `client_id` / `client_secret`. Set `redirect_uri` to match the URL you
  registered (for non-web apps, `https://www.healthplanet.jp/success.html` works).
- **Garmin Connect**: email / password (assumes MFA is disabled).

> Note: writing to Garmin does not use an official public API; it goes through the
> `upload-service` (the same import path the Garmin app uses). This is a personal-use
> approach that may break if Garmin changes their internals.

## Running locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in the values

# 1) Authorize HealthPlanet once (saves access/refresh tokens under data/)
python -m scripts.authorize_healthplanet

# 2) [Important] Seed so existing weights are not re-uploaded (see below)
python -m src.sync --seed

# 3) Run the sync (uploads only measurements taken after the seed)
python -m src.sync
```

### Avoiding duplicate weight entries (required once, on first setup)

If you already have weights in Garmin (e.g. entered manually), a first sync that bulk-uploads
HealthPlanet history would create **duplicates**. To prevent this, run seed **once** right
after the first authorization:

```bash
python -m src.sync --seed
```

`--seed` only records "everything currently in HealthPlanet is already synced" into the
state file; it **uploads nothing** to Garmin. Subsequent `python -m src.sync` runs send
**only weights measured after the seed**.

Alternatively, the environment variable `SYNC_SINCE=20260614` (only measurements after that
date) achieves the same goal. On k8s, setting `SYNC_SINCE` via the Secret/env is the easiest
(no manual seed needed).

On the first Garmin login the session is saved under `data/garmin_tokens/`, so no re-login is
needed afterwards (tokens last ~1 year and auto-refresh).

## Docker

```bash
docker build -t healthplanet-garmin-sync:latest .
docker run --rm -v "$PWD/data:/data" --env-file .env healthplanet-garmin-sync:latest
```

## Deploying to Kubernetes (k3s)

`k8s/` ships a Namespace / PVC / Secret (template) / CronJob. It runs twice a day
(10:17 / 22:17 JST) on homelab k3s.

Key points:

- The only thing you must pre-load into the PVC is the **HealthPlanet token**. With MFA
  disabled, Garmin **logs in automatically on the first CronJob run** using the Secret
  credentials and saves the session to the PVC (`/data/garmin_tokens/`), so no pre-loading
  is needed.
- To avoid duplicate weight entries, the easiest option is to **set `SYNC_SINCE` on the
  first rollout** (uncomment it in `k8s/cronjob.yaml` and put in a date).

### 0. Prerequisites

- `kubectl` points at your homelab k3s cluster (`kubectl get nodes`).
- The image must be **available on the node that runs the Job** (below). For multiple nodes,
  import on all nodes or use a private registry.

### 1. Build the image and import it into k3s (no registry needed)

```bash
docker build -t healthplanet-garmin-sync:latest .
docker save healthplanet-garmin-sync:latest -o /tmp/hpgs.tar
sudo k3s ctr images import /tmp/hpgs.tar          # run on each node
```

> If you use a registry, push there and set `image:` in `k8s/cronjob.yaml` to that tag.

### 2. Create Namespace / PVC / Secret

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/pvc.yaml

# Copy the Secret template and fill in real values (secret.yaml is gitignored = never committed)
cp k8s/secret.example.yaml k8s/secret.yaml
# Fill in: HEALTHPLANET_CLIENT_ID/SECRET, GARMIN_EMAIL/PASSWORD, etc.
kubectl apply -f k8s/secret.yaml
```

### 3. Load the HealthPlanet token into the PVC (first time only)

OAuth authorization needs a browser, so do it locally and copy the resulting **single token
file** into the PVC.

```bash
# (a) Authorize locally (generates data/healthplanet_tokens.json)
python -m scripts.authorize_healthplanet

# (b) Spin up a throwaway Pod that mounts the PVC (run as the same uid:10001 as appuser)
kubectl -n healthplanet-garmin-sync apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: hpgs-seed
spec:
  restartPolicy: Never
  securityContext:
    runAsUser: 10001
    runAsGroup: 10001
    fsGroup: 10001
  containers:
    - name: shell
      image: busybox:1.36
      command: ["sh", "-c", "sleep 3600"]
      volumeMounts:
        - { name: data, mountPath: /data }
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: healthplanet-garmin-sync-data
EOF
kubectl -n healthplanet-garmin-sync wait --for=condition=Ready pod/hpgs-seed --timeout=60s

# (c) Copy just the HealthPlanet token
kubectl -n healthplanet-garmin-sync cp ./data/healthplanet_tokens.json hpgs-seed:/data/healthplanet_tokens.json

# (optional) If you ran `python -m src.sync --seed` locally, copy state too and you won't need SYNC_SINCE
# kubectl -n healthplanet-garmin-sync cp ./data/state.json hpgs-seed:/data/state.json

# (d) Clean up
kubectl -n healthplanet-garmin-sync delete pod hpgs-seed
```

> If you don't load `state.json`, enable `SYNC_SINCE` in `k8s/cronjob.yaml` before step 4
> (otherwise the first run bulk-uploads the past).

### 4. Deploy the CronJob

```bash
kubectl apply -f k8s/cronjob.yaml
kubectl -n healthplanet-garmin-sync get cronjob   # check SCHEDULE/TIMEZONE
```

### 5. Smoke-test with a manual trigger

```bash
kubectl -n healthplanet-garmin-sync create job --from=cronjob/healthplanet-garmin-sync manual-1
kubectl -n healthplanet-garmin-sync logs -f job/manual-1
```

Success looks like `Garmin login OK` and `No new measurements` (or `uploaded N measurement(s)`)
in the logs. This first run also auto-creates `/data/garmin_tokens/`, so no re-login afterwards.

### 6. Operations

```bash
# History and logs of scheduled runs
kubectl -n healthplanet-garmin-sync get jobs
kubectl -n healthplanet-garmin-sync logs job/<job-name>

# Suspend / resume
kubectl -n healthplanet-garmin-sync patch cronjob healthplanet-garmin-sync -p '{"spec":{"suspend":true}}'
kubectl -n healthplanet-garmin-sync patch cronjob healthplanet-garmin-sync -p '{"spec":{"suspend":false}}'

# On a code change: rebuild → re-import into k3s → update the cronjob with the new tag
#   (even when reusing :latest, re-importing makes the next Job pick it up)
```

> To change `schedule`/`timeZone`, edit `k8s/cronjob.yaml` and `kubectl apply` again. The
> minute is deliberately off `:00` to avoid the top-of-the-hour access spike. `timeZone`
> requires k8s/k3s **v1.27+**; on older versions remove that line and use UTC:
> `schedule: "17 1,13 * * *"` (= 10:17 / 22:17 JST).

## Troubleshooting

- **`No HealthPlanet tokens found`**: `scripts/authorize_healthplanet.py` hasn't been run.
- **HealthPlanet refresh fails / 401**: the refresh_token expired. Run the authorization
  script again.
- **Garmin auth fails**: wrong password / account locked / MFA enabled. If you enable MFA,
  you must pass `prompt_mfa` in `src/garmin.py`.
- **Dates are off**: check the container `TZ=Asia/Tokyo` (FIT is written in UTC).

## Metrics

Currently **weight only** (HealthPlanet tag 6021). To also sync body fat (6022) etc., extend
the fetch tags in `healthplanet.py` and `WeightScaleMessage.percent_fat` etc. in
`fit_writer.py`.
