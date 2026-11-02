# lab-tab

The [nas-tab](../nas-tab) page, for a whole homelab instead of one Docker host. Same page, same `nas.js`
and `nas.css`; only the collector changes.

nas-tab answers "is everything up, what changed, what runs next" for one box by reading Docker, `/proc`
and cron. A lab is a hypervisor cluster with dozens of guests, each running a few services, so
`collector/lab-state.py` reads what a lab usually already has instead:

| Source | What it gives the page | Needs |
|---|---|---|
| **Catalogue** (`catalogue.json`) | groups (drawn as the map), services, where each one runs, its health URL | a JSON file you generate or write |
| **Health probes** | per service: answering, failing (4xx/5xx except 401/403), or not answering | nothing: one GET each, no credentials |
| **Pulse summary** | guest state, CPU and memory per service; Guests / Memory / CPU temp / fullest storage tiles; Pulse alerts and Uptime Kuma monitors in "To do" | a [pulse-kiosk](https://github.com/rcourtman/Pulse) style `/api/summary` URL |
| **NUT** | UPS tile, "UPS on battery" in "To do" | a NUT server; `LIST VAR` needs no login |
| **GitLab** | active pipeline schedules as "Scheduled tasks", with their last result | a `read_api` token |

Everything except the catalogue is optional; a block with no source is hidden instead of shown empty.

`pulse-widgets.yml` adds a side column of plain Glance `custom-api` widgets over the same summary:
nodes (CPU, memory, temperature, load), other hosts, storage bars, Uptime Kuma counts and the event feed.

## How a service gets its state

In this order, the first that applies wins:

1. its guest (`vmid`) is not running in Pulse → **stopped**
2. it has a `health` URL → **healthy** (2xx/3xx/401/403), **unhealthy** (other 4xx/5xx) or **unreachable**
3. an Uptime Kuma monitor has the same name → that monitor's status
4. its guest is running → **running** (no health check)
5. otherwise `assume` from the catalogue, or **unknown**

Problems are loud (`down`) for `critical` and `important` tiers and quiet (`to do`) for the rest. Pulse alerts
that were acknowledged, and Kuma monitors already shown as a service, are not repeated.

## Catalogue

`catalogue.example.json` shows the format: nas-tab's catalogue plus `health`, `vmid`, `where`, `ip`, `tier`,
`facts` (extra `[label, value]` lines on the service sheet) and `assume`. Write it by hand, or generate it
from whatever already describes your lab (a CMDB, an inventory repo, NetBox).

## Install

With Docker (see `docker-compose.example.yml`): Glance and the collector share a small tmpfs volume, both run
as `nobody` with a read-only root filesystem.

1. `config/glance.yml` from `glance.example.yml`, plus `config/pulse-widgets.yml`.
2. `assets/nas.js` and `assets/nas.css` from `../nas-tab/assets`, and an empty `assets/data/` directory.
3. `collector/lab-state.py`, `catalogue.json`, `config.env` from `config.example.env`.
4. Check it: `python3 collector/lab-state.py --config config.env --summary` prints
   `52 / 58 up · 7 problems · 3 tasks` and writes nothing.
5. `docker compose up -d`, open `http://host:8080/lab`.

Without Docker it runs from cron like nas-state.py: `* * * * * python3 /opt/lab-tab/lab-state.py --config /opt/lab-tab/config.env`.

Self-check (offline): `python3 collector/test_lab_state.py`.

## Changes to nas.js

Three small, backwards-compatible additions so the page is not Docker-only:

- `system.containers.label` and `system.disk.label` rename the first and disk tiles ("Guests", "TB · local-zfs pve2").
- `services[].facts` (`[label, value, wide]`) replaces the Docker facts (image, version, uptime) on the sheet.
- `updates` or `tasks` missing from the JSON hides that block; nas-state.py always writes both, so nothing changes there.

## Limitations

- Health probes do not verify TLS certificates (they send nothing and only read a status code).
- Guest CPU and memory are per guest: services sharing a VM show the same numbers.
- No "Recent updates" source yet; the block stays hidden.
