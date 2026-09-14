# Docker security & runtime report

Date: 2026-09-14. Evidence below was produced by running the commands shown against the compose files in
this repository. Where the environment forced a substitution it is stated; nothing is inferred from reading
the files alone unless marked *static*.

## Environment caveats (read first)

| Constraint | Effect | Mitigation used |
|---|---|---|
| Docker Hub / any registry: pull denied (`download failed: Forbidden`) | the official base images (`python:3.11-slim-bookworm`, `node:22-bookworm-slim`) and `hummingbot/gateway:version-2.16.0` cannot be pulled; `# syntax=docker/dockerfile:1.7` cannot be resolved | Dockerfile parameterised (`ARG BASE_IMAGE`, `NODE_IMAGE`, `APT_PACKAGES`, `PY_SITE`) and built **unmodified** with a base image imported from this machine's Debian rootfs (`fedr/base-local:sandbox`, Python 3.11.15, Node 22.22.2); the syntax directive was dropped (no 1.7-only features are used) |
| No Docker daemon provided | – | a daemon was started in the sandbox: `dockerd --storage-driver=overlay2 --iptables=false --bridge=none` (no NAT; user-defined bridge networks and loopback port publishing via docker-proxy work) |
| Gateway source build needs the JSR npm registry (blocked) | Gateway container cannot run here | app started with `--no-deps`; network isolation proven with a stand-in container carrying the `gateway` alias |

**Therefore:** image *build reproducibility* with the official bases is **BLOCKED BY EXTERNAL DEPENDENCY**
(re-run `docker compose build` on a normal host); container *runtime behaviour, hardening, persistence and
network topology* are **VERIFIED** as documented below.

## Image (`docker/Dockerfile`, target `prod`)

*Static:* multi-stage (frontend build → python deps → slim runtime); runtime stage copies only
`site-packages`, `/usr/local/bin`, `backend/fedr` and the built UI; `USER fedr` (uid/gid 10001, no shell,
home `/app`); `EXPOSE 8935`; `VOLUME /data`; HEALTHCHECK on `/api/system/health`; no secrets baked in;
`.dockerignore` excludes `.env*`, `data/`, `node_modules/`, `dist/`, `backend/fedr/static`.

*Runtime (built with the substituted base):*

```
$ docker image inspect fedr/app:latest --format 'User={{.Config.User}} Exposed={{.Config.ExposedPorts}}'
User=fedr Exposed=map[8935/tcp:{}]
Healthcheck=[CMD-SHELL python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8935/api/system/health', timeout=5).status==200 else 1)"]
$ docker exec fedr-app python -c "import fedr,ccxt,web3,solders;print('python deps ok', ccxt.__version__)"
python deps ok 4.5.78
```

Build steps that ran inside the build: `npm ci` (frontend), `npm run build` (vite), `python -m pip install .`
(all runtime dependencies), user creation, `COPY --chown`. The image is 4.4 GB only because the imported
sandbox base is a full Debian rootfs; the official `python:3.11-slim` base yields a ~500 MB image.

Findings fixed during this verification:

1. `pip install --upgrade pip` and a bare `pip` are base-image assumptions; the Dockerfile now uses
   `python -m pip install .` bound to the image interpreter and no longer self-upgrades pip.
2. `groupadd`/`useradd` are now idempotent (`getent`/`id` guards) so a base image that already provides the
   user does not break the build.
3. The `# syntax=` directive forced a registry fetch at build time even for air-gapped hosts; removed.

## Compose (`docker-compose.yml` + overlays)

```
$ for f in "" dev paper testnet live; do docker compose -f docker-compose.yml ${f:+-f docker-compose.$f.yml} config -q; done
base: OK   dev: OK   paper: OK   testnet: OK   live: OK
$ docker compose config | awk '/^  gateway:/,/^  [a-z]+:$/' | grep -c "ports:"
0                                   # gateway publishes nothing
$ docker compose config | grep -A2 published
        published: "8935"           # app: host 127.0.0.1 only (see Ports below)
```

Service `app`: `read_only: true`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`,
`tmpfs: /tmp:size=64m,mode=1777,uid=10001,gid=10001`, named volume `fedr-data:/data`, loopback publish,
private bridge network `fedr-internal`, `restart: unless-stopped`, healthcheck 30 s.
Service `gateway`: same `no-new-privileges`, **no `ports:`**, reachable only as `gateway:15888` on the
private network, `DEV=true` plain HTTP confined to that network.

Findings fixed during this verification:

4. **Bind-mounted `./data` was unwritable for uid 10001.** First run failed *safely*:
   `RuntimeError: unsafe or invalid configuration: FEDR_DATA_DIR /data is not writable: [Errno 13]
   Permission denied: '/data/.write-test'` → container exited, nothing ran degraded. The default is now the
   named volume `fedr-data` (initialised with the image's ownership). Bind mounts remain possible with
   `chown -R 10001:10001 data` (documented in README / OPERATIONS).
5. `/tmp` tmpfs was mounted root-owned (`touch /tmp/x: Permission denied`); now `mode=1777,uid=10001`.
6. `env_file: .env` was mandatory, so `docker compose config` failed without the file; it is now
   `required: false` (variables can come from the environment / an orchestrator).

## Runtime verification (`docker compose up -d --no-deps --no-build app`, SIMULATION mode)

```
$ docker compose ps
NAME       IMAGE             STATUS                    PORTS
fedr-app   fedr/app:latest   Up (healthy)              127.0.0.1:8935->8935/tcp
$ curl -s 127.0.0.1:8935/health/live            → 200 {"status":"ok"}
$ curl -s 127.0.0.1:8935/health                 → 200 {"status":"ok","message":"SYSTEM READY"}
$ curl -s 127.0.0.1:8935/health/ready           → 200 {"status":"ready","checks":{"database":true,"profit_guard":true,"risk_engine":true,"strategy_engine":true,"scan_loop":true,"gateway":true,"not_emergency_stopped":true}}
$ curl -s 127.0.0.1:8935/api/system/health      → 200 {"ok":true,"status":"SYSTEM READY"}
$ curl -s -o /dev/null -w %{http_code} 127.0.0.1:8935/api/trading/state                      → 401
$ curl -s -o /dev/null -w %{http_code} -H "Authorization: Bearer $TOKEN" .../api/trading/state → 200
$ curl -s 127.0.0.1:8935/ | head -3             → <!doctype html> … (built UI served by the app)
```

### Hardening, as seen from inside the running container

```
$ docker inspect fedr-app --format '…'
User=fedr ReadonlyRootfs=true CapDrop=[ALL] SecurityOpt=[no-new-privileges:true]
Tmpfs=map[/tmp:size=64m,mode=1777,uid=10001,gid=10001] Ports=map[8935/tcp:[{127.0.0.1 8935}]]
$ docker exec fedr-app sh -c 'id; grep CapEff /proc/1/status; touch /app/x; touch /data/p; touch /tmp/x'
uid=10001(fedr) gid=10001(fedr) groups=10001(fedr)
CapEff: 0000000000000000                  # no capabilities at all
touch: cannot touch '/app/x': Read-only file system
WRITE /data: ok   WRITE /tmp: ok
$ docker exec fedr-app ls -la /data/config
-rw------- 1 fedr fedr 44 master.key        # generated master key, mode 0600, owned by the app user
```

### Persistence

```
POST /api/system/emergency-stop  → {"activated_at_ms":…,"reconciliation":{"ok":true,…}}
docker compose restart app       → emergency_stop=True   (state read back from SQLite)
docker compose down && up -d     → emergency_stop=True   (named volume survives down/up)
/data/database: fedr.db fedr.db-shm fedr.db-wal          (WAL mode)
```

### Network isolation of the Gateway

The Gateway image cannot run here, so a stand-in HTTP server was attached to the same private network with
the alias `gateway`:

```
$ docker run -d --network fedr_fedr-internal --network-alias gateway fedr/base-local:sandbox python3 -m http.server 15888
$ docker exec fedr-app python -c "import urllib.request;print(urllib.request.urlopen('http://gateway:15888/').status)"
200                                       # reachable from the app over the private network
$ curl -s -o /dev/null -w %{http_code} http://127.0.0.1:15888/
000                                       # not reachable from the host
$ docker port fedr-gateway-standin
(empty)                                   # nothing published
```

## Not verified here (do this on the target host)

- `docker compose build` with the official base images and `docker compose pull` of the Gateway image.
- The real Gateway container's own hardening (it runs as the image's user; FEDR only confines its network).
- Behaviour under a real `iptables`/NAT setup (this daemon ran with `--iptables=false`); the loopback
  publish was verified via docker-proxy.
- Log rotation / disk-pressure behaviour of the named volume over weeks of paper trading.

## Checklist

- [x] non-root uid 10001, no capabilities, read-only root FS, `no-new-privileges`
- [x] only `127.0.0.1:8935` published; Gateway unpublished and reachable from the app only
- [x] no secrets in the image or compose files; generated master key 0600 inside the volume
- [x] startup fails closed on an unwritable data dir (observed) and on unsafe env combinations (tests)
- [x] health endpoints + Docker healthcheck green; restart and down/up keep state
- [ ] official base images and Gateway image (registry blocked here)
