# 🚢 RMS Titanic — Voyage Dashboard

Interactive data app for **Keboola Data Apps**. FastAPI serves a self-contained vanilla JS
dashboard over Titanic passenger data mounted by Keboola Input Mapping.

> **This repo is a CI fixture.** The `keboola/ui` Playwright suite clones `main` on every
> data-app deploy it triggers (`packages/e2e-testing/src/__checks__/e2e/data-apps.test.ts`).
> Breaking `main` turns that lane red. Use a branch for experiments.

---

## Repository structure

```
titanic-python-app/
├── app.py                                  ← FastAPI backend + full HTML/CSS/JS frontend (inlined)
├── pyproject.toml                          ← dependencies (floors, see below)
├── keboola-config/
│   ├── setup.sh                            ← install step, run before the app starts
│   ├── nginx/sites/app.conf                ← listens on 8888, proxies to 127.0.0.1:8050
│   └── supervisord/app.conf                ← starts uvicorn on 127.0.0.1:8050
└── .gitignore
```

---

## Tech stack

| Layer | Technology |
|---|---|
| Server | Python >= 3.11 · FastAPI · Uvicorn |
| Data | Pandas, over the CSV Keboola mounts — no Storage API client |
| Frontend | Vanilla JS · pure SVG charts · CSS animations, all inlined in `app.py` |
| Hosting | Keboola Data Apps, `data-app-python-js` runtime |

No Streamlit. No Node.js. No build step.

---

## Dependencies are floors, not pins

`pyproject.toml` deliberately carries `>=` and no upper caps, and no `uv.lock` is committed.

- The runtime image chooses the interpreter. Keboola offers several `data-app-python-js`
  variants and they differ in Python version (3.11 and 3.13 today).
- A pinned release stops shipping wheels for interpreters newer than itself, and `uv` then
  compiles it from source on every cold start. `pandas==2.2.2` on CPython 3.13 cost
  ~5.5 CPU-minutes that way — far longer than the readiness probe waits.
- `setup.sh` runs `uv sync --no-build`, so a dependency with no wheel for the image's Python
  fails in a fraction of a second and names itself, instead of compiling silently.

Raising a floor is the fix when a new runtime appears. Committing a lock is not: it would hold
the app on the releases that exist today and break on the next interpreter.

---

## Features

- ⚓ **Survival analysis** — by class, gender and age group
- 💰 **Fare heatmap** — passengers sorted by ticket price
- 🌍 **Animated voyage map** — Southampton → Cherbourg → Queenstown → sinking point
- 📋 **Passenger explorer** — server-side pagination, filtering, sorting and full-text search
- ✨ **Animated starfield** + ocean waves background

---

## REST API

All data is served by FastAPI. The JS frontend calls these endpoints at runtime.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Liveness check + row count |
| GET | `/api/stats` | KPI numbers (total, survivors, avg age, avg fare) |
| GET | `/api/by-class` | Survival breakdown by passenger class |
| GET | `/api/by-gender` | Survival breakdown by sex |
| GET | `/api/by-port` | Boarding port breakdown |
| GET | `/api/by-age-group` | Survival rate by age group |
| GET | `/api/heatmap?n=200` | Fare heatmap sample (max 500) |
| GET | `/api/passengers` | Paginated, filtered and sorted passenger table |

### `/api/passengers` query params

| Param | Default | Values |
|-------|---------|--------|
| `q` | `""` | Full-text search across name, hometown, destination |
| `survived` | `all` | `all` / `survived` / `lost` |
| `cls` | `all` | `all` / `1` / `2` / `3` |
| `page` | `1` | Page number |
| `per_page` | `50` | 1–200 |
| `sort_by` | `PassengerId` | Any column name |
| `sort_dir` | `asc` | `asc` / `desc` |

Interactive docs at `/docs` (Swagger UI).

---

## How it runs on Keboola

1. The platform clones this repo into `/app`.
2. `keboola-config/setup.sh` installs the dependencies with `uv`.
3. supervisord starts `uvicorn app:app` on `127.0.0.1:8050`.
4. nginx publishes it on **8888** — the port the platform's readiness probe watches.

Steps 3 and 4 happen only after step 2 finishes, so a slow install shows up as a pod that
never becomes ready rather than as an error.

**Data comes from Input Mapping only.** The app reads the first `*.csv` under
`$KBC_DATADIR/in/tables` (`/data/in/tables` by default) and raises `FileNotFoundError` if there
is none — there is no Storage API fallback and no built-in sample data. Map a Titanic table in
the app's Input Mapping.

No secrets are needed.

---

## Local development

```bash
uv sync
KBC_DATADIR=./testdata uv run uvicorn app:app --port 8050
```

Put a Titanic CSV in `./testdata/in/tables/` first. Then open
[http://localhost:8050](http://localhost:8050).

---

## Expected table columns

Required:

```
PassengerId, Survived, Pclass, Name, Sex, Age, SibSp, Parch, Fare
```

Optional, hidden when absent: `Embarked` or `Boarded`, `Lifeboat`, `Hometown`, `Destination`,
`Age_wiki`.

Port codes are normalised automatically: `S` → Southampton, `C` → Cherbourg, `Q` → Queenstown.
