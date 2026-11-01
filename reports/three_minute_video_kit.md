# Three-minute demo video production kit

## What to make

Create a 16:9, 1080p screen-recorded introduction for a government or project-review audience. Use the actual dashboard and SUMO footage. The video should explain the prototype and one specific experiment, not suggest that the system controls live traffic or that a simulation result is a measured city outcome.

Sora is not an option now: OpenAI discontinued the Sora web/app experience on April 26, 2026 and the API on September 24, 2026. This kit is designed for ordinary screen recording and video editing.

## Before recording

1. Open PowerShell in the project directory and activate the environment:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   streamlit run scripts\dashboard.py
   ```

2. In the dashboard, open **Simulation results** and select the completed run you intend to present. Check that the date, scenario, comparison metrics, signal plan, and map all belong to that run. Use a run with understandable results; do not choose a result just because it has a positive number.
3. If you need a new run, go to **New simulation** and complete the three-step flow: describe the scenario, choose date and settings, then review and run. For a short video, use an existing completed run if available; generating a fresh PPO policy can take time and a short training run is not evidence of a converged policy.
4. Close unrelated windows and hide notifications. Never show `.env`, API keys, terminal history, or private file paths. Set the browser zoom so labels and metrics are legible at 1080p.
5. Record separate clips rather than one long take. Pause for two seconds before and after each action so clips are easy to trim. Use 30 fps if available, and move the pointer slowly.

## Three-minute shot list and narration

Narration is about 330 words. Read at a calm pace; leave room for the on-screen evidence. The timing is approximate, so trim pauses rather than rushing the narration.

| Time | What to record / show | Voiceover |
| --- | --- | --- |
| 0:00-0:18 | Results page. Begin on the Seoul study-area map, then show the selected experiment and headline metrics. Title: **Coordinating traffic signals through simulation**. | “Traffic signals can affect how efficiently vehicles move through a road network. This project is a decision-support prototype for exploring coordinated signal strategies in a small Seoul study area. It combines a road map, a traffic simulator, and reinforcement learning so a proposed strategy can be compared with a reference under the same scenario.” |
| 0:18-0:42 | Show the map at a readable scale. Point out the study area and a few intersections. Add a simple label sequence: **OpenStreetMap → SUMO → comparison**. | “OpenStreetMap provides the road network used to build the local test area. Where available, Korean government intersection records provide additional reference information. The selected network is prepared for SUMO, which models vehicles, routes, and signalized junctions. This is a limited study area, not a complete digital twin of Seoul.” |
| 0:42-1:08 | Switch to **New simulation**. Record Step 1 with one suggested prompt or a short scenario prompt. Then show Step 2 date, time, area, and duration controls. | “To define an experiment, the user describes a traffic situation, such as a weekday morning commute, then selects the date, start time, study radius, and simulation length. The scenario settings determine the demand assumptions used to create trips. These generated trips are simulation inputs; they should not be described as observed vehicle counts unless a run explicitly uses measured data.” |
| 1:08-1:34 | Show Step 3 review. Start a prepared run, then cut to actual SUMO-GUI footage if available. If SUMO-GUI is unavailable, show the dashboard’s progress state. | “Before running, Step Three presents a review of the selected settings. SUMO then advances the traffic simulation. The PPO controller observes traffic conditions and selects from permitted signal actions. SUMO applies the signal logic, including transition and safety constraints. This process is an offline experiment: it does not send commands to roadside equipment.” |
| 1:34-2:05 | Return to **Simulation results**. Show the fixed-time and adaptive comparison. Hold on the throughput and completed-vehicle metrics, then show queue, waiting, trip time, and the signal plan. | “The result page compares the adaptive controller with a fixed-time reference using paired scenario demand. Network throughput is the main learning objective and is calculated from completed trips over simulated time. Queue, waiting, and trip time add context. Vehicles still travelling when the simulation ends are not counted as completed. Read the values from this exact run: they describe this SUMO experiment, not measured traffic performance on Seoul roads.” |
| 2:05-2:34 | Show the signal timeline or proposed-plan JSON response. Zoom in enough to read the signal identifier, phases, and durations. Then show the download action if present. | “The proposed plan translates the selected SUMO signal behavior into a time-based schedule and a downloadable JSON response shaped after the public service format. Identifiers and mappings are only as reliable as the available junction data and the network mapping. The export is a prototype proposal for review; it is not a live signal command or a guarantee that a public service will accept it.” |
| 2:34-2:50 | Show a clean, simple overlay or README diagram: **Question → Meta tool choice → validated Python tool → SUMO/data → measured result → explanation**. | “The optional Meta assistant handles natural-language requests and can choose among approved project tools. LangGraph manages that workflow. Python validates and runs the tools, while SUMO and saved experiment data provide the measurements. Meta can explain the evidence, but it does not train PPO by itself or control traffic lights.” |
| 2:50-3:00 | End on the map and result page. Closing title: **A testbed for evaluating signal strategies**. | “The next step is to calibrate traffic demand with reliable field measurements and test across more times, scenarios, and random seeds. The prototype helps make those comparisons visible and reviewable before any real-world use is considered.” |

## Capture checklist

- Capture the results page, map, scenario steps, review screen, SUMO vehicle movement if available, signal timeline, and JSON response as separate clips.
- Keep the selected run name and scenario visible when showing its metrics. Do not combine a map from one experiment with metrics from another.
- When showing improvement, read the actual baseline and adaptive values on screen. State the units and direction (for example, lower average waiting time). If there is no improvement, report that honestly; the demo is about the evaluation workflow, not guaranteed wins.
- Do not claim demand comes from last year's real traffic data unless the exact displayed experiment was calibrated from that data. The current guided dashboard uses scenario-generated demand; the optional T-Data helper is not called by the guided dashboard.
- If using SUMO-GUI, use the configuration and route files for the same experiment being shown in the dashboard. Do not show a separate or unrelated simulation as though it generated the displayed metrics.
- Record voiceover after the screen clips. Add accurate captions, keep music quiet under speech, and avoid covering dashboard metrics with titles.

## Edit plan for the two recordings already captured

The two supplied captures run about 1 minute 47 seconds (Chrome dashboard) and 20 seconds (SUMO GUI), for about 2 minutes 7 seconds total before trimming. The dashboard clip already contains most of the walkthrough. Use short excerpts, then record the voiceover separately and let it carry the explanation during the footage. Avoid slowing the screen recordings just to reach three minutes.

| Approximate final time | Source footage | Edit action |
| --- | --- | --- |
| 0:00-0:08 | Title card | Title: **Coordinating traffic signals through simulation**. Use a quiet map background or a still from the dashboard. |
| 0:08-0:27 | Chrome dashboard, around 0:00-0:18 | Start with the result cards, then show the study map. Keep the actual selected run visible. |
| 0:27-0:40 | SUMO GUI, around 0:03-0:16 | Show the vehicles moving through the junction. Add a small caption: **SUMO simulation · illustrative run**. |
| 0:40-1:02 | Chrome dashboard, around 0:48-1:05 | Show New simulation and the scenario prompt. Cut before any long pause. |
| 1:02-1:24 | Chrome dashboard, around 1:05-1:28 | Show the review screen. Keep settings readable; narrate what the selected date, area, and run length mean. |
| 1:24-1:51 | Chrome dashboard, around 0:00-0:18 | Return to the comparison metrics. Use a brief zoom or crop on throughput, completed vehicles, queue, waiting, and trip time. |
| 1:51-2:13 | Chrome dashboard, around 0:17-0:34 | Show the red/amber/green schedule. Explain that each row is a signal group or controlled movement in SUMO, not necessarily a separate physical signal head. |
| 2:13-2:29 | Chrome dashboard, around 1:36-1:47 | Show the proposed-plan response/download area. Keep API keys and unrelated browser details out of frame. |
| 2:29-2:49 | Chrome dashboard, around 0:34-0:48 | Show the report's decision summary and limitations. The voiceover should identify this as simulated evidence. |
| 2:49-3:00 | End card | **A testbed for evaluating signal strategies**. Add a small note: **Offline simulation · not connected to live signals**. |

This edit uses roughly 1 minute 40 seconds of screen footage and about 20 seconds of SUMO, with title/end cards and narration carrying the remainder. If the selected source moment does not show the item named in a row, scrub the recording and choose the nearest clear moment; the timestamps are approximate, based on the supplied captures.

## Editing sequence

1. Put the clips in storyboard order and trim pauses, loading delays, and mouse travel.
2. Record or generate the narration from the script above. Keep the delivered video close to three minutes; shorten visual holds before cutting important caveats.
3. Add only the short titles listed in the shot list. Use simple cuts or fades; avoid flashy transitions that make a government-facing demo harder to follow.
4. Add captions and check technical terms and place names: **SUMO**, **PPO**, **LangGraph**, and **Jongno**.
5. Export as MP4, 16:9, 1920×1080, 30 fps. Watch the export once from beginning to end and verify the text is readable and every number matches the selected experiment.

## What I need to assemble the actual video for you

This chat cannot capture your locally running dashboard or access the discontinued Sora service. To move from this production kit to a finished edit, record the clips above and place them in a folder; then share or attach them here. I can help sequence and trim the supplied footage, add titles/captions, and prepare an export workflow. The dashboard and SUMO footage must come from your actual local run so the video remains accurate.
