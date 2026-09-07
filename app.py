"""
Titanic Data App — FastAPI backend + self-contained JS frontend.

- Started by supervisord from `keboola-config/supervisord/app.conf`, not from `pyproject.toml`.
- Listens on 127.0.0.1:8050; nginx publishes it on 8888, the port the platform probes.
- Reads the first CSV under `$KBC_DATADIR/in/tables` and raises if there is none.
"""
import os, math, json
import pandas as pd
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

# ── Config ────────────────────────────────────────────────────────────────────
DATA_DIR = os.environ.get("KBC_DATADIR", "/data/")

app = FastAPI(title="Titanic Data App")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Data loading ──────────────────────────────────────────────────────────────
_cache: pd.DataFrame | None = None

def get_df() -> pd.DataFrame:
    global _cache
    if _cache is not None:
        return _cache
    _cache = _load()
    return _cache

def _load() -> pd.DataFrame:
    tables_dir = os.path.join(DATA_DIR, "in", "tables")
    csv_files = sorted(f for f in
                       (os.path.join(tables_dir, n) for n in os.listdir(tables_dir))
                       if f.endswith(".csv")) if os.path.isdir(tables_dir) else []
    if csv_files:
        return _clean(pd.read_csv(csv_files[0]))
    raise FileNotFoundError(f"No CSV found in {tables_dir}")

def _clean(df: pd.DataFrame) -> pd.DataFrame:
    for c in ["PassengerId","Survived","Pclass","Age","SibSp","Parch","Fare","Age_wiki"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ["PassengerId","Survived","Pclass","SibSp","Parch"]:
        if c in df.columns:
            df[c] = df[c].astype("Int64")
    port_map = {"S":"Southampton","C":"Cherbourg","Q":"Queenstown"}
    for col in ("Boarded","Embarked"):
        if col in df.columns:
            df[col] = df[col].map(lambda x: port_map.get(str(x).strip(), x) if pd.notna(x) else x)
    if "Sex" in df.columns:
        df["Sex"] = df["Sex"].str.strip().str.lower()
    return df

def _nan(v):
    try:
        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            return None
    except Exception:
        pass
    return v

def _port_col(df):
    return "Boarded" if "Boarded" in df.columns else "Embarked"

# ── API routes ────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    try:
        return {"status": "ok", "rows": len(get_df())}
    except Exception as e:
        return {"status": "error", "error": str(e),
                "kbc_token_set": bool(KBC_TOKEN),
                "table_id": TABLE_ID or "(not set)"}

@app.get("/api/stats")
def stats():
    df = get_df()
    total = len(df)
    surv  = int(df["Survived"].sum())
    return {
        "total":     total,
        "survivors": surv,
        "lost":      total - surv,
        "surv_rate": round(surv / total * 100, 1) if total else 0,
        "avg_age":   _nan(round(df["Age"].mean(), 1))  if "Age"  in df.columns else None,
        "avg_fare":  _nan(round(df["Fare"].mean(), 2)) if "Fare" in df.columns else None,
        "max_fare":  _nan(round(df["Fare"].max(),  2)) if "Fare" in df.columns else None,
    }

@app.get("/api/by-class")
def by_class():
    df = get_df()
    out = []
    for cls, label in [(1,"1st Class"),(2,"2nd Class"),(3,"3rd Class")]:
        sub = df[df["Pclass"]==cls]
        if len(sub):
            out.append({"class":cls,"label":label,"total":len(sub),
                        "survived":int(sub["Survived"].sum()),
                        "pct":round(sub["Survived"].mean()*100)})
    return out

@app.get("/api/by-gender")
def by_gender():
    df = get_df()
    out = []
    for sex, label in [("female","Female"),("male","Male")]:
        sub = df[df["Sex"]==sex]
        if len(sub):
            s = int(sub["Survived"].sum())
            out.append({"sex":sex,"label":label,"survived":s,"lost":len(sub)-s})
    return out

@app.get("/api/by-port")
def by_port():
    df = get_df(); col = _port_col(df)
    if col not in df.columns: return []
    total = len(df)
    out = [{"port":str(p),"count":len(g),"pct":round(len(g)/total*100)}
           for p, g in df.groupby(col) if pd.notna(p) and str(p).strip()]
    return sorted(out, key=lambda x: -x["count"])

@app.get("/api/by-age-group")
def by_age_group():
    df = get_df()
    if "Age" not in df.columns: return []
    bins   = [0,12,18,35,60,120]
    labels = ["Child (0–12)","Teen (13–18)","Young Adult (19–35)","Adult (36–60)","Senior (61+)"]
    d2 = df.dropna(subset=["Age"]).copy()
    d2["grp"] = pd.cut(d2["Age"], bins=bins, labels=labels)
    return [{"group":l,"total":len(sub),"survived":int(sub["Survived"].sum()),
             "pct":round(sub["Survived"].mean()*100)}
            for l in labels if len(sub := d2[d2["grp"]==l])]

@app.get("/api/heatmap")
def heatmap(n: int = Query(200, le=500)):
    df = get_df()
    hm = df[["Fare","Survived","Pclass"]].dropna(subset=["Fare"]).sort_values("Fare")
    if len(hm) > n:
        hm = hm.iloc[[int(i*len(hm)/n) for i in range(n)]]
    return [{"fare":round(float(r.Fare),2),
             "survived":int(r.Survived) if pd.notna(r.Survived) else None,
             "class":int(r.Pclass) if pd.notna(r.Pclass) else None}
            for r in hm.itertuples()]

@app.get("/api/passengers")
def passengers(
    q:        str = Query(""),
    survived: str = Query("all"),
    cls:      str = Query("all"),
    page:     int = Query(1,  ge=1),
    per_page: int = Query(50, ge=1, le=200),
    sort_by:  str = Query("PassengerId"),
    sort_dir: str = Query("asc"),
):
    df = get_df(); col = _port_col(df)
    mask = pd.Series(True, index=df.index)
    if q:
        ql = q.lower()
        tm = pd.Series(False, index=df.index)
        for c in ["Name","Hometown","Destination"]:
            if c in df.columns:
                tm |= df[c].str.lower().str.contains(ql, na=False)
        mask &= tm
    if survived == "survived": mask &= df["Survived"]==1
    elif survived == "lost":   mask &= df["Survived"]==0
    if cls != "all":           mask &= df["Pclass"]==int(cls)
    filtered = df[mask]
    if sort_by in filtered.columns:
        filtered = filtered.sort_values(sort_by, ascending=(sort_dir=="asc"), na_position="last")
    total = len(filtered)
    tp    = max(1, math.ceil(total/per_page))
    page  = min(page, tp)
    sl    = filtered.iloc[(page-1)*per_page : page*per_page]
    opt   = lambda c: c if c in df.columns else None
    rows  = []
    for _, r in sl.iterrows():
        def g(c): return _nan(r[c]) if c and c in df.columns and pd.notna(r.get(c)) else None
        rows.append({
            "id":r.get("PassengerId"), "name":r.get("Name","Unknown"),
            "sex":r.get("Sex",""),     "age":_nan(r.get("Age")),
            "cls":_nan(r.get("Pclass")),
            "boarded":g(col),          "dest":g("Destination"),
            "lifeboat":g("Lifeboat"),
            "fare":round(float(r["Fare"]),2) if pd.notna(r.get("Fare")) else None,
            "survived":int(r["Survived"]) if pd.notna(r.get("Survived")) else None,
            "hometown":g("Hometown"),
        })
    return {"total":total,"page":page,"per_page":per_page,"total_pages":tp,"rows":rows}

# ── Serve the self-contained frontend ─────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
@app.get("/{full_path:path}", response_class=HTMLResponse)
def frontend(full_path: str = ""):
    # Skip API routes
    if full_path.startswith("api/"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return HTMLResponse(FRONTEND_HTML)

# ── Self-contained frontend HTML ──────────────────────────────────────────────
FRONTEND_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet"/>
<title>RMS Titanic — Voyage Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--navy:#050d1a;--deep:#081428;--panel:#0a1f3a;--border:#1a3a5c;--gold:#c9a227;--gold2:#f0c850;--blue:#1f8fff;--teal:#00d4b4;--red:#e05c5c;--text:#e8edf5;--muted:#7a9bbf;--glow:0 0 20px rgba(31,143,255,.35)}
html,body{min-height:100vh;background:var(--navy);color:var(--text);font-family:'Inter',sans-serif;overflow-x:hidden}
#stars{position:fixed;inset:0;pointer-events:none;z-index:0}
#ocean{position:fixed;bottom:0;left:0;right:0;height:120px;z-index:1;overflow:hidden}
.wave{position:absolute;bottom:0;left:-200%;width:400%;height:80px;background:linear-gradient(180deg,rgba(8,20,40,0) 0%,rgba(8,20,40,.8) 60%,rgba(5,13,26,1) 100%);border-radius:50% 50% 0 0/30px 30px 0 0;animation:wave var(--s) linear infinite}
.wave:nth-child(2){animation-delay:calc(var(--s)*-.4);opacity:.6}.wave:nth-child(3){animation-delay:calc(var(--s)*-.7);opacity:.4;height:60px}
@keyframes wave{from{transform:translateX(0)}to{transform:translateX(50%)}}
#app{position:relative;z-index:2;max-width:1200px;margin:0 auto;padding:2rem 1.5rem 160px}
header{text-align:center;padding:3rem 0 2.5rem}
.ship-line{display:flex;align-items:center;justify-content:center;gap:1.5rem;margin-bottom:.5rem}
.hline{flex:1;height:1px;background:linear-gradient(90deg,transparent,var(--gold),transparent)}
.anchor{font-size:2rem;animation:bob 3s ease-in-out infinite;display:inline-block}
@keyframes bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-6px)}}
h1{font-family:'Playfair Display',serif;font-size:clamp(2.2rem,5vw,4rem);font-weight:900;letter-spacing:.04em;background:linear-gradient(135deg,var(--gold) 0%,var(--gold2) 50%,var(--gold) 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin-bottom:.25rem}
.subtitle{color:var(--muted);font-size:.85rem;letter-spacing:.25em;text-transform:uppercase}
.date-badge{display:inline-block;margin-top:1rem;padding:.35rem 1.2rem;border:1px solid var(--gold);border-radius:20px;color:var(--gold);font-size:.75rem;letter-spacing:.15em}
.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:1rem;margin:2rem 0}
.kpi{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:1.4rem 1.2rem;position:relative;overflow:hidden;transition:transform .2s,box-shadow .2s;cursor:default;min-height:100px}
.kpi:hover{transform:translateY(-3px);box-shadow:var(--glow)}
.kpi::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:var(--accent,var(--blue))}
.kpi-icon{font-size:1.6rem;margin-bottom:.4rem}.kpi-val{font-family:'Playfair Display',serif;font-size:2.2rem;font-weight:700;color:var(--accent,var(--blue));line-height:1}
.kpi-label{font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;margin-top:.3rem}
.kpi-sub{font-size:.7rem;color:var(--muted);margin-top:.2rem}
.sec-title{font-family:'Playfair Display',serif;font-size:1.35rem;color:var(--gold);margin:2.5rem 0 1rem;display:flex;align-items:center;gap:.75rem}
.sec-title::after{content:'';flex:1;height:1px;background:linear-gradient(90deg,var(--border),transparent)}
.charts-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:1rem}
.chart-card{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:1.5rem}
.chart-title{font-size:.8rem;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;margin-bottom:1.2rem}
.bar-group{margin-bottom:.9rem}
.bar-label{display:flex;justify-content:space-between;font-size:.78rem;margin-bottom:.3rem}
.bar-label .name{color:var(--text)}.bar-label .val{color:var(--muted)}
.bar-track{background:rgba(255,255,255,.05);border-radius:4px;height:10px;overflow:hidden}
.bar-fill{height:100%;border-radius:4px;width:0%;background:linear-gradient(90deg,var(--c1),var(--c2));transition:width 1.2s cubic-bezier(.22,1,.36,1)}
.pie-wrap{display:flex;align-items:center;gap:1.5rem;flex-wrap:wrap}
svg.pie{flex-shrink:0}.pie-legend{display:flex;flex-direction:column;gap:.6rem}
.leg-item{display:flex;align-items:center;gap:.5rem;font-size:.8rem}
.leg-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.heatmap{display:grid;grid-template-columns:repeat(20,1fr);gap:2px;margin-top:.5rem}
.hm-cell{aspect-ratio:1;border-radius:2px;transition:transform .1s}.hm-cell:hover{transform:scale(1.4);z-index:10}
.toolbar{display:flex;flex-direction:column;gap:.6rem;margin-bottom:.5rem}
.search-wrap{position:relative}
#search{width:100%;background:var(--deep);border:1px solid var(--border);border-radius:8px;padding:.8rem 1rem .8rem 2.8rem;color:var(--text);font-size:.9rem;outline:none;transition:border .2s}
#search:focus{border-color:var(--blue);box-shadow:0 0 0 3px rgba(31,143,255,.15)}
#search::placeholder{color:var(--muted)}.search-icon{position:absolute;left:.9rem;top:50%;transform:translateY(-50%);color:var(--muted)}
.filter-row{display:flex;gap:.5rem;flex-wrap:wrap}
.fbtn{background:var(--panel);border:1px solid var(--border);color:var(--muted);padding:.35rem .9rem;border-radius:20px;cursor:pointer;font-size:.75rem;transition:.2s}
.fbtn:hover{border-color:var(--blue);color:var(--blue)}.fbtn.active{background:rgba(31,143,255,.15);border-color:var(--blue);color:var(--blue)}
.tbl-wrap{overflow-x:auto;border-radius:10px;border:1px solid var(--border);max-height:440px;overflow-y:auto}
table{width:100%;border-collapse:collapse;font-size:.8rem}
thead th{background:var(--panel);position:sticky;top:0;z-index:5;padding:.75rem 1rem;text-align:left;font-size:.7rem;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;border-bottom:1px solid var(--border);white-space:nowrap;cursor:pointer;user-select:none}
thead th:hover{color:var(--blue)}.sa{font-size:.6rem;margin-left:.25rem;opacity:.5}.sa.active{opacity:1;color:var(--blue)}
tbody tr{border-bottom:1px solid rgba(26,58,92,.4);transition:background .15s}
tbody tr:hover{background:rgba(31,143,255,.07)}tbody td{padding:.65rem 1rem;color:var(--text)}
.badge{display:inline-block;padding:.2rem .6rem;border-radius:20px;font-size:.68rem;font-weight:600;letter-spacing:.05em}
.b-surv{background:rgba(0,212,180,.15);color:var(--teal)}.b-lost{background:rgba(224,92,92,.15);color:var(--red)}
.b-1{background:rgba(201,162,39,.15);color:var(--gold)}.b-2{background:rgba(31,143,255,.15);color:var(--blue)}.b-3{background:rgba(122,155,191,.15);color:var(--muted)}
.age-bar{display:inline-block;height:6px;border-radius:3px;background:linear-gradient(90deg,var(--blue),var(--teal));margin-left:.4rem;vertical-align:middle;opacity:.7}
.tbl-footer{display:flex;justify-content:space-between;align-items:center;margin-top:.5rem;flex-wrap:wrap;gap:.5rem}
.tbl-info{font-size:.72rem;color:var(--muted)}.pagination{display:flex;gap:.3rem;flex-wrap:wrap}
.pg-btn{background:var(--panel);border:1px solid var(--border);color:var(--muted);padding:.3rem .7rem;border-radius:6px;cursor:pointer;font-size:.75rem;transition:.2s}
.pg-btn:hover:not([disabled]){border-color:var(--blue);color:var(--blue)}.pg-btn.active{background:rgba(31,143,255,.2);border-color:var(--blue);color:var(--blue)}.pg-btn[disabled]{opacity:.3;cursor:default}
@keyframes shimmer{from{background-position:-200% 0}to{background-position:200% 0}}
.skeleton{background:linear-gradient(90deg,var(--panel) 25%,var(--border) 50%,var(--panel) 75%);background-size:200% 100%;animation:shimmer 1.5s infinite;border-radius:8px}
footer{text-align:center;color:var(--muted);font-size:.72rem;margin-top:3rem;padding:1.5rem;border-top:1px solid var(--border)}
@media(max-width:600px){.kpi-val{font-size:1.6rem}tbody td,thead th{padding:.5rem .6rem}}
</style></head><body>
<canvas id="stars"></canvas>
<div id="ocean"><div class="wave" style="--s:8s"></div><div class="wave" style="--s:11s"></div><div class="wave" style="--s:14s"></div></div>
<div id="app">
<header>
  <div class="ship-line"><div class="hline"></div><span class="anchor">⚓</span><div class="hline"></div></div>
  <h1>RMS TITANIC</h1><p class="subtitle">Voyage Dashboard · April 1912</p>
  <div class="date-badge" id="hdr-badge">Loading…</div>
</header>
<div class="kpi-grid" id="kpi-grid">
  <div class="kpi skeleton" style="--accent:#e05c5c"></div><div class="kpi skeleton" style="--accent:#00d4b4"></div>
  <div class="kpi skeleton" style="--accent:#e05c5c"></div><div class="kpi skeleton" style="--accent:#1f8fff"></div>
  <div class="kpi skeleton" style="--accent:#c9a227"></div>
</div>
<div class="sec-title">⚓ Survival Analysis</div>
<div class="charts-row">
  <div class="chart-card"><div class="chart-title">By Passenger Class</div><div id="class-bars"></div></div>
  <div class="chart-card"><div class="chart-title">By Gender</div>
    <div class="pie-wrap"><svg class="pie" width="130" height="130" viewBox="0 0 130 130" id="pie-sex"></svg><div class="pie-legend" id="pie-legend"></div></div>
  </div>
  <div class="chart-card"><div class="chart-title">Boarding Ports</div><div id="port-bars"></div></div>
</div>
<div class="sec-title">🎂 Age &amp; Fare</div>
<div class="charts-row">
  <div class="chart-card">
    <div class="chart-title">Fare Heatmap — sorted by price &nbsp;<span style="font-size:.68rem"><span style="display:inline-block;width:10px;height:10px;background:rgba(0,212,180,.8);border-radius:2px;vertical-align:middle"></span> survived &nbsp;<span style="display:inline-block;width:10px;height:10px;background:rgba(224,92,92,.7);border-radius:2px;vertical-align:middle"></span> lost</span></div>
    <div class="heatmap" id="heatmap"></div>
    <div style="display:flex;justify-content:space-between;margin-top:.4rem;font-size:.65rem;color:var(--muted)"><span>↑ Low fare</span><span>High fare ↑</span></div>
  </div>
  <div class="chart-card"><div class="chart-title">Age Groups — Survival Rate</div><div id="age-bars"></div></div>
</div>
<div class="sec-title">🌍 Voyage Route</div>
<div class="chart-card" style="padding:0;overflow:hidden;border-radius:12px;">
<svg viewBox="0 0 800 300" style="width:100%;display:block;background:var(--deep);">
  <defs>
    <radialGradient id="gO" cx="50%" cy="50%" r="60%"><stop offset="0%" stop-color="#0a1f40"/><stop offset="100%" stop-color="#050d1a"/></radialGradient>
    <filter id="gl"><feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  </defs>
  <rect width="800" height="300" fill="url(#gO)"/>
  <g stroke="rgba(31,143,255,.06)" stroke-width=".5"><line x1="0" y1="75" x2="800" y2="75"/><line x1="0" y1="150" x2="800" y2="150"/><line x1="0" y1="225" x2="800" y2="225"/><line x1="200" y1="0" x2="200" y2="300"/><line x1="400" y1="0" x2="400" y2="300"/><line x1="600" y1="0" x2="600" y2="300"/></g>
  <path d="M155,55 L168,48 L175,58 L172,75 L165,82 L158,78 L152,68 Z" fill="rgba(31,143,255,.15)" stroke="rgba(31,143,255,.4)" stroke-width="1"/>
  <path d="M185,95 L210,88 L218,100 L210,118 L195,115 L183,108 Z" fill="rgba(31,143,255,.15)" stroke="rgba(31,143,255,.4)" stroke-width="1"/>
  <path d="M140,72 L155,68 L158,80 L150,90 L138,86 Z" fill="rgba(31,143,255,.12)" stroke="rgba(31,143,255,.35)" stroke-width="1"/>
  <path d="M580,60 L640,52 L660,75 L650,120 L620,140 L590,130 L570,105 L572,80 Z" fill="rgba(31,143,255,.15)" stroke="rgba(31,143,255,.4)" stroke-width="1"/>
  <path id="rp" d="M163,72 Q200,60 240,95 Q350,130 490,145 Q540,148 615,95" stroke="var(--gold)" stroke-width="2" fill="none" stroke-dasharray="6 4" opacity=".85"/>
  <circle r="6" fill="var(--gold)" filter="url(#gl)" opacity=".9"><animateMotion dur="8s" repeatCount="indefinite" rotate="auto"><mpath href="#rp"/></animateMotion></circle>
  <g transform="translate(490,145)">
    <circle r="10" fill="rgba(224,92,92,.2)" stroke="var(--red)" stroke-width="1.5"><animate attributeName="r" values="10;18;10" dur="2s" repeatCount="indefinite"/><animate attributeName="opacity" values=".8;.3;.8" dur="2s" repeatCount="indefinite"/></circle>
    <circle r="4" fill="var(--red)"/>
    <text y="-16" text-anchor="middle" fill="var(--red)" font-size="9" font-family="Inter">SANK HERE</text>
    <text y="-6" text-anchor="middle" fill="var(--muted)" font-size="7.5" font-family="Inter">41°N 49°W</text>
  </g>
  <g font-family="Inter" font-size="9.5">
    <circle cx="163" cy="72" r="5" fill="var(--teal)" opacity=".9"/><text x="163" y="62" text-anchor="middle" fill="var(--teal)">Southampton</text>
    <circle cx="200" cy="95" r="4" fill="var(--blue)" opacity=".9"/><text x="200" y="86" text-anchor="middle" fill="var(--blue)">Cherbourg</text>
    <circle cx="142" cy="80" r="4" fill="#a78bfa" opacity=".9"/><text x="133" y="71" fill="#a78bfa">Queenstown</text>
    <circle cx="620" cy="95" r="5" fill="var(--gold)" opacity=".8"/><text x="620" y="85" text-anchor="middle" fill="var(--gold)">New York</text>
    <text x="620" y="107" text-anchor="middle" fill="var(--muted)" font-size="8">(never reached)</text>
  </g>
</svg>
</div>
<div class="sec-title">📋 Passenger Explorer</div>
<div class="toolbar">
  <div class="search-wrap"><span class="search-icon">🔍</span><input id="search" type="text" placeholder="Search name, hometown, destination…"/></div>
  <div class="filter-row" id="fbrow">
    <button class="fbtn active" data-f="all">All</button>
    <button class="fbtn" data-f="survived">Survived</button>
    <button class="fbtn" data-f="lost">Lost</button>
    <button class="fbtn" data-c="1">1st Class</button>
    <button class="fbtn" data-c="2">2nd Class</button>
    <button class="fbtn" data-c="3">3rd Class</button>
  </div>
</div>
<div class="tbl-wrap"><table>
  <thead><tr>
    <th data-s="id"># <span class="sa">↕</span></th>
    <th data-s="name">Name <span class="sa">↕</span></th>
    <th data-s="sex">Sex <span class="sa">↕</span></th>
    <th data-s="age">Age <span class="sa">↕</span></th>
    <th data-s="cls">Class <span class="sa">↕</span></th>
    <th data-s="boarded">Boarded <span class="sa">↕</span></th>
    <th>Destination</th><th>Lifeboat</th>
    <th data-s="fare">Fare <span class="sa">↕</span></th>
    <th data-s="survived">Status <span class="sa">↕</span></th>
  </tr></thead>
  <tbody id="tbody"><tr><td colspan="10" style="text-align:center;padding:2rem;color:var(--muted)">Loading passengers…</td></tr></tbody>
</table></div>
<div class="tbl-footer"><span class="tbl-info" id="tcount"></span><div class="pagination" id="pgn"></div></div>
<footer>RMS Titanic · 14–15 April 1912 · Powered by Keboola &amp; FastAPI</footer>
</div>
<script type="module">
// ── Stars ──────────────────────────────────────────────────────────────────
const cv=document.getElementById('stars'),ctx=cv.getContext('2d');
const rsz=()=>{cv.width=innerWidth;cv.height=innerHeight};rsz();window.addEventListener('resize',rsz);
const stars=Array.from({length:220},()=>({x:Math.random()*innerWidth,y:Math.random()*innerHeight*.65,r:Math.random()*1.5+.3,a:Math.random(),da:(Math.random()-.5)*.015}));
(function drw(){ctx.clearRect(0,0,cv.width,cv.height);stars.forEach(s=>{s.a=Math.max(.05,Math.min(1,s.a+s.da));if(s.a<=.05||s.a>=1)s.da*=-1;ctx.beginPath();ctx.arc(s.x,s.y,s.r,0,Math.PI*2);ctx.fillStyle=`rgba(255,255,255,${s.a})`;ctx.fill()});requestAnimationFrame(drw)})();

// ── API ────────────────────────────────────────────────────────────────────
async function api(path){const r=await fetch(path);if(!r.ok)throw new Error(r.status);return r.json()}

// ── Counter ────────────────────────────────────────────────────────────────
function cnt(el,v,fmt=x=>Math.round(x),dur=1400){const s=Date.now();(function t(){const p=Math.min(1,(Date.now()-s)/dur),e=1-Math.pow(1-p,3);el.textContent=fmt(e*v);if(p<1)requestAnimationFrame(t)})()}

// ── Bars ───────────────────────────────────────────────────────────────────
const CL=[['#c9a227','#f0c850'],['#1f8fff','#64b5f6'],['#7a9bbf','#aabfd8'],['#00d4b4','#00ffcc'],['#a78bfa','#c4b5fd'],['#e05c5c','#f87171']];
function bars(id,items,lFn,vFn,sFn){
  const el=document.getElementById(id);el.innerHTML='';
  items.forEach((r,i)=>{const[c1,c2]=CL[i%CL.length],p=vFn(r),dv=document.createElement('div');
    dv.className='bar-group';dv.innerHTML=`<div class="bar-label"><span class="name">${lFn(r)}</span><span class="val">${sFn?sFn(r):p+'%'}</span></div><div class="bar-track"><div class="bar-fill" style="--c1:${c1};--c2:${c2}" data-w="${p}"></div></div>`;
    el.appendChild(dv)});
  requestAnimationFrame(()=>el.querySelectorAll('.bar-fill').forEach(b=>b.style.width=b.dataset.w+'%'))}

// ── Pie ────────────────────────────────────────────────────────────────────
function renderPie(data){
  const pc={'Female survived':'#00d4b4','Female lost':'rgba(0,212,180,.2)','Male survived':'#1f8fff','Male lost':'rgba(31,143,255,.2)'};
  const sl=[];data.forEach(g=>{sl.push({l:`${g.label} survived`,v:g.survived,c:pc[`${g.label} survived`]});sl.push({l:`${g.label} lost`,v:g.lost,c:pc[`${g.label} lost`]})});
  const pt=sl.reduce((a,b)=>a+b.v,0),svg=document.getElementById('pie-sex'),ns='http://www.w3.org/2000/svg';
  let ang=-Math.PI/2;
  sl.forEach(s=>{const sw=s.v/pt*Math.PI*2,x1=65+55*Math.cos(ang),y1=65+55*Math.sin(ang);ang+=sw;const x2=65+55*Math.cos(ang),y2=65+55*Math.sin(ang);
    const p=document.createElementNS(ns,'path');p.setAttribute('d',`M65,65 L${x1},${y1} A55,55,0,${sw>Math.PI?1:0},1,${x2},${y2} Z`);p.setAttribute('fill',s.c);p.setAttribute('stroke','var(--navy)');p.setAttribute('stroke-width','2');svg.appendChild(p)});
  const surv=data.reduce((a,b)=>a+b.survived,0),pct=Math.round(surv/pt*100);
  [[14,'var(--gold)','700',pct+'%',70],[8,'var(--muted)','400','SURVIVED',83]].forEach(([fs,fill,fw,txt,y])=>{
    const t=document.createElementNS(ns,'text');t.setAttribute('x',65);t.setAttribute('y',y);t.setAttribute('text-anchor','middle');t.setAttribute('fill',fill);t.setAttribute('font-size',fs);t.setAttribute('font-weight',fw);t.setAttribute('font-family','Playfair Display,serif');t.textContent=txt;svg.appendChild(t)});
  const lg=document.getElementById('pie-legend');lg.innerHTML='';
  sl.forEach(s=>{lg.innerHTML+=`<div class="leg-item"><div class="leg-dot" style="background:${s.c}"></div><span>${s.l}</span><span style="color:var(--muted);margin-left:auto;padding-left:.5rem">${s.v}</span></div>`})}

// ── Heatmap ────────────────────────────────────────────────────────────────
function renderHeatmap(data){
  const el=document.getElementById('heatmap');el.innerHTML='';
  const mx=Math.max(...data.map(d=>d.fare));
  data.forEach(d=>{const al=0.25+(d.fare/mx)*.75,c=document.createElement('div');c.className='hm-cell';c.title=`£${d.fare} · Cls ${d.class??'?'} · ${d.survived===1?'Survived':'Lost'}`;c.style.background=d.survived===1?`rgba(0,212,180,${al})`:`rgba(224,92,92,${al*.8})`;el.appendChild(c)})}

// ── Table state ────────────────────────────────────────────────────────────
let sF='all',sC='all',sQ='',sBy='id',sDr='asc',pg=1;
const pp=50,CLS=['','1st','2nd','3rd'];

async function loadTable(){
  const p=new URLSearchParams({q:sQ,survived:sF==='survived'?'survived':sF==='lost'?'lost':'all',cls:sC,page:pg,per_page:pp,sort_by:sBy,sort_dir:sDr});
  const d=await api('/api/passengers?'+p);
  const tp=d.total_pages,tot=d.total,s=(d.page-1)*pp;
  document.getElementById('tcount').textContent=`Showing ${(s+1).toLocaleString()}–${Math.min(s+d.rows.length,tot).toLocaleString()} of ${tot.toLocaleString()} passengers`;
  document.getElementById('tbody').innerHTML=d.rows.map(p=>{
    const ab=p.age!=null?`<span class="age-bar" style="width:${Math.min(p.age*1.2,80)}px"></span>`:'';
    return `<tr>
      <td style="color:var(--muted)">${p.id??'—'}</td>
      <td><strong>${p.name}</strong>${p.hometown?`<br><span style="font-size:.68rem;color:var(--muted)">${p.hometown}</span>`:''}</td>
      <td>${p.sex==='male'?'♂':p.sex==='female'?'♀':'—'}</td>
      <td>${p.age??'?'}${ab}</td>
      <td><span class="badge b-${p.cls}">${CLS[p.cls]??'—'}</span></td>
      <td style="color:var(--muted)">${p.boarded??'—'}</td>
      <td style="font-size:.75rem">${p.dest??'—'}</td>
      <td style="color:${p.lifeboat?'var(--teal)':'var(--muted)'}">${p.lifeboat??'—'}</td>
      <td>${p.fare!=null?'£'+p.fare.toFixed(2):'—'}</td>
      <td><span class="badge ${p.survived===1?'b-surv':'b-lost'}">${p.survived===1?'Survived':'Lost'}</span></td>
    </tr>`}).join('');
  // Pagination
  const pgn=document.getElementById('pgn');
  if(tp<=1){pgn.innerHTML='';return}
  const btn=(l,n,dis,act)=>`<button class="pg-btn${act?' active':''}" data-p="${n}" ${dis?'disabled':''}>${l}</button>`;
  let h=btn('«',1,pg===1)+btn('‹',pg-1,pg===1);
  for(let i=Math.max(1,pg-2);i<=Math.min(tp,pg+2);i++)h+=btn(i,i,false,i===pg);
  h+=btn('›',pg+1,pg===tp)+btn('»',tp,pg===tp);
  pgn.innerHTML=h;
  pgn.querySelectorAll('.pg-btn:not([disabled])').forEach(b=>b.addEventListener('click',()=>{pg=+b.dataset.p;loadTable()}))}

// ── Sort ───────────────────────────────────────────────────────────────────
document.querySelectorAll('th[data-s]').forEach(th=>th.addEventListener('click',()=>{
  const c=th.dataset.s;sDr=sBy===c?(sDr==='asc'?'desc':'asc'):'asc';sBy=c;pg=1;
  document.querySelectorAll('.sa').forEach(s=>{s.textContent='↕';s.classList.remove('active')});
  const sa=th.querySelector('.sa');sa.textContent=sDr==='asc'?'↑':'↓';sa.classList.add('active');loadTable()}));

// ── Filters ────────────────────────────────────────────────────────────────
document.getElementById('fbrow').addEventListener('click',e=>{
  const b=e.target.closest('.fbtn');if(!b)return;
  document.querySelectorAll('.fbtn').forEach(x=>x.classList.remove('active'));b.classList.add('active');
  if(b.dataset.f){sF=b.dataset.f;sC='all'}if(b.dataset.c){sC=b.dataset.c;sF='all'}pg=1;loadTable()});

let st2;
document.getElementById('search').addEventListener('input',e=>{clearTimeout(st2);st2=setTimeout(()=>{sQ=e.target.value.trim();pg=1;loadTable()},300)});

// ── Boot ───────────────────────────────────────────────────────────────────
(async()=>{
  try{
    const[stats,byClass,byGender,byPort,byAge,hm]=await Promise.all([
      api('/api/stats'),api('/api/by-class'),api('/api/by-gender'),
      api('/api/by-port'),api('/api/by-age-group'),api('/api/heatmap?n=200')]);
    const k=stats;
    document.getElementById('hdr-badge').textContent=`Southampton → New York · ${k.total.toLocaleString()} passengers`;
    const kd=[
      {i:'👥',v:k.total,l:'Total Records',a:'#e05c5c',f:v=>Math.round(v).toLocaleString()},
      {i:'🛥️',v:k.survivors,l:'Survivors',sb:`${k.surv_rate}% rate`,a:'#00d4b4',f:v=>Math.round(v).toLocaleString()},
      {i:'💀',v:k.lost,l:'Lost at Sea',a:'#e05c5c',f:v=>Math.round(v).toLocaleString()},
      {i:'🎂',v:k.avg_age??0,l:'Avg Age',a:'#1f8fff',f:v=>v.toFixed(1)+' yrs'},
      {i:'🎟️',v:k.avg_fare??0,l:'Avg Fare',sb:`Max £${k.max_fare}`,a:'#c9a227',f:v=>'£'+v.toFixed(2)},
    ];
    const kg=document.getElementById('kpi-grid');kg.innerHTML='';
    kd.forEach(d=>{const dv=document.createElement('div');dv.className='kpi';dv.style.setProperty('--accent',d.a);
      dv.innerHTML=`<div class="kpi-icon">${d.i}</div><div class="kpi-val">0</div><div class="kpi-label">${d.l}</div>${d.sb?`<div class="kpi-sub">${d.sb}</div>`:''}`;
      kg.appendChild(dv);cnt(dv.querySelector('.kpi-val'),d.v,d.f)});
    bars('class-bars',byClass,r=>r.label,r=>r.pct,r=>`${r.survived}/${r.total} (${r.pct}%)`);
    bars('port-bars',byPort,r=>r.port,r=>r.pct,r=>`${r.count} pax (${r.pct}%)`);
    bars('age-bars',byAge,r=>r.group,r=>r.pct,r=>`${r.pct}% survived`);
    renderPie(byGender);renderHeatmap(hm);
    await loadTable();
  }catch(e){document.getElementById('hdr-badge').textContent='⚠ Could not connect to API — '+e.message}
})();
</script>
</body></html>"""