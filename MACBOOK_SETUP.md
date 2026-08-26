# Moving to the MacBook

*Written 2026-08-26, on the last day the Windows desktop is reachable.*

Read this top to bottom **before** the desktop is out of reach. The code is
safe the moment it is pushed. Three other things are not, and two of them
cannot be recovered at any price.

---

## 0. Do these four before you leave the desktop

In order. The first is five minutes, the rest are copying.

### 0.1 Push everything

```powershell
cd C:\Users\lucae\Downloads\quant-research
.\run_tests.cmd            # expect 415 passed
```

Then commit the 5 pending files in GitHub Desktop and **push**. Verify on
github.com that the commit is there. Until it is, the desktop is still the only
copy of the fair-value model.

### 0.2 Copy the raw data — this is the part that cannot be undone

```
data\        461 MB
data_fast\   1.1 GB
```

**Order-book snapshots are point-in-time. There is no endpoint that returns
them retroactively.** The 108,489 two-sided books behind the complementarity
result, the 3.56M-event fast arm, the pinned panel — all of it exists in those
two directories and nowhere else you control. Trades can be re-pulled from the
API at a cost in hours; books cannot be re-pulled at all.

Copy both directories to an external drive, and to cloud storage as well. Two
copies, because a single external drive in a moving box is one drop from being
zero copies. They are gitignored deliberately (`data/`, `data_*/`, `*.jsonl`)
and must never be committed — that stays true, this is a private backup.

`deploy/README.md` anticipated exactly this: *"or gets carried to class loses
data that never comes back."*

### 0.3 Move the credentials as files, never as text

```
certs\kalshi_prod.pem      <- this key can place real trades
certs\kalshi_demo.pem
.env                       <- holds KALSHI_API_KEY_ID and 8 other secrets
```

**Do not open the private key. Do not paste its contents into a terminal, a
chat window, a text editor, a note, or any assistant — including me.** Move it
as a file: external drive, or `scp` directly between the two machines. It is
the only thing in this repo that can move real money.

On the Mac, after copying:

```bash
chmod 600 ~/quant-research/certs/*.pem
```

macOS will not complain the way `ssh` does, but the Kalshi client reads it and
a world-readable private key in a shared home directory is a live risk.

Verify the path afterwards with `grep`, never `cat` — the file also holds the
API key id:

```bash
grep KALSHI_PRIVATE_KEY_PATH ~/quant-research/.env
```

### 0.4 Copy the SSH key for the collector server

The collector runs under systemd at `/opt/quant-research` on an Oracle Cloud
instance and is the *live* half of Project 1. The private key that reaches it
(`oracle_key`, referenced in `deploy/README.md`) is on the desktop, and without
it the server keeps collecting but you cannot log in, check the healthcheck,
restart the unit, or pull the data it has accumulated.

Same rule: move it as a file, `chmod 600`, never paste it. Write down the
server IP somewhere you will still have — it is not in the repo.

---

## 1. Setting up the MacBook

```bash
# Xcode command line tools, if not already present
xcode-select --install

# Python 3.11+ — the repo is tested on 3.11 and 3.13
brew install python@3.13 git

git clone https://github.com/Dodaflop1/quant-research.git
cd quant-research

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

Then put the copied files back:

```bash
cp -R /Volumes/<drive>/data /Volumes/<drive>/data_fast .
cp -R /Volumes/<drive>/certs .
cp /Volumes/<drive>/.env .
chmod 600 certs/*.pem
```

Edit `.env` so `KALSHI_PRIVATE_KEY_PATH` points at the new absolute path.

### Check it works

```bash
python -m pytest tests/unit -q          # expect 415 passed
```

If a test fails on a missing module, the venv is short a declared dependency —
that already happened once on Windows with `scikit-learn`, and the failure was
worth having, because `bimodality()` had been silently answering "not bimodal"
whenever the mixture fit could not run.

### The `.cmd` scripts are Windows-only

`run_tests.cmd`, `run_collector.cmd`, `run_backfill.cmd`, `audit_data.cmd`,
`discover_universe.cmd`, `tail_log.cmd`, `tally_trades.cmd` and
`run_recovery.cmd` will not run. Each is two or three lines wrapping a Python
call — read the one you need and run its command directly, or write the `.sh`
equivalents as a small first commit on the Mac. `run_tests.cmd` is just:

```bash
.venv/bin/python -m pytest -q
```

---

## 2. Where the work stands

Everything below is in the repo. `TASKS.md` is the live checklist and
`HANDOFF.md` the longer orientation; `CONTEXT_BUNDLE.md` exists for briefing a
model that cannot see the repo.

**Project 2 (diffusion) is the stronger half.** Its headline claim —
reflexivity in trade arrivals — was found, doubted, tested and substantially
retracted by the project's own controls. A 2× rate step fabricates a branching
ratio of 0.799 on data with zero self-excitation, and the time-rescaling
diagnostics rejected 0 of 24 such fits. The largest open question is the 61–72%
diagnostic rejection rate, unexplained after four attempts.

**Project 1 (Kalshi) is the thinner half.** Structural arbitrage is closed in
every direction and at every field size — that is a real result, but a result
about what is not there. Market calibration is measured and the power analysis
is the finding: 398 markets give 7% power against a favourite-longshot bias, so
"not rejected" means "could not have detected it".

**The fair-value model is written, tested, and refuses to quote** until its
forecast-error distribution is fitted. See `docs/fair_value_temperature.md`,
including the correction: most daily temperature series settle on The Weather
Company, not the NWS, and only a handful can be fitted from free data at all.

### Next, in order

1. **Run the 10,000-market calibration collection.** Resume-safe now, so it can
   be interrupted and restarted freely. This is the prerequisite for any claim
   about favourite-longshot bias, and it can run unattended while you unpack.
   ```bash
   python scripts/market_calibration.py --max-markets 10000 --rate 2.0
   ```
2. **Decide the temperature domain question** — the three routes are laid out
   at the end of `docs/fair_value_temperature.md`.
3. **The backtest engine.** Chronological replay, no lookahead, fills capped at
   observed depth. Largest remaining gap in Project 1 and the thing an
   interviewer opens first.

---

## 3. Two habits worth keeping

**Probe the interface before building on it.** Nearly every failure this
project has paid for came from building on an assumed payload shape that
produced a plausible-looking number: `volume` where the API returns
`volume_fp`, an alphabetical slice mistaken for a random sample, a page cap
mistaken for an exhausted cursor, a help-centre article mistaken for the
per-series settlement field. `scripts/inspect_payload.py` and
`scripts/probe_weather.py` exist for this and cost one request each.

**Check `git rev-parse HEAD` before trusting a number from another machine.**
A stale clone in a session container disagreed with the working copy by eight
tests, and the disagreement was invisible until the totals were compared.

**A silent cap is a lie.** If something bounds coverage — top-N, a page limit,
a sampling fraction — it has to say what it dropped. "Nothing found" and
"stopped looking" reading the same is how the weather probe reported an empty
universe that had 365 series in it.
