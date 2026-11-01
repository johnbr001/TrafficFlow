# Adaptive Traffic Signal Coordination Prototype

## What this service is for

This project is a local decision-support prototype for studying coordinated traffic signals on a real Korean road map. It combines OpenStreetMap (OSM) roads and junctions, Korean government intersection reference data, SUMO traffic simulation, and a joint reinforcement-learning controller. The Streamlit dashboard guides a user through choosing a study area and traffic scenario, running a simulation, and comparing the proposed controller with a SUMO fixed-time reference.

The city-scale controller's primary learning objective is **network throughput**, measured as completed trips per simulated hour. Queue length, waiting time, and trip time are additional checks. Generated routes and scenario demand are synthetic or assumption-based unless explicitly identified as imported observations. This is a research prototype: it does not control roadside equipment, submit timing changes to a government system, or establish real-world performance.

The current default is a Seoul test area around Jongno-gu. The city selector is driven by `config/korean_regions.json`; availability of government records can vary by city. OSM roads, junction candidates, and potential traffic generators are displayed on the map. Generator importance is a map-category estimate, not a measured traffic count.

## Install on Windows

### 1. Install Python

Install Python 3.11 or later and make sure the Python launcher (`py`) is available in PowerShell. The reinforcement-learning dependencies include PyTorch through Stable-Baselines3, so a working virtual environment and internet access are required during installation.

### 2. Install SUMO

Install Eclipse SUMO using the [official SUMO download instructions](https://sumo.dlr.de/docs/Downloads.php). The dashboard's simulations use the command-line `sumo` program. `sumo-gui` is optional and is only needed to open a simulation visually. The SUMO installation must include its `tools` directory, which contains TraCI and `sumolib`.

Find the SUMO installation directory. On many Windows installations it is similar to `C:\Program Files (x86)\Eclipse\Sumo` or `C:\Program Files (x86)\SUMO`; use the directory that actually contains `tools` and `bin`. Add that directory to `SUMO_HOME` in `.env` (see below). You may also add SUMO's `bin` directory to Windows `PATH`. `SUMO_BINARY` and `SUMO_GUI_BINARY` can be set to explicit executable paths when SUMO is not on `PATH`.

### 3. Create the Python environment and install dependencies

Open PowerShell in the project directory (`E:\Samsung\TrafficFlow` in the current setup):

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks virtual-environment activation, allow scripts for the current terminal only, then activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Copy the example environment file if `.env` does not already exist. Preserve an existing `.env` so that local credentials and SUMO paths are not overwritten:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Open `.env` in a text editor and fill in the settings you use. Keep `.env` private; it is ignored by Git. Never paste API keys into source files or commit them.

| Variable | Required? | What to put there |
| --- | --- | --- |
| `SUMO_HOME` | Required for SUMO runs | Full path to the SUMO installation directory that contains `tools` and `bin`. |
| `SUMO_BINARY` | Optional | Full path to `sumo.exe` if `sumo` is not found on `PATH` or under `SUMO_HOME\bin`. |
| `SUMO_GUI_BINARY` | Optional | Full path to `sumo-gui.exe` if you want to open visual SUMO runs. |
| `DATA_GO_KR_API_ROOT` | Optional | Government API root. Normally leave as `https://apis.data.go.kr/1320000/CrossRoadInfoService`. |
| `DATA_GO_KR_SERVICE_KEY` | Needed to refresh government intersection data | Your data.go.kr service key. Do not include the API endpoint path. |
| `SEOUL_TDATA_API_KEY` | Needed to fetch Seoul road speeds | Your Seoul T-Data key for dataset 1015. |
| `AGENT_PROVIDER` | Optional | Set to `meta` to enable the Meta model when credentials are configured. |
| `MODEL_API_KEY` | Optional | Meta Model API bearer key. `META_API_KEY` is also accepted as a fallback name. |
| `META_MODEL` | Optional | Exact model identifier enabled for your Meta API account. |

Example format (replace paths and key placeholders with your own values):

```dotenv
SUMO_HOME=C:\\Program Files (x86)\\Eclipse\\Sumo
SUMO_BINARY=
SUMO_GUI_BINARY=
DATA_GO_KR_API_ROOT=https://apis.data.go.kr/1320000/CrossRoadInfoService
DATA_GO_KR_SERVICE_KEY=your_data_go_kr_service_key
SEOUL_TDATA_API_KEY=your_seoul_tdata_key
AGENT_PROVIDER=meta
MODEL_API_KEY=your_meta_model_api_key
META_MODEL=your_account_model_identifier
```

The Meta model is optional. The dashboard's SUMO simulation and PPO functions do not require Meta credentials. The public signal-plan JSON API also needs no API key.

### 4. Check the local setup

With the virtual environment activated, run:

```powershell
python scripts/setup_check.py
```

Confirm that Python, SUMO, `SUMO_HOME`, TraCI, and `sumolib` are found. A missing government or Meta key does not prevent a local SUMO simulation; those integrations will be unavailable until configured.

## Run the dashboard

From the project root, with `.venv` activated:

```powershell
python -m streamlit run scripts/dashboard.py
```

Streamlit prints a local address, usually `http://localhost:8501`, and normally opens it in your browser. Keep the terminal running while you use the dashboard. Stop it with `Ctrl+C`.

### First use

1. Open **New simulation** in the left navigation.
2. Choose a city and synchronize its local map and government intersection data. This first download requires internet access, OSMnx/Overpass availability, and `DATA_GO_KR_SERVICE_KEY` for official intersection records. Existing downloaded data is cached locally; refresh is explicit.
3. Describe the situation or use a suggested commute, quiet-traffic, or event scenario. Select the date, time period, study radius, and simulation duration.
4. Review the demand estimate and its assumptions. When T-Data speeds are used, the speed-based conversion is an estimate and not a measured local vehicle count.
5. Run the SUMO simulation. Train a city PPO policy when needed, then evaluate it against the fixed-time reference using the same route demand. Training takes longer than a normal simulation and benefits from more than the short demonstration timestep count.
6. Open **Simulation results** to review the fixed-time/adaptive comparison, throughput, queues, travel times, signal table, map visualization, and generated report. The proposed plan can be downloaded in the government-shaped JSON envelope described below.

Saved maps, routes, simulation output, model files, and reports are under `data/`, `experiments/results/`, `aatc/rl/models/`, and `reports/`. Do not delete these folders if you need to retain local results or cached inputs.

## Reinforcement learning and simulation

SUMO runs a microscopic road-traffic simulation over an OSM-derived network. `randomTrips.py` generates trips for the chosen scenario. The fixed-time reference uses a SUMO signal program generated for the network; it is not automatically the timing plan returned by the government reference API. The coordinated PPO controller chooses hold/switch decisions jointly across the selected signals. SUMO's configured yellow/all-red clearance intervals and minimum/maximum green safeguards constrain those decisions.

The city reward is configured in `config/city_reward_config.json`. Each newly completed trip earns the main positive reward. Normalized network queue and waiting costs are deliberately smaller secondary terms; invalid actions and unnecessary switching are penalized. The dashboard and report show throughput as `completed_vehicles / simulated_hours`. Vehicles still on the road at the end are not counted as completed. This favors progress through the modeled network, but it does not equal the number of cars entering a real intersection or a sensor-measured city traffic count.

For command-line training and evaluation, from an activated environment:

```powershell
python scripts/train_city_rl.py --city Seoul --datetime 2026-11-02T08:00 --duration 900 --radius-km 1.5 --timesteps 2048 --seed 42
python scripts/evaluate_city_rl.py --model aatc/rl/models/city_L01_1p5km_ppo_42.zip --city Seoul --datetime 2026-11-02T08:00 --duration 900 --radius-km 1.5 --seed 42
```

Use the model path printed by the training command; its name depends on selected settings. A city model is tied to its training city and subnetwork radius. Training creates randomized routes for episodes; evaluation compares fixed-time and PPO with paired scenario demand. The example `2048` steps is intended as a short smoke demonstration, not enough evidence of a converged or operationally optimal policy. Compare multiple seeds, dates, and demand levels before drawing conclusions.

## External data and APIs this project requests

| Source | What the service requests or uses | Credential |
| --- | --- | --- |
| OpenStreetMap through OSMnx and the Overpass API | Drivable road graphs, junction candidates, and selected potential traffic generators for the configured study-area place. OSMnx may also use the OSM geocoding service to resolve a place name. Responses are cached under `data/cities/`. | No project API key. Public service availability and fair-use limits apply. |
| Korean Police Agency CrossRoadInfoService at `https://apis.data.go.kr/1320000/CrossRoadInfoService` | `GET /getCrossRoadInfoList` and `GET /getCrossRoadInfoDetail`, with the selected Korean city/region code, paging, JSON response type, and service key. These supply intersection and signal reference records; they are not used as an executable SUMO timing schedule. | `DATA_GO_KR_SERVICE_KEY`; optional root override: `DATA_GO_KR_API_ROOT`. |
| Seoul T-Data, dataset 1015 | An optional helper can request HTTPS data from `https://t-data.seoul.go.kr/apig/apiman-gateway/tapi/TopisIccStTimesLinkTrfSectionStats/1.0` for a selected date. The current guided dashboard does not call this helper. | `SEOUL_TDATA_API_KEY` only if calling the helper directly. |
| Meta Model API | `POST https://api.meta.ai/v1/chat/completions` for the optional traffic assistant and report narrative. | `MODEL_API_KEY` (or `META_API_KEY`) and `META_MODEL`. |
| Korean holiday calendar | The Python `holidays` package supplies holiday dates used by the local scenario calendar. Local event data and rules provide additional date/time demand multipliers. | No API key. |

Requests to OSM and government data require internet access. The application does not query these sources continuously: map/government sync is user-triggered and cached. T-Data dataset 1015 contains speed, not volume. Its client and speed-based estimator remain in `aatc/city_data/real_traffic.py` for explicit Python use, but the current guided dashboard does not expose them. Imported Smart Intersection volume CSVs can be analyzed with `scripts/import_seoul_traffic.py`; current SUMO demand and PPO training are not calibrated automatically from those counts.

## API this project serves

The project includes a separate, read-only HTTP API for retrieving a saved simulated traffic-signal proposal. Start it in a second PowerShell terminal from the project root:

```powershell
.\.venv\Scripts\Activate.ps1
python scripts/signal_plan_api.py
```

By default it listens only on `127.0.0.1:8080`. Available routes:

| Method and path | Purpose |
| --- | --- |
| `GET /health` or `GET /api/v1/health` | Health response. |
| `GET /api/v1/cities/{city}/signal-plans/latest` | Latest saved comparison for a city, for example `/api/v1/cities/seoul/signal-plans/latest`. |
| `GET /api/v1/cities/{city}/signal-plans/{run_id}` | Proposal for a specific saved run ID. |

Example request:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/api/v1/cities/seoul/signal-plans/latest
```

The successful response is JSON in the public service-style `response.header` / `response.body.items.item` envelope. Items retain government field names where available and include a `SIMULATION_PLAN` extension for the proposed time-based SUMO signal schedule. The JSON contains run/scenario metadata and limitations; missing official intersection mappings remain explicit. The API reads saved files and does not run SUMO on demand, modify a government system, or claim that the receiving public service accepts this schema. There is no authentication in this prototype. It binds to localhost by default; only change `--host` for a controlled network deployment.

## What Meta AI and LangGraph do

The traffic assistant uses Meta for natural-language interpretation and explanation. LangGraph runs the Python workflow around the model: it keeps request state, routes between model and local handling, executes approved tools, and returns evidence. Meta does not run SUMO, train PPO, or control a live traffic light. The graph is created in `aatc/agent/workflow.py`; `TrafficAgent.ask()` in `aatc/agent/traffic_agent.py` configures and invokes it.

### Traffic-agent graph: exact nodes and transitions

Each call starts with a new `TrafficAgentState`. It contains the user question, selected mode, conversation messages, the latest model message and tool calls, accumulated tool steps, tool-round count, final result, and any provider error. No conversation history is loaded from earlier user requests.

```mermaid
flowchart TD
    Q[TrafficAgent.ask question] --> INIT[Create state and choose mode]
    INIT --> RM[route_mode node]
    RM -->|mode=model via _select_mode| CM[call_model node]
    RM -->|mode=local via _select_mode| LF[local_fallback node]
    CM --> SEL{_select_model_next}
    SEL -->|No tool calls| FM[finish_model node]
    SEL -->|Calls remain, under limit| ET[execute_tools node]
    ET --> CM
    SEL -->|Round limit reached| TL[tool_limit node raises ProviderError]
    FM --> OUT[Answer, provider, tool steps, evidence]
    LF --> OUT2[Local answer and tool evidence]
    CM -. provider error .-> WRAP[TrafficAgent.ask catches ProviderError]
    TL -. exception .-> WRAP
    WRAP -->|fallback enabled| INIT2[Invoke graph again with mode=local]
    INIT2 --> RM
    WRAP -->|fallback disabled| ERR[Return provider error]
```

| LangGraph node or router | What the implementation does | State passed forward |
| --- | --- | --- |
| `route_mode` | Node function is intentionally a no-op. It marks the graph's routing point; the conditional router `_select_mode` reads `state["mode"]` and chooses the model route or local route. `TrafficAgent` decides the initial mode from `AGENT_PROVIDER`, `MODEL_API_KEY`/`META_API_KEY`, and `META_MODEL`. | Keeps the initial question, messages, counters, and result fields unchanged. |
| `call_model` | Calls `MetaProvider.complete(messages, tool_schemas)`. The provider sends a `POST` to `https://api.meta.ai/v1/chat/completions` with the configured model, system prompt, conversation, and tool definitions. Tool calling is automatic (`tool_choice=auto`) and parallel tool calls are disabled. | Stores the returned assistant message and its `tool_calls`. Invalid response shapes raise `ProviderError`. |
| `_select_model_next` (conditional router) | Examines the last Meta response. It routes to `finish_model` when there are no tool calls, to `execute_tools` when calls remain and the configured limit is not reached, or to `tool_limit` when the limit has been reached. The limit is `max_tool_rounds` in `config/agent.json` (4 by default). | Reads `tool_calls` and `tool_rounds`; does not change state. |
| `execute_tools` | Adds Meta's assistant tool-call message to the conversation. For each call it parses the JSON arguments and calls `TrafficAgentTools.execute(name, arguments)`. The registry rejects unknown tool names and validates inputs against the declared schemas before dispatching to project Python code. It appends each tool result as a tool message for Meta and records the call, arguments, and result in `tool_steps`. | Updates `messages`, `tool_steps`, increments `tool_rounds`, and clears the just-handled tool calls/message. The normal edge loops back to `call_model`, allowing Meta to interpret the evidence or request another allowed operation. |
| `finish_model` | Extracts Meta's final text (including text-block responses) and assembles the model-mode response. | Sets `result` with `answer`, `provider`, `tool_steps`, and `evidence` copied from tool results. Then the graph ends. |
| `tool_limit` | Raises `ProviderError("tool round limit reached")`; it does not silently truncate the model's requested operations. | The exception leaves the graph. `TrafficAgent.ask()` catches it and, when `fallback_to_local` is enabled, starts a new graph invocation in local mode. If fallback is disabled, it returns an error response. |
| `local_fallback` | Calls `TrafficAgent._ask_local()`. This uses deterministic keyword/intent rules to select a tool and fixed bounded arguments. It does not call Meta and is not an LLM. | Sets `result` with the local answer, `provider="local_tools"`, executed tool steps, and evidence; it also includes the provider/fallback reason when applicable. Then the graph ends. |

#### What Meta is asked to do

The system prompt in `aatc/agent/prompts.py` makes SUMO outputs and saved experiment files the source of truth. It tells Meta to request tools for claims about traffic, predictions, controller performance, or signal timing; distinguish measured SUMO results from estimates; report failures or missing evidence; and avoid inventing outcomes. Meta interprets the user request, selects among the tool schemas, supplies bounded JSON arguments, then explains the returned evidence in readable language. It does not get a general-purpose Python or TraCI session.

The available tool names are `simulate_city_scenario`, `evaluate_city_policy`, `run_simulation`, `compare_controllers`, `compare_ppo_with_fixed`, `generate_traffic_scenario`, `optimize_signal`, `predict_traffic`, `inspect_experiment`, and `explain_last_transition`. `TrafficAgentTools` maps those names to specific Python functions. For example, `optimize_signal` only evaluates the allowed bounded timing candidates; `evaluate_city_policy` loads an existing policy and compares it in SUMO; `predict_traffic` calculates a short linear projection from saved samples. Every operation is executed locally and its result is returned to Meta as evidence.

There is no live-signal write tool. Meta's arguments cannot bypass the tool registry or the safety controller inside SUMO. Tool results are simulation/analysis outputs, not instructions to roadside controllers. Requests have no persistent chat memory.

### Separate LangGraph graph: city vehicle-demand estimate

`aatc/city_data/demand_workflow.py` defines a second graph used before city SUMO demand generation. It is not part of the Meta conversation and does not call an LLM:

```mermaid
flowchart LR
    S[Start with network, density, multiplier, duration] --> M[measure_network_lane_kilometers]
    M --> E[estimate_vehicle_count]
    E --> X[Return estimate and assumptions]
```

| Node | Calculation | Output |
| --- | --- | --- |
| `measure_network_lane_kilometers` | Loads the selected SUMO network through `sumolib`, keeps edges that allow passenger vehicles, sums each edge's length multiplied by its lane count, and converts meters to lane-kilometers. It also counts eligible road edges. | `lane_km` and `passenger_edge_count`. The graph stops with an error if the network contains no passenger-accessible lane length. |
| `estimate_vehicle_count` | Computes `base insertion density × scenario multiplier × passenger lane-kilometers × duration seconds ÷ 3600`. | Rounded estimate, input assumptions, effective density, and a note that this is expected departures before route validation/insertion constraints—not an observed count. |

The dashboard displays the estimate for review; SUMO's route generator and insertion rules determine what actually departs. This graph makes the arithmetic and network size explicit, but it does not calibrate synthetic traffic to detector counts.

### Meta-assisted simulation report

The executive report uses Meta for one optional narrative request through `MetaProvider.complete(messages, tools=[])`; this report call is direct and does **not** run through the traffic-agent LangGraph. The report code sends a facts-only JSON bundle derived from the saved comparison and asks Meta for five plain-language sections: decision, test description, changes, meaning of metrics, and next steps. It instructs the model not to invent numbers or causes. The deterministic report renderer supplies the exact before/after table and signal measurements from SUMO data. If credentials are absent or the request fails, it writes the local template instead; previously saved Meta narrative may be retained when regenerating offline.

To check Meta connectivity after setting `.env`, run:

```powershell
python scripts/check_meta_api.py
```

Meta account access, model availability, rate limits, or network connectivity can affect this optional feature. The rest of the dashboard remains usable without it.

## Project layout

- `scripts/dashboard.py` — Streamlit dashboard.
- `scripts/signal_plan_api.py` — read-only simulated signal-plan HTTP API.
- `scripts/run_city_simulation.py`, `scripts/train_city_rl.py`, `scripts/evaluate_city_rl.py` — city SUMO and PPO commands.
- `aatc/city_data/` — OSM/government sync, scenario demand, SUMO city network, PPO, demand calculation, reports, and public JSON serialization.
- `aatc/agent/` — Meta adapter, LangGraph workflow, prompts, allowlisted tools, and local fallback.
- `config/` — city, agent, RL, signal reward, and scenario configuration.
- `data/cities/` — cached map and government data.
- `experiments/results/` — saved SUMO runs, comparisons, and reports.
- `.env.example` — safe template; `.env` — private local settings, not for source control.

## Data quality and limitations

- OSM is a road-map source, not a verified inventory of all live traffic signals. Government records are fetched separately and matched to map junctions conservatively by location.
- The government API's intersection and movement reference fields are not automatically translated to verified signal timings in SUMO. SUMO network programs are generated for simulation.
- Generated trips, default insertion rates, road-speed conversions, and date/event adjustments are model assumptions unless a data source is explicitly shown. Speed is not vehicle volume.
- A completed-trip rate is a useful network-flow proxy inside the selected simulation. A larger number does not by itself establish better real-world operation; inspect queue, waiting, travel time, and junction-level changes too.
- Results cover only the selected map boundary, routes, demand, run length, and random seed. They are not citywide forecasts, field measurements, or an approved recurring signal schedule.
- OSM data is available under the [OpenStreetMap copyright and ODbL terms](https://www.openstreetmap.org/copyright). Government and Seoul data use remain subject to their publishers' terms.
