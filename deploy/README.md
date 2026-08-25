# Moving the collector to a server

Order books cannot be backfilled. A laptop that sleeps, reboots for an update,
or gets carried to class loses data that never comes back — and it has already
killed this collector once. Everything here exists to retire that one risk.

Written for Ubuntu 22.04/24.04 on ARM (Oracle Always Free) but nothing is
ARM-specific; any Linux box you control works the same way.

---

## Why not Dartmouth's cluster

Discovery is a Slurm batch cluster. It is built for jobs that request N cores
for M hours and exit, which is the opposite shape from a process that must stay
up for six weeks holding an open network connection. Concretely:

- **Login nodes are not for running processes.** Leaving a long-lived daemon on
  one is the fastest way to have an account suspended on any shared cluster.
- **Compute nodes on many HPC clusters have no outbound internet.** If
  Discovery's do not, the collector cannot reach Kalshi at all — the job would
  start, fail every request, and burn allocation.
- **Access is gated to the campus network or the Dartmouth VPN**, another moving
  part between your process and a six-week uptime target.
- **Your Kalshi private key can place real trades.** Putting it on infrastructure
  someone else has root on is the argument that stands independent of any policy
  question.

On policy: Dartmouth's Acceptable Use Policy allows "appropriate incidental
personal use" and points at restrictions on commercial activity applicable to a
non-profit. A personal trading-adjacent collector running continuously for six
weeks is not obviously incidental. That is a question for
<research.computing@dartmouth.edu>, not for me — but it is worth asking before
rather than after.

**The version of this that does work:** find a faculty sponsor. If this becomes
a research project under someone in Econ, QSS, Tuck or CS, the compute question
answers itself *and* you get something worth more than the compute — a
supervisor who can speak to your work. For a candidate without a PhD, that
reference is a larger asset than a free VM. Worth pursuing on its own timeline,
separately from keeping the collector alive tonight.

---

## 1. Create the box

Oracle Always Free, Ampere A1, Ubuntu 24.04. 1 OCPU / 6 GB is already more than
this needs — the collector is network-bound and idles near zero CPU. Give the
boot volume **at least 50 GB**; the default 46.6 GB works, and collection runs
about 240 MB/day, so six weeks is roughly 10 GB.

In the VCN security list, you only need inbound SSH. The collector makes
outbound HTTPS and needs nothing opened for it.

## 2. Copy the repo up

From your Windows machine, in the repo folder:

```powershell
scp -r -i C:\path\to\oracle_key .\src .\scripts .\deploy .\requirements.txt ubuntu@<SERVER_IP>:/tmp/qr
```

Then on the server:

```bash
sudo mkdir -p /opt/quant-research
sudo rsync -a /tmp/qr/ /opt/quant-research/

# Windows bytecode rides along in __pycache__, compiled for whatever Python is
# installed there. Ubuntu 24.04 ships 3.12; stale .pyc files for other versions
# are inert but are a pointless thing to debug later.
sudo find /opt/quant-research -name __pycache__ -type d -exec rm -rf {} +
```

## 3. Copy the credentials — by hand, once

**Do not paste the private key into a terminal, a chat window, a text editor, or
this file.** Copy it as a file, directly, and nowhere else:

```powershell
scp -i C:\path\to\oracle_key .\certs\kalshi_prod.pem .\.env ubuntu@<SERVER_IP>:/tmp/
```

Only the **prod** key. `certs/kalshi_demo.pem` stays on your machine — the demo
environment serves synthetic books and the server has no use for it, so there is
no reason to put a second credential on a box that already holds one that can
trade.

On the server:

```bash
sudo mkdir -p /opt/quant-research/certs
sudo mv /tmp/kalshi_prod.pem /opt/quant-research/certs/
sudo mv /tmp/.env /opt/quant-research/.env
```

Repoint the key path — the relative `./certs/...` in `.env` resolves against the
working directory, which is not where the service runs from:

```bash
sudo sed -i 's|^KALSHI_PRIVATE_KEY_PATH=.*|KALSHI_PRIVATE_KEY_PATH=/opt/quant-research/certs/kalshi_prod.pem|' /opt/quant-research/.env
grep KALSHI_PRIVATE_KEY_PATH /opt/quant-research/.env
```

Check that one line rather than `cat`-ing the file — `.env` also holds your API
key ID.

`install.sh` locks both down to the service account in the next step.

## 4. Choose the universe — do not carry the old one across

The universe pinned on 2026-08-24 was selected on volume alone, and volume on
Kalshi is concentrated in same-day sports. Checked against the ticker dates:

| | |
|---|---|
| Already settled | 38 of 150 |
| Settles within a week | 20 |
| Settles within the six-week window | 42 |
| Survives the window | 2 dated + 48 undated |

A quarter of it was dead before the server existed, and only about a third would
have lasted the project. Pinning it would have produced a panel that decays to
nothing — the opposite of the problem pinning was meant to solve.

So generate a fresh, long-lived universe on the server after installing:

```bash
sudo -u collector /opt/quant-research/.venv/bin/python \
    scripts/collect_kalshi.py --discover \
    --min-days-to-close 45 --min-volume 100 --max-markets 150
```

`--min-days-to-close 45` drops any family whose **soonest** leg settles inside
the window. A basket needs every leg live simultaneously, so the family's usable
lifetime is its minimum leg, not its maximum. Expect elections, Fed decisions,
macro releases and season-long outcomes rather than tonight's tennis — thinner
books, but they are the only markets that can produce a six-week series at all.

Read the drop tally it prints before committing to the result. If it selects
almost nothing, relax `--min-volume` before relaxing `--min-days-to-close`: a
thin book that lasts is worth more here than a busy one that settles Friday.

When the selection looks right, run it for real — `--save-universe` writes
`data/universe.txt` on every start, so the panel is pinned from that point on
and recoverable afterwards.

## 5. Install and start

```bash
cd /opt/quant-research
sudo ./deploy/install.sh
# step 4 first - the service starts with --tickers-file and will exit
# immediately if data/universe.txt is not there
sudo systemctl enable --now kalshi-collector
sudo systemctl enable --now kalshi-watchdog.timer
journalctl -u kalshi-collector -f
```

You should see a cycle line every 60 seconds within about two minutes.

## 6. Stop the laptop

Once the server has been writing cleanly for ten minutes, stop the Windows
collector. Running both doubles your Kalshi request rate against the same rate
limit and writes two overlapping copies of the same series.

Keep the Windows `data/` directory. It holds 08-23 onward and the server starts
from zero.

---

## Watching it

| | |
|---|---|
| Live log | `journalctl -u kalshi-collector -f` |
| Why did it restart | `journalctl -u kalshi-collector --since '2 hours ago' \| grep -i 'exiting\|Started\|Stopped'` |
| Restart count | `systemctl show kalshi-collector -p NRestarts` |
| Is data fresh | `/opt/quant-research/deploy/healthcheck.sh` |
| Watchdog activity | `journalctl -t kalshi-watchdog` |
| Disk | `df -h /` |
| Full coverage audit | `python3 scripts/audit_coverage.py --data /opt/quant-research/data --per-market` |

`NRestarts` climbing is the number to watch. systemd will keep the collector
alive through a crash loop forever by design, so a rising restart count is the
only visible symptom of a bug that is being papered over.

## Two layers, two failure modes

- **`Restart=always`** handles the process dying.
- **The watchdog timer** handles the process being alive and stuck — a hung
  socket, a wedged DNS lookup, a retry loop with no exit. systemd cannot see
  that, because from its side nothing is wrong. `healthcheck.sh` checks whether
  the data file grew in the last 10 minutes and restarts on stale, which is the
  exact failure this project already hit once.

## Pulling the data back

```powershell
scp -r -i C:\path\to\oracle_key ubuntu@<SERVER_IP>:/opt/quant-research/data/raw .\data\server_raw
```

Analysis runs where you pull it to. The server installs
`requirements-collector.txt` only — four packages, no numpy, no streamlit — so
it stays fast to provision and small to attack.

## If it will not start

```bash
journalctl -u kalshi-collector -n 50 --no-pager
```

- **401 on every request** — check the clock first, not the key.
  `timedatectl status` should say "System clock synchronized: yes". Kalshi signs
  a millisecond timestamp and a few seconds of drift fails every signature.
- **`certs/kalshi_prod.pem` not found** — `KALSHI_PRIVATE_KEY_PATH` in `.env`
  is still a Windows path.
- **Permission denied on the key** — re-run `install.sh`; it chowns secrets to
  the service account.
- **Exits immediately with "nothing selected"** — `data/universe.txt` did not
  make it across, or every pinned market has settled.
