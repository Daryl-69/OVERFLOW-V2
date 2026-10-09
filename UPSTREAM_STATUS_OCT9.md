# Upstream — Status Audit & Remaining Work (Oct 9, 2026)

Audit of updated codebase: **46 Python files** (~283 KB), **4 frontend files** (~83 KB), **11 test files** (~49 KB).
Hackathon: **WeMakeDevs Environmental Hacks** (AWS required). Deadline: ~2–3 days.

---

## 1. Where the project stands now

### 1a. What is built and real

| Area | Code | Status | Notes |
|---|---|---|---|
| **Printable card** (ArUco, checkerboard, chlorine scale, colour patches, QR) | `upstream/card/` | ✅ Real | SVG + raster renderer from one spec |
| **OpenCV reader** (homography, colour correction, CIELAB chlorine, turbidity index, blur/glare checks, EXIF, pHash) | `upstream/reader/core.py` | ✅ Real | Only tested on synthetic photos |
| **Signal levels** (per-tap baselines, IS 10500 0.2 mg/L threshold, complaint-cluster rule) | `locate/levels.py`, `baseline.py` | ✅ Real | |
| **Travel-time fit + pipe-map rebuild from timings** | `locate/traveltime.py`, `rebuild.py` | ✅ Real | Rebuild recovers ~70% of pipe length |
| **Bayesian triangulation** with flow-tree ensemble, Plan B graph rule | `locate/triangulate.py` | ✅ Real | Crown jewel: 16m on synthetic, 39m on Indore |
| **Safe-after advisories, strip requests, sentinel planner** | `advisories.py`, `planner.py` | ✅ Real | |
| **FastAPI intake** (photo upload, manual readings, live state API) | `upstream/intake/app.py` | ✅ Real | Works locally |
| **SQLite store** + **DynamoDB store** (split behind `BaseStore`) | `intake/store.py`, `intake/dynamo.py` | ✅ Real | DynamoDB store built, not deployed |
| **Incident decision engine** (≥3 clustered reds + p_breach ≥ 25%) | `upstream/incident.py` | ✅ Real | |
| **Incident workflow** (agent → engineer approval → alerts) | `intake/workflow.py` | ✅ Real | `LocalWorkflow` + `StepFunctionsWorkflow` |
| **AI incident agent** (Strands/Bedrock Claude + rule-based fallback) | `upstream/agent/` | ✅ Real | 6 tools, en/hi templates, traces |
| **Lambda handlers** (reader, locate, agent, incident status, REST API) | `upstream/cloud/` | ✅ Code built | Not deployed — no SAM template |
| **Ward serialization** (pickle for Lambda cold start) | `upstream/ward.py` | ✅ Real | |
| **Bhagirathpura streets** (real OpenStreetMap) | `data/streets/bhagirathpura.osm.json` | ✅ Real | 81 KB, 700m radius |
| **Bhagirathpura scenario** (simulated outbreak on real streets) | `data/scenarios/bhagirathpura.json` | ✅ Real | Default scenario |
| **Evaluation on Indore streets** | `data/evaluation_bhagirathpura.json` | ✅ Real | 60 runs, strong numbers |
| **Dashboard** (Leaflet, vendored offline) | `dashboard/static/` | ✅ Real | Replay + live + incidents |
| **Dashboard API switching** | `dashboard/static/config.js` | ✅ Real | Local and remote API base |
| **Presigned S3 upload** (cloud handler) | `cloud/handlers.py::_presign()` | ✅ Code built | Needs deployment |
| **Household report page** | `intake/static/report.html` | ✅ Real | |
| **WhatsApp webhook** | `intake/whatsapp.py` | ✅ Real | Mocked in tests |
| **CI** (GitHub Actions) | `.github/workflows/ci.yml` | ✅ Real | |
| **Tests** (11 files) | `tests/` | ✅ Real | Network, reader, locator, app, agent, cloud, incidents, store, export |

### 1b. What is simulated / synthetic

| Component | What's real | What's fake |
|---|---|---|
| **Street grid** | Bhagirathpura roads from OpenStreetMap | — |
| **Pipe network** | — | Shortest-path tree + 35% loops along streets |
| **Households** | — | 140 randomly placed with random habits |
| **Risk attributes** | — | Random pipe age, sewer crossings |
| **Supply schedule** | — | Every 2 days, 6 AM, 2-hour window |
| **Outbreak** | — | Simulated crack + slug physics |
| **Photos** | Reader code is real | Only tested on synthetic images |
| **Evaluation** | Algorithms are real | Data is simulated |

### 1c. What changed since the original audit

| Original gap | Now |
|---|---|
| Zero AWS code | `upstream/cloud/` — 3 files, all Lambda handlers + REST API |
| No agent | `upstream/agent/` — 4 files, Strands/Bedrock + rule fallback |
| No human-approval | `intake/workflow.py` — approve/reject with `waitForTaskToken` |
| No DynamoDB | `intake/dynamo.py` — full `BaseStore` implementation |
| No incident logic | `upstream/incident.py` — red cluster + p_breach rules |
| Synthetic ward only | Real Bhagirathpura streets + scenario committed |
| No Indore evaluation | `evaluation_bhagirathpura.json` — 60 runs |
| `store.py` monolithic | Split into `BaseStore` → `Store` (SQLite) + `DynamoStore` |
| No ward serialization | `upstream/ward.py` — pickle for Lambda cold start |
| Dashboard replay-only | Live intake + incidents panel + config.js switching |
| No AWS deps in pyproject | Added `[aws]`, `[agent]`, `[dev]` optional dep groups |

---

## 2. Current architecture

### Local (working now)

```
Phone → FastAPI /api/photo → OpenCV reader → SQLite
                                                ↓
                                        Levels (🟢🟡🔴)
                                                ↓
                                     Incident decision (≥3 red + p ≥ 25%)
                                                ↓
                                     Agent (rule-based, in-process)
                                                ↓
                                     Engineer: Approve/Reject (dashboard)
                                                ↓
                                     Outbox: advisories (en + hi)
```

### Target AWS (code exists, not deployed)

```
Phone → API Gateway → presigned URL → S3 photos/
                                        ↓ (ObjectCreated)
                     Lambda "reader"  (container: upstream.reader + opencv)
                                        ↓ PutItem
                  DynamoDB  Readings | Meta | Incidents
                                        ↓ (incident opened)
                  Step Functions "IncidentFlow"
                    1. Lambda "locate"   (triangulation on ward.pkl)
                    2. Lambda "agent"    (Strands/Bedrock, waitForTaskToken)
                    3. Engineer Approve/Reject → dashboard
                    4. SNS: boil-water SMS to downstream homes
                                        ↓
                  Dashboard (Leaflet app, pointed at API Gateway)
```

---

## 3. Gap analysis

### P0 (must ship) — by task

| # | Task | Status | What's missing |
|---|---|---|---|
| 1 | Bhagirathpura OSM fetch + scenario | ✅ Done | — |
| 2 | Infra-as-code (SAM): S3, DynamoDB, IAM, API GW | ✅ Done | `aws/template.yaml` (8.8 KB) — S3, 3 DynamoDB tables, 5 Lambda functions, HTTP API, Step Functions, SNS, IAM |
| 3 | Reader Lambda container image | ✅ Done | `aws/Dockerfile` — public.ecr.aws/lambda/python:3.12, opencv-headless, ward.pkl baked in at build time |
| 4 | Store split: SQLite + DynamoDB | ✅ Done | — |
| 5 | Locate Lambda | ✅ Done | Handler + template resource + DynamoDB stream trigger with filter |
| 6 | Step Functions + SNS | ✅ Done | `aws/statemachine/incident.asl.json` — Locate → Agent (waitForTaskToken) → Approve/Reject → SNS Map → MarkNotified |
| 7 | Strands incident agent | ✅ Done | Needs Bedrock model access enabled in AWS Console |
| 8 | Dashboard incidents + Amplify | ⚠️ Partial | Dashboard updated. No Amplify/S3 hosting config. |
| 9 | Presigned upload | ✅ Done | Handler + API route in template |

### P1 (credibility)

| Task | Status | Notes |
|---|---|---|
| Real photos + calibration | ❌ Skip | Can't do field work from different state |
| Seed baseline history | ✅ Script ready | `aws/seed_baselines.py` — needs running against deployed tables |
| Evaluation on Indore | ✅ Done | 97% detection, 0% false alarms, 39m |
| CloudWatch + X-Ray | ❌ Skip | Low hackathon priority |

---

## 4. Remaining work breakdown

### Must do (deployment + testing)

| # | Task | Status | Effort |
|---|---|---|---|
| **A1** | SAM template | ✅ **Already exists** (`aws/template.yaml`, 8.8 KB) | — |
| **A2** | Dockerfile | ✅ **Already exists** (`aws/Dockerfile`) | — |
| **A3** | Step Functions ASL | ✅ **Already exists** (`aws/statemachine/incident.asl.json`) | — |
| **A4** | SAM deploy config | ✅ **Just created** (`aws/samconfig.toml`) | — |
| **A5** | Seed baselines script | ✅ **Just created** (`aws/seed_baselines.py`) | — |
| **A6** | `sam build && sam deploy` + debug IAM/permissions | ❌ **Must do** | 3–5 h |
| **A7** | Run seed script against deployed DynamoDB | ❌ **Must do** | 30 min |
| **A8** | Enable Bedrock Claude model access in us-east-1 | ❌ **Must do** | 15 min |
| **A9** | End-to-end test: photo → S3 → Lambda → DynamoDB → incident → agent → dashboard | ❌ **Must do** | 1–2 h |

### Should build (improves demo)

| # | Task | Effort |
|---|---|---|
| **B1** | Update `config.js` with API Gateway URL | 5 min |
| **B2** | Host dashboard on S3 + CloudFront | 1 h |
| **B3** | Enable Bedrock Claude in us-east-1 | 15 min |
| **B4** | Verify SNS SMS (sandbox phone numbers) | 30 min |

### Skip for hackathon

| Task | Why |
|---|---|
| Real photo calibration | No field access |
| CloudWatch/X-Ray | Cosmetic |
| WhatsApp through AWS | Already works locally, adds deploy complexity |
| Hindi toggle on household page | Nice-to-have |

---

## 5. SAM template spec (what A1 must contain)

### Resources

| Resource | Type | Key config |
|---|---|---|
| `PhotoBucket` | `AWS::S3::Bucket` | Event → ReaderFunction on `s3:ObjectCreated:*`, prefix `photos/` |
| `ReadingsTable` | `AWS::DynamoDB::Table` | PK: `id` (S), PAY_PER_REQUEST |
| `MetaTable` | `AWS::DynamoDB::Table` | PK: `key` (S) |
| `IncidentsTable` | `AWS::DynamoDB::Table` | PK: `id` (S) |
| `UpstreamFunction` | `AWS::Serverless::Function` | PackageType: Image, MemorySize: 2048, Timeout: 120, all env vars |
| `HttpApi` | `AWS::Serverless::HttpApi` | CORS: `*`, routes → UpstreamFunction |
| `IncidentFlow` | `AWS::StepFunctions::StateMachine` | Locate → Agent (waitForTaskToken, 1h timeout) → Approve/Reject → SNS |
| `AlertTopic` | `AWS::SNS::Topic` | Boil-water alerts |

### Step Functions flow (ASL)

```
StartAt → Locate → Agent (waitForTaskToken, 3600s)
  ↓ approved → Prepare → SNS Publish → Mark Notified → End
  ↓ rejected → Mark Closed → End
  ↓ timeout  → Mark Expired → End
```

### API Gateway routes (all → same Lambda, path-based routing)

```
GET  /api/health        GET  /api/live          GET  /api/households
POST /api/presign        GET  /api/result
GET  /api/incidents      GET  /api/incidents/{id}
POST /api/incidents/{id}/approve
POST /api/incidents/{id}/reject
```

---

## 6. Evaluation numbers (for the pitch)

### Synthetic ward

| Metric | Value |
|---|---|
| Detection rate | 80% |
| False alarm rate | 1.7% |
| Median error (with pipe map) | **16 m** |
| Crack in 90% region | **96%** |
| First complaint ticket | 157 m |

### Bhagirathpura / Indore (real streets)

| Metric | Value |
|---|---|
| Detection rate | **97%** |
| False alarm rate | **0%** |
| Median error (with pipe map) | **39 m** |
| Crack in 90% region | **93%** |
| Median error (no pipe map) | 55 m |
| First complaint ticket | **295 m** |

**Headline:** Upstream pinpoints the breach **7.5× closer** than the first complaint ticket, with zero false alarms.

---

## 7. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| SAM deploy fails on IAM | Blocks everything | Start Day 1. Local demo as backup. |
| OpenCV Lambda cold start > 10s | Slow demo | Container + 2GB + provisioned concurrency = 1 |
| Bedrock not enabled in time | No AI agent | Rule-based fallback works identically |
| SNS sandbox blocks SMS | Can't show SMS | Email fallback or console screenshot |
| "All simulated" critique | Judges doubt value | Frame: "The data doesn't exist because nobody has built the collection tool. We built it." |
| Deploy eats all remaining time | No video | **Video > deploy.** If AWS is broken by Day 2 PM, demo locally. |

---

## 8. Timeline (2–3 days)

| When | Task | Fallback |
|---|---|---|
| **Day 1 AM** | Write SAM template + Dockerfile + ward pickle | — |
| **Day 1 PM** | `sam build && sam deploy` — expect 2–3 fix rounds | — |
| **Day 1 Eve** | Test: upload → S3 → Lambda → DynamoDB | If stuck: keep debugging |
| **Day 2 AM** | Seed baselines. Enable Bedrock. Full flow test. | If AWS broken: **commit to local demo** |
| **Day 2 PM** | `config.js` → API GW. Host dashboard. | Dashboard local, API remote |
| **Day 2 Eve** | Script demo video. Practice. | — |
| **Day 3 AM** | **Record. README. Submit.** | — |
| **Day 3** | ⛔ **FREEZE** | — |

---

## 9. Demo video script (≤ 3 min)

| Time | Beat | Screen |
|---|---|---|
| 0:00–0:20 | Indore 2025: 22 deaths from sewage in drinking water | Headlines / probe commission |
| 0:20–0:40 | Nobody used *when* dirty water arrived at each tap | Seismology analogy |
| 0:40–1:10 | **Live:** phone photo → S3 → Lambda → reading in DynamoDB | Phone + console / dashboard |
| 1:10–1:50 | Readings arrive → map lights up → red → incident → agent | Dashboard live / replay |
| 1:50–2:20 | Agent plan in dashboard → engineer clicks Approve → SMS | Incidents panel + phone |
| 2:20–2:40 | "39m vs 295m. 97% detection. Zero false alarms." | Numbers + architecture |
| 2:40–3:00 | Cost: printed card + strips. Next: 50-home pilot. | |

---

## 10. Definition of done

### Full submission
- [ ] `aws/template.yaml` in repo
- [ ] `aws/Dockerfile` builds, Lambda runs
- [ ] Photo → DynamoDB reading in < 15 seconds
- [ ] Red cluster → Step Functions → agent → dashboard
- [ ] Engineer approval gates SNS
- [ ] Bhagirathpura eval numbers in README
- [ ] 3-minute video uploaded

### Minimum viable submission (if AWS deploy fails)
- [ ] Local demo runs on Bhagirathpura
- [ ] SAM template committed (proves architecture is real)
- [ ] Video demos local flow + shows architecture diagram
- [ ] Evaluation numbers in README

---

## 11. File inventory

### Exists ✅

```
upstream/
├── card/          spec.py, render.py
├── reader/        core.py, color.py, synth.py
├── sim/           streets.py, world.py, outbreak.py
├── locate/        baseline.py, levels.py, traveltime.py, rebuild.py,
│                  triangulate.py, advisories.py, planner.py
├── intake/        app.py, live.py, store.py, dynamo.py, workflow.py, whatsapp.py
├── dashboard/     static/ (app.js 48KB, style.css 21KB, index.html 15KB, config.js, vendor/)
├── agent/         runner.py, tools.py, messages.py, __init__.py
├── cloud/         common.py, handlers.py, __init__.py
├── network.py, physics.py, scenario.py, incident.py, ward.py
├── evaluate.py, export.py, geo.py, reports.py, cli.py
data/
├── streets/bhagirathpura.osm.json
├── scenarios/bhagirathpura.json, demo.json
├── evaluation.json, evaluation_bhagirathpura.json
tests/ (11 files)
pyproject.toml, README.md, BUILD.md
```

### AWS infrastructure (all present ✅)

```
aws/
├── template.yaml              ✅ 8.8 KB — full SAM template
├── Dockerfile                 ✅ Lambda container (opencv + ward.pkl baked in)
├── samconfig.toml             ✅ Deploy defaults (just created)
├── seed_baselines.py          ✅ DynamoDB seeding script (just created)
└── statemachine/
    └── incident.asl.json      ✅ Step Functions state machine definition
```

### Remaining action items

```
1. sam build && sam deploy --guided   (first-time deploy, ~20 min build + deploy)
2. Enable Bedrock Claude in us-east-1 (AWS Console → Bedrock → Model access)
3. python aws/seed_baselines.py       (seed DynamoDB with baselines)
4. Update config.js with API Gateway URL from sam deploy output
5. Test end-to-end: phone photo → S3 → Lambda → DynamoDB → dashboard
6. Record demo video
```
