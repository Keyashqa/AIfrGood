# Ydhya — Frontend

React + Vite clinical dashboard for the Ydhya AI triage system. Doctors use this to intake patients, monitor the live AI pipeline, review results, add clinical notes, download PDF handover reports, and explore platform information.

---

## Pages

| Route | Page | Description |
|-------|------|-------------|
| `/login` | LoginPage | Register / login. JWT stored in `localStorage` |
| `/` | TriagePage | Patient intake form → starts triage → live SSE log panel |
| `/result` | ResultPage | Full AI verdict — risk, priority score, safety alerts, workup table, treatment plan, bridging care, council summary, department routing. Download PDF + save doctor's notes |
| `/council` | CouncilPage | Radar chart + expandable specialist cards with scores, flags, and differentials |
| `/queue` | QueuePage | Priority-sorted table of all active patients with risk/department/alert filters |
| `/analytics` | AnalyticsPage | Stat cards, risk distribution pie, department load bar chart, trend lines, alert frequency |
| `/quick-triage` | QuickTriagePage | Offline ML-only classification for rapid screening (no LLM) |
| `/about` | AboutPage | Platform overview — pipeline explanation, features, audience benefits, tech stack |

---

## State (`src/state/triageStore.js` — Zustand)

| Key | Description |
|-----|-------------|
| `sessionId` | Active triage session ID |
| `verdict` | Enriched CMO verdict object |
| `classification` | XGBoost classification result |
| `specialists` | Array of full specialist opinions |
| `otherSpecialty` | Other-specialty relevance scores |
| `patientData` | Patient vitals and demographics |
| `streamEvents` | SSE log event history |
| `phase` | Pipeline phase (`idle` / `running` / `done`) |
| `token` | JWT token (persisted in `localStorage`) |
| `doctor` | Logged-in doctor profile (includes `facility_level`) |

---

## API Layer

### `src/api/triageApi.js`

| Function | Description |
|----------|-------------|
| `register(username, password, name, facility_level)` | `POST /api/auth/register` |
| `login(username, password)` | `POST /api/auth/login` — returns JWT + doctor profile |
| `updateFacilityLevel(level)` | `PUT /api/auth/facility` |
| `startTriage(data)` | `POST /api/triage` — sends temperature in °F |
| `connectSSE(sessionId, handlers)` | Opens `EventSource` for live pipeline events |
| `getPatients()` | `GET /api/dashboard/patients` |
| `getStats()` | `GET /api/dashboard/stats` |
| `dischargePatient(id)` | `DELETE /api/patients/{id}` |
| `getDoctorNotes(id)` | `GET /api/patients/{id}/notes` |
| `saveDoctorNotes(id, notes)` | `POST /api/patients/{id}/notes` |
| `downloadReport(id)` | `GET /api/patients/{id}/report.pdf` — returns Blob |
| `uploadDocument(file)` | `POST /api/upload/document` |

### `src/api/quickTriageApi.js`

| Function | Description |
|----------|-------------|
| `runQuickTriage(data)` | `POST` to the ML-only server on port `8001` — instant classification without LLM |

---

## Component Structure

```
src/components/
├── common/
│   ├── RiskBadge.jsx          # Colour-coded risk chip (Low / Medium / High)
│   ├── PriorityBadge.jsx      # P1–P4 MOHFW priority label
│   ├── PriorityCircle.jsx     # Circular priority score (0–100)
│   ├── ActionChip.jsx         # Recommended action chip (Immediate / Urgent / Standard / Can Wait)
│   └── FlagChip.jsx           # RED_FLAG / YELLOW_FLAG / INFO chip
├── intake/
│   ├── PatientForm.jsx        # Demographics + vitals + symptoms + conditions form
│   └── DocumentUpload.jsx     # Drag-and-drop document upload
├── stream/
│   └── SSELogPanel.jsx        # Dark terminal-style live pipeline log
├── result/
│   ├── VerdictHeader.jsx      # Risk banner + priority circle + CMO action
│   ├── SafetyAlerts.jsx       # CRITICAL / WARNING alert list
│   ├── ExplanationCard.jsx    # CMO reasoning + key factors
│   ├── WorkupTable.jsx        # Ordered investigations by priority
│   ├── DepartmentRouting.jsx  # Primary + secondary departments
│   ├── CouncilSummary.jsx     # Specialist opinion summary table
│   └── OtherDepartments.jsx   # Additional flagged departments
└── council/
    ├── SpecialistCard.jsx     # Expandable specialist opinion card
    ├── ConsensusBar.jsx       # Council consensus visualisation (Unanimous / Majority / Split)
    └── CouncilRadar.jsx       # Radar chart of specialist relevance vs urgency scores
```

---

## Setup

```bash
cd frontend
npm install
npm run dev       # http://localhost:5173
```

Requires the backend running on `localhost:8000`. Vite proxies all `/api` requests there automatically.

---

## Design System

- **Component library**: Material UI v7
- **Theme**: `src/theme.js` — primary `#1565C0`, secondary `#00897B`, background `#F5F6FA`
- **Typography**: Inter / Roboto, heading weights 700–800
- **Cards**: `borderRadius: 12px`, soft shadow `0 2px 8px rgba(0,0,0,0.08)`
- **Sidebar**: Collapses to 60px icons-only, expands to 220px on hover
- **Logo**: `src/assets/logo.png` — Ydhya Y-mark wordmark
