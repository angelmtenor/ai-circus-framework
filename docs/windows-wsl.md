# Windows setup — WSL2 (Ubuntu-26.04)

Everything in this repo (`make`, `docker compose`, `k3d`, the `scripts/*.sh` helpers, the
per-service `uv` projects) assumes a Linux shell. On Windows, the supported way to run or
contribute is **WSL2** — the whole toolchain *and* Docker Engine live inside an Ubuntu distro,
and you use a normal Windows browser to open the app. Nothing is installed on the Windows side
except WSL itself — no Docker Desktop, so no Docker Desktop subscription terms to worry about.

This project is itself developed inside WSL2 on **Ubuntu 26.04 LTS**, which is the example
throughout; any current Ubuntu LTS works the same way.

## 1. Enable WSL

PowerShell **as Administrator**:

```powershell
wsl --install
```

This enables the required Windows features, installs the WSL2 kernel, and installs a default
Ubuntu. Reboot if prompted.

## 2. Install Ubuntu-26.04 specifically

```powershell
wsl --list --online
wsl --install Ubuntu-26.04
```

## 3. Launch it and finish first-run setup

```powershell
wsl -d Ubuntu-26.04
```

This drops you into an interactive prompt asking for a UNIX username and password — run it from
a real interactive terminal (Windows Terminal, PowerShell), since it can't be scripted.

Steps 1–3 are all that's strictly needed for a fresh distro — the rest of this section is
convenience and verification.

## 4. Update the system

Inside the distro:

```bash
sudo apt update && sudo apt upgrade -y
```

## 5. Set it as your default distro

Useful once you have more than one installed:

```powershell
wsl --set-default Ubuntu-26.04
```

## 6. Confirm everything's healthy

```powershell
wsl --status
wsl -l -v
```

`wsl -l -v` should list `Ubuntu-26.04` with `VERSION 2` — WSL1 doesn't support Docker's engine.

### Resource limits (`.wslconfig`)

WSL2 grabs up to half the host's RAM and every core by default, and a full k3s stack plus image
builds will happily use all of that — enough to starve Windows. Pin it in
`%UserProfile%\.wslconfig` (create the file if it doesn't exist; adjust to your machine — the
whole platform on k3d is comfortable at 10 GB):

```ini
[wsl2]
memory=10GB
processors=14
```

Apply with `wsl --shutdown` from PowerShell, then relaunch the distro. **`wsl --shutdown` kills
everything running inside — a `make k3s-build` in progress included — so do this before
starting long jobs, not during.** It's worse than lost time: it is a hard power-off of the VM,
and a file being written at that instant can survive *truncated* in Docker's build cache, then
get baked into images and crash at import time (exit code 135) — the `k3s-deploy-verify`
skill's Gotcha 6 has the diagnosis and fix if it ever happens.

## 7. Clone — inside the Linux filesystem

`git` is preinstalled on the Ubuntu WSL image (if not: `sudo apt install -y git`), so this
comes before the toolchain — the setup scripts in the next step live in the repo.

```bash
mkdir -p ~/PROJECTS && cd ~/PROJECTS
git clone https://github.com/angelmtenor/ai-circus-framework
cd ai-circus-framework
```

Contributing? Fork first and clone your fork instead — see [Contributing from Windows](#contributing-from-windows).

Two things that matter on WSL specifically:

- **Clone under `~/` (the distro's own filesystem), not `/mnt/c/...`.** The Windows-drive mount
  is dramatically slower for `uv sync`/`node_modules`/`docker build` contexts, and `make
  k3s-cluster`/`docker compose` bind-mount `./scenarios` and `infra/` into containers, which
  is unreliable across the `/mnt/c` boundary.
- **Clone with the distro's `git`, not Git for Windows.** Git for Windows defaults to CRLF line
  endings (`core.autocrlf=true`), which breaks every `scripts/*.sh` shebang and `.env` parsing
  inside containers. Cloning from inside WSL keeps LF endings and the scripts' executable bits.

## 8. Provision the distro — two scripts

Steps 7–9 are the same on any Ubuntu machine — the root README's
[Getting started, step 0](../README.md#0-provision-the-machine-fresh-ubuntu-2404--native-vm-or-wsl2)
is the canonical, shorter version; this page adds the WSL-specific details.

The toolchain is installed by two scripts in the repo, split by privilege level. Both are
idempotent (safe to re-run) and non-interactive.

**Root half** — system update, and `git`, `git-flow` (the AVH edition `AGENTS.md` §5
requires), `make`, `curl`, `wget`, `gcc`/`g++`/`clang`, `python3`, `pipx`, `htop`, `nano`… plus
the timezone set to UTC. It does *not* install Docker (next step) — and don't pass `--gpu`
on WSL unless you want the CUDA toolkit: the GPU driver is the Windows one.

```bash
sudo ./scripts/setup_sudo.sh
```

**User half** — no `sudo`: `~/.local/bin` on `PATH`, [`uv`](https://docs.astral.sh/uv/)
(manages every backend service's Python ≥ 3.14 — `uv sync` downloads it, no
`apt install python`), `nvm` + **Node.js 22** (what CI and `ui-react` use), `git`'s default
branch, and a coloured prompt. It warns — rather than inventing one — if your git identity is
unset, so set it first:

```bash
git config --global user.name "Your Name" && git config --global user.email "you@example.com"
```

```bash
./scripts/setup_user.sh && source ~/.bashrc
```

Then initialise git-flow in the clone (once):

```bash
git flow init -d
```

For the **Kubernetes (recommended)** path, additionally [`k3d`](https://k3d.io/#installation)
and [`kubectl`](https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/) — Linux binaries,
inside the distro (a Windows-side `kubectl.exe` isn't what `make k3s-*` calls); the scripts
don't cover these:

```bash
curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash
```

```bash
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" && sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl && rm kubectl
```

## 9. Docker — Docker Engine inside the distro

Install **Docker Engine natively inside Ubuntu**, always following the official
[Install Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/) guide (apt
repository method) — the steps below are that page's, reproduced for convenience; if they ever
differ, the official page wins. Docker Engine is Apache-2.0 licensed with no per-seat terms,
which is why this repo doesn't use Docker Desktop.

Ubuntu on WSL2 ships with systemd enabled (`/etc/wsl.conf` → `[boot] systemd=true`), which is
what lets `dockerd` run as a normal service — confirm with `systemctl is-system-running` (any
answer but *"offline"* is fine); if it says the system wasn't booted with systemd, add that line
to `/etc/wsl.conf`, run `wsl --shutdown` from PowerShell, and relaunch.

Inside the distro:

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl
```

```bash
sudo install -m 0755 -d /etc/apt/keyrings && sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc && sudo chmod a+r /etc/apt/keyrings/docker.asc
```

```bash
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null && sudo apt-get update
```

```bash
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

`docker-compose-plugin` is what provides `docker compose` (v2, the only form this repo's
`Makefile` uses). The service is enabled and started for you on Ubuntu:

```bash
sudo systemctl is-active docker && sudo docker run --rm hello-world
```

### Post-install: run `docker` without `sudo`

Every `make` target here calls plain `docker`/`docker compose`, so finish with the official
[Linux post-installation step](https://docs.docker.com/engine/install/linux-postinstall/#add-your-user-to-the-docker-group)
— create the `docker` group and add your user to it:

```bash
sudo groupadd docker
```

(`groupadd: group 'docker' already exists` is fine — the package sometimes creates it.)

```bash
sudo usermod -aG docker $USER
```

Then **log out and log back in** so the group membership is re-evaluated. In WSL, closing the
window isn't enough — exit the shell, then from PowerShell:

```powershell
wsl --terminate Ubuntu-26.04
```

Relaunch the distro and confirm it works without `sudo` — and, while you're at it, that the
whole toolchain from step 8 is on `PATH`:

```bash
docker run --rm hello-world && docker compose version && make --version | head -1 && git flow version && uv --version && node --version && k3d version && kubectl version --client
```

### Keep the virtual disk in check

The distro's whole filesystem — Docker images, build cache and volumes included — lives in one
`ext4.vhdx` on the Windows drive (`%LocalAppData%\wsl\{guid}\` for distros installed with
`wsl --install`, `%LocalAppData%\Packages\CanonicalGroupLimited…\LocalState\` for
Store-installed ones). That file **grows on demand and never shrinks on its own** — `docker
system prune` frees space *inside* Linux, but the `.vhdx` stays as big as it ever was, and when
it fills the Windows drive the VM stalls (see Troubleshooting). Three habits keep it sane:

1. **Compact the `.vhdx` now and then.** Don't reach for `wsl --manage <distro> --set-sparse
   true` — WSL 2.7+ refuses it ("*disabled due to potential data corruption*") unless you pass
   `--allow-unsafe`, and the name is accurate. Instead, TRIM inside the distro first (so the
   compact has zeroed blocks to reclaim), then compact from Windows with the distro stopped:

   ```bash
   sudo fstrim -v /        # inside the distro; systemd also runs this weekly via fstrim.timer
   ```

   ```powershell
   wsl --shutdown
   diskpart
   ```

   …and inside `diskpart`, one line at a time (`Optimize-VHD -Mode Full` does the same if you
   have the Hyper-V PowerShell module):

   ```
   select vdisk file="<path-to-ext4.vhdx>"
   attach vdisk readonly
   compact vdisk
   detach vdisk
   exit
   ```

2. **Cap Docker's build cache.** BuildKit reserves a percentage of the disk by default — on WSL's
   nominally 1 TB virtual disk that's ~100 GB of cache before it garbage-collects anything. Cap
   it in `/etc/docker/daemon.json` inside the distro (create the file if needed), then
   `sudo systemctl restart docker`. `50GB` is deliberate: a full build of the 12 images is ~10 GB
   of layers today, and a cap too close to that makes every rebuild evict the cache it just
   built — leave room for the repo to grow and for a few rebuilds' worth of layers to coexist.

   ```json
   {
     "builder": {
       "gc": {
         "enabled": true,
         "defaultKeepStorage": "50GB"
       }
     }
   }
   ```

3. **Unregister distros you no longer use** — each keeps its own multi-GB `.vhdx`
   (`wsl -l -v` lists them; `wsl --unregister <Distro>` deletes it, *everything inside
   included*). A leftover Docker Desktop install also keeps a `docker-desktop` distro and a
   `docker_data.vhdx` — uninstalling Docker Desktop removes both.

From here, continue with the root README's [Getting started](../README.md#getting-started) from
**step 1** — `make bootstrap`, pick an LLM, then `make k3s-all` (Kubernetes) or `make all`
(Docker Compose) — exactly as on native Linux.

> **Don't mix engines.** If Docker Desktop is also installed on this machine, leave its
> *Settings → Resources → WSL integration* toggle **off** for `Ubuntu-26.04` — otherwise it
> injects its own `docker` CLI into the distro and two engines compete for the same ports.

## Opening the app from Windows

Once `make k3s-verify`/`make verify` passes, open **[http://aiopen.localhost](http://aiopen.localhost)**
in your normal **Windows** browser. No hosts-file edits or extra networking are needed:

- Chrome, Edge and Firefox resolve every `*.localhost` name to loopback themselves.
- The container ports (80 for Traefik, `127.0.0.1:8010` for `platform-registry`) are published
  *inside the distro*, and WSL2's default **localhost forwarding** relays every port listening in
  the distro to Windows `localhost` — so `http://aiopen.localhost`, `http://keycloak.localhost`
  and `http://localhost:8010` all reach the containers from Windows. (If one of them doesn't,
  see the troubleshooting table — the fix is a one-line `.wslconfig`.)
- `make verify`'s `curl` checks inside the distro work for the same reason (curl ≥ 8 resolves
  `*.localhost` on its own, even though `getent hosts aiopen.localhost` returns nothing).

On the Kubernetes path, the standing `kubectl port-forward` the README asks for runs **inside the
distro**, in a shell you keep open:

```bash
kubectl -n ai-circus port-forward svc/platform-registry 8010:8000 &
```

## Editor & AI agents

Install the **WSL** extension in VS Code on Windows, then run `code .` from the repo inside the
distro — the editor's terminal, Python interpreter (`uv`'s `.venv` per service) and the
`.vscode/settings.json` in this repo all resolve inside Ubuntu. Claude Code (or any of the
agents `AGENTS.md` addresses) should likewise be launched from the WSL shell, so `make`, `uv`
and `docker` are the Linux ones.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| *"The command 'docker' could not be found in this WSL 2 distro. We recommend to activate the WSL integration in Docker Desktop settings."* | That's a leftover Docker Desktop shim on the Windows `PATH`, not a real error — step 9 hasn't been done yet. Install Docker Engine inside the distro; `/usr/bin/docker` then takes precedence. |
| `permission denied while trying to connect to the Docker daemon socket` | You're not in the `docker` group yet, or the new membership hasn't been picked up — redo the post-install step in 9, including `wsl --terminate Ubuntu-26.04`. |
| `Cannot connect to the Docker daemon` / `systemctl` says the system wasn't booted with systemd | `dockerd` isn't running. Check `sudo systemctl status docker`; if systemd itself is off, add `[boot]\nsystemd=true` to `/etc/wsl.conf`, `wsl --shutdown`, relaunch. |
| Windows browser can't reach `http://localhost:8010` / `http://aiopen.localhost` although `make verify` passes inside the distro | WSL2's localhost forwarding is off or flaky. In `%UserProfile%\.wslconfig`: `[wsl2]` → `localhostForwarding=true` (the default), or switch to `networkingMode=mirrored` so the distro shares Windows' loopback outright; `wsl --shutdown`, relaunch. |
| `make: command not found`, `uv: command not found`, `node: command not found` | Step 8 above — `sudo ./scripts/setup_sudo.sh` then `./scripts/setup_user.sh`; open a new shell (or `source ~/.bashrc`) afterwards. |
| `Failed to fetch` on the login screen, `make verify` fails on `aiopen.localhost` | Port 80 is taken on the Windows side (IIS / *World Wide Web Publishing Service*, some VPN clients). In PowerShell: `Get-NetTCPConnection -LocalPort 80` shows who owns it. Otherwise follow the README's generic checklist. |
| `/bin/bash^M: bad interpreter`, or `.env` values ending in `\r` | The repo was cloned with Windows `git` (CRLF). Re-clone from inside WSL. |
| Pods stuck `Pending`/`Evicted`, containers OOM-killed, `make k3s-wait` timing out | WSL2 defaults to a fraction of host RAM. Raise it in `%UserProfile%\.wslconfig` (`[wsl2]` → `memory=16GB`, adjust to your machine), then `wsl --shutdown` from PowerShell and relaunch. |
| Cluster/containers eating CPU while idle | `make k3s-pause` (k3d) or `make down` (compose) — or `wsl --shutdown` stops the whole VM; state on named volumes survives. |
| `df -h /` inside the distro jumps by ~70 GB right after the platform starts | Not the images: SeaweedFS pre-allocates 1 GiB × 7 volumes per scenario bucket unless started with `-master.volumePreallocate=false` — which this repo's manifests/compose now do. On a data volume created before that, see the `k3s-deploy-verify` skill's Gotcha 7 to reclaim it without losing data. |
| `wsl.exe`/`cmd.exe` from inside the distro: `cannot execute binary file: Exec format error` | The Windows-binary interop registration dropped (happens e.g. after starting/stopping another distro from inside this one). Docker and everything Linux keep working. Re-register without a restart: `sudo sh -c 'echo ":WSLInterop:M::MZ::/init:PF" > /proc/sys/fs/binfmt_misc/register'` — or just restart the distro. |
| After a WSL restart, `kubectl` answers for a few seconds then `connection refused`; `make k3s-resume` fails with `status=restarting` | k3d node-IP swap — Docker re-assigned the k3d containers' IPs in a different order and k3s crash-loops looking for its old one. Recovery and prevention (`K3S_SUBNET`): `k3s-deploy-verify` skill, Gotcha 8. |
| Windows drive filling up although `df -h /` inside the distro shows plenty free | The distro's `ext4.vhdx` (and any old distro's) has grown and never shrinks by itself — see [Keep the virtual disk in check](#keep-the-virtual-disk-in-check): `fstrim` + `diskpart compact`, cap the build cache, unregister unused distros. |

### When WSL itself misbehaves

**`wsl --install` hangs forever on "Provisioning the new WSL instance…"** — the first-run setup
needs an interactive console to ask for a UNIX username/password. Run from a non-interactive
shell (a script, CI, an agent) it hangs indefinitely, and can wedge the whole WSL service so
that even `wsl --terminate` / `wsl --shutdown` hang too. Kill the stuck processes, then finish
the setup non-interactively:

```powershell
Get-Process wsl, wslhost, vmmemWSL -ErrorAction SilentlyContinue | Stop-Process -Force
wsl -d Ubuntu-26.04 -u root -- passwd <username>   # sets the password without needing a login shell
```

**`passwd: Authentication token manipulation error`** — the account has no password yet
(locked), so `passwd` as that user fails, and an interactive `wsl -u root` login shell gets
hijacked back to the default user by Ubuntu's first-run setup. Set it as a one-off root command
instead — the same `wsl -d Ubuntu-26.04 -u root -- passwd <username>` as above.

**WSL "disconnects", commands hang or fail at random** — usually one of two things:

1. **The Windows drive is full.** The virtual disk lives there; when it fills, the VM
   effectively stalls. Check with `Get-PSDrive C` in PowerShell and free space (see the disk
   section above). Anything that was running inside — a build, the cluster — is gone once WSL
   restarts.
2. **Filesystem corruption.** Inside the distro:

   ```bash
   mount | grep ' / '
   ```

   `emergency_ro` among the mount options means ext4 hit corruption and force-remounted
   read-only. Repair it from *outside* the instance, using any other registered distro:

   ```powershell
   wsl --shutdown
   wsl --mount <path-to-ext4.vhdx> --vhd --bare
   wsl -d <OtherDistro> -u root -- lsblk -f          # find the attached disk, e.g. /dev/sdd
   wsl -d <OtherDistro> -u root -- e2fsck -f -y /dev/sdd
   wsl --unmount <path-to-ext4.vhdx>
   ```

## Contributing from Windows

Nothing Windows-specific beyond the above — the same rules as any other contributor apply:

1. Fork [angelmtenor/ai-circus-framework](https://github.com/angelmtenor/ai-circus-framework) on
   GitHub and clone **your fork** inside WSL (step 7), then steps 8–9 as above.
2. Branch from `develop`: `git flow feature start <name>` (git-flow, per `AGENTS.md` §5;
   Conventional Commits per [`styleguide.md`](../styleguide.md)).
3. Before opening a PR: `make check` inside every service you touched (`make check-all` from
   the root for cross-service changes), `npm run build` in `ui-react/` for frontend changes, plus
   an actual `docker compose up` smoke test of the affected service(s) — CI's
   `compose-validate` never boots containers.
4. Push the `feature/<name>` branch to your fork and open a pull request against **`develop`**
   (never `main`).
