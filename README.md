# Lumen: any idea, beautifully explained

Type a question like *"Explain how self-attention works in Transformers"* and get a freshly generated
**30-second animated explainer video** in the style of 3Blue1Brown. The video is drawn with
[Manim](https://www.manim.community/), and its captions are burned into the frame. Nothing is pre-rendered
or matched to a topic. Every lesson is planned by an LLM, every scene is written as Manim code by an LLM,
and that code is rendered in an isolated Docker sandbox.

```
User prompt
  → AI lesson planner         (Claude: structured JSON storyboard, validated with Pydantic)
  → Storyboard                (3–4 scenes, exact timings, captions, animation beats)
  → AI Manim code generator   (Claude: a Python file written against our design system)
  → Manim renderer            (Docker: no network, read-only, CPU/memory/time limits)
  → Video validator           (ffprobe: duration, resolution, codec, decodes cleanly)
  → MP4                       → browser player (Next.js)
     ↺ if the code fails to render, Claude repairs it from the traceback (up to 2 repairs)
```

## Quick start

Prerequisites: **Python 3.12**, **Node 20+**, **Docker Desktop** (running), and an **Anthropic API key**.

```bash
cp .env.example .env          # then set ANTHROPIC_API_KEY (and ANTHROPIC_WORKSPACE_ID if your key needs it)
make setup                    # backend venv, frontend npm install, builds the renderer image (~2 min)
make check                    # confirms Python, Node, Docker, the renderer image and the key are all present
make dev                      # backend on :8000 and frontend on :3000; Ctrl-C stops both
```

Open <http://localhost:3000>, click an example (or type your own question), then click
**Generate Explanation**. A video typically takes **1½–4 minutes**: about 20 s to plan, 40–170 s to
write the animation code, about 20 s to render at 1080p, and 1 s to validate.

| Command | What it does |
|---|---|
| `make setup` | `setup-backend` + `setup-frontend` + `renderer` |
| `make renderer` | `docker build -f docker/Dockerfile.renderer -t explainer-manim-renderer:0.19.2 docker/` |
| `make dev` | Start both servers (`RELOAD=1 make dev` for backend autoreload) |
| `make backend` / `make frontend` | Start one side only |
| `make test` | Backend test suite (216 tests; 11 of them render for real in Docker) |
| `make check` | Report which prerequisites are present |

## Configuration (`.env` in the repo root)

The backend reads `.env` on startup. The key is used server-side only. It is never sent to the browser,
and never passed into the render sandbox. `.env` is gitignored. **Never put a real key in `.env.example`.**

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required for live generation |
| `ANTHROPIC_WORKSPACE_ID` | — | Only needed for user-scoped keys (`sk-ant-usr-…`); the API returns a clear error if it's missing |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | Or set `PLANNER_MODEL` / `CODEGEN_MODEL` separately |
| `PLANNER_EFFORT` / `CODEGEN_EFFORT` / `REPAIR_EFFORT` | `medium` / `medium` / `medium` | `low`…`max`. `CODEGEN_EFFORT=high` thinks longer (codegen took 44–255 s at `high`, against 117 s at `medium` on the same storyboard) |
| `ANTHROPIC_REFUSAL_FALLBACK` | `true` | Server-side refusal fallback (`fallbacks: "default"`); switches itself off automatically if the API rejects it |
| `RENDER_WIDTH` × `RENDER_HEIGHT` @ `RENDER_FPS` | 1920×1080 @ 30 | 1280×720 renders about 20% faster |
| `RENDER_TIMEOUT_S`, `RENDER_CPUS`, `RENDER_MEMORY` | 420, 3, 3g | Sandbox limits |
| `MAX_REPAIR_ATTEMPTS` | 2 | LLM repairs after a failed render |
| `TARGET_DURATION_S`, `DURATION_TOLERANCE_S` | 30, 3 | The validator rejects videos outside 27–33 s |
| `ALLOW_OFFLINE_DEMO` | `true` | Enables the explicitly labelled offline demo (see below) |
| `JOB_TTL_HOURS`, `MAX_CONCURRENT_JOBS` | 6, 2 | Old job folders are deleted automatically |
| `BACKEND_URL` | `http://127.0.0.1:8000` | Where Next.js proxies `/api/*` (must be set before `next dev`/`next build`) |

## How it works

### 1. Lesson planner: [`lesson_planner.py`](backend/app/pipeline/lesson_planner.py), [prompt](backend/app/pipeline/prompts/planner_system.md)
One Claude call with structured output (`output_config.format` = the JSON schema of a draft model). The
prompt teaches the model to pick **one idea** and **one concrete running example with specific values**,
reused in every scene, arranged as hook → mechanism → payoff. Captions must fit a reading-speed budget of
about 15 characters per second, and visuals must stay within the 2D vocabulary the design system can draw.
Code then normalizes the draft: scene durations are rescaled to add up to exactly 30 s and start times are
computed. It is then validated against [`Storyboard`](backend/app/pipeline/schema.py), which checks the
contiguous timeline, caption reading speed, plain-text captions and MathTex-ready LaTeX. If validation
fails, the planner retries once with the errors as feedback.

### 2. Manim code generator: [`manim_generator.py`](backend/app/pipeline/manim_generator.py), [prompt](backend/app/pipeline/prompts/codegen_system.md)
One streamed Claude call that receives the whole storyboard, the design-system API reference, the scene
interface, the Manim CE v0.19.2 pitfalls, and layout, pacing and accuracy rules. It writes one Python file:
`class ExplainerVideo(ExplainerScene)` with one `with self.scene_block(...)` per storyboard scene. As the
code streams in, the UI shows "Animating scene k of n". The code then passes a static check
(syntax, required structure, import allowlist, no file/network/`eval` usage) before it is rendered.

### 3. Design system: [`backend/manim/visual_helpers.py`](backend/manim/visual_helpers.py)
Reusable primitives, not scenes:
- **Palette and typography:** navy background, blue/purple/cyan/yellow accents, Inter.
- **Layout areas:** a reserved caption band and header.
- **Building blocks:** token rows, arrays with pointers, weighted edges and graphs, axes and plots with
  tangents, bar charts, equations, callouts, braces, and animation helpers.

Two guarantees are built in:
- **Timing:** `scene_block` counts the frames actually written. It shortens any animation that would
  overrun its scene and pads the end, so every scene lasts exactly its storyboard duration and the video
  is the sum of the durations, give or take one frame.
- **Captions:** they always come from the storyboard text, verbatim, in a consistent style. A layout guard
  keeps content out of the caption and header bands.

### 4. Sandboxed renderer: [`renderer.py`](backend/app/pipeline/renderer.py), [`Dockerfile.renderer`](docker/Dockerfile.renderer)
The generated Python never runs in the API process. Each attempt runs:

```
docker run --rm --network none --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges
  --user 1000:1000 --memory 3g --memory-swap 3g --cpus 3 --pids-limit 512
  -v backend/manim:/opt/explainer:ro  -v <attempt dir>:/work:rw
  explainer-manim-renderer:0.19.2 manim render scene.py ExplainerVideo --resolution 1920,1080 --fps 30 ...
```

- The docker CLI is started with a scrubbed environment (no `ANTHROPIC_*`).
- Renders run one at a time, under a hard timeout that ends in `docker kill`.
- If Docker isn't available, jobs fail with a clear error. There is never a fallback to running on the host.
- Tests check that the sandbox has no network, can't see host files, receives no secrets, and that runaway
  code is killed.

### 5. Repair, validation, and the job API
- **Repair:** on a failed render, the traceback tail goes back to Claude in a separate repair call, which
  returns a corrected file. A static-check failure gets the same treatment. Up to 2 repairs.
- **Validation:** [`validator.py`](backend/app/pipeline/validator.py) runs ffprobe and a full decode.
  The video must be 27–33 s long, at the configured resolution, H.264 yuv420p, and decode without errors.
  A bad video is never reported as a success.
- **Job API:** [`main.py`](backend/app/main.py) and [`jobs.py`](backend/app/jobs.py) keep jobs in memory
  with a small thread pool; temporary files live under `backend/.jobs/<job_id>/`. There is no database.

| Endpoint | |
|---|---|
| `POST /api/generate` `{"prompt", "mode"?: "live"\|"offline_demo"}` | → `{"job_id"}`; 422 for an empty or too-long prompt, 503 if no API key |
| `GET /api/jobs/{id}` | status, `stage_index` (0–4), stage label, `detail`, storyboard summary, `video_url`, `error` |
| `GET /api/jobs/{id}/video` | the MP4 (supports Range for seeking; `?download=1` downloads it) |
| `GET /api/jobs/{id}/code` | the generated Manim Python |
| `GET /api/jobs/{id}/debug` | storyboard, per-attempt errors and render logs, timings |
| `GET /api/health` | API key configured? Docker and image present? |

### 6. Frontend: [`frontend/`](frontend/)
Next.js 16, React, TypeScript, Tailwind, lucide-react and motion.
- **Landing:** examples, inline validation.
- **Generation page:** polls the job every second. It shows the five real pipeline stages, the live
  sub-status, and the AI's lesson plan as soon as it exists. Progress is never faked.
- **Result page:** the video player with clickable chapters, plus Replay, Download MP4 and Generate Another.
- Responsive down to 390 px. The browser only talks to Next.js, which proxies `/api/*` to FastAPI.

## Inspecting what the AI generated

Every job keeps its artifacts in `backend/.jobs/<job_id>/`:

```
prompt.txt            the question
storyboard.json       the validated lesson plan
attempts/<n>/scene.py the code rendered in attempt n (1 = first generation, 2+ = repairs)
attempts/<n>/render.log  sandbox stdout/stderr, including EXPLAINER_TIMELINE lines (planned vs actual per scene)
attempts.json         per-attempt stage, error excerpt, timeline, layout fixes
scene.py              the final code that rendered successfully
final.mp4             the validated video
```

In the browser, use **View generated code** on the result page, or open `/api/jobs/<id>/code` or
`/api/jobs/<id>/debug`. You can also run any stage on its own:

```bash
cd backend
.venv/bin/python -m app.pipeline.lesson_planner "How does binary search work?"   # prints a storyboard (live API)
.venv/bin/pytest -m live -s tests/test_lesson_planner.py                         # plans all three demo prompts (live API)
```

## Offline demo mode (explicitly labelled)

With no API key, the landing page shows a banner and a **Render offline demo** button. It renders a
**pre-written** sample lesson (the Pythagorean theorem, in
[`backend/manim/offline_demo/`](backend/manim/offline_demo/)) through the real sandbox and validator, so
you can check Docker rendering without AI. The user's prompt is ignored. The job reports
`mode: "offline_demo"`, and the UI shows an "Offline demo — pre-written sample lesson, not AI-generated"
badge. It is never presented as live generation.

## Verified results (Apple Silicon Mac, Docker Desktop 4 CPUs / 8 GB, `claude-opus-5-5`)

All runs below are live, end-to-end, through the real pipeline:

| Prompt | Total time | Renders | Duration | Notes |
|---|---|---|---|---|
| Explain how self-attention works in Transformers. | 202 s | 1 | 30.000 s | "the river bank": scores 0, 1.8, 1.1 → softmax 0.1, 0.6, 0.3 |
| Explain gradient descent visually. | 143 s | 1 | 30.000 s | y = x² from x = 3, steps 3 → 1.5 → 0.75 → 0.375 |
| How does binary search work? | 82 s | 1 | 30.000 s | midpoints 12 → 23 → 16, found in 3 checks vs 7 |
| Explain how neural networks learn. (driven through the UI with Playwright) | 236 s | 1 | 30.000 s | E = (2w − 6)², w: 1 → 2 → 3, error 16 → 4 → 0 |
| Explain how self-attention works in Transformers. (UI, second run) | 341 s | 1 | 30.000 s | "The cat sat": scores 1, 3, 2 → weights 0.09, 0.67, 0.24 |
| The same storyboard, codegen at `medium` effort | 136 s (codegen + render) | 1 | 30.000 s | As correct, and better centred |
| Pre-written code with an injected `NameError` | 59 s | 2 | 30.000 s | Render failed → Claude repair (35 s) → re-render passed |

- **Effort:** the five live prompt runs used `CODEGEN_EFFORT=high`. It is now `medium` by default, because
  a side-by-side test on the same storyboard took half the time at equal quality. That comparison is a
  single sample; set `CODEGEN_EFFORT=high` to go back.
- **Timing and layout:** every scene matched its planned duration exactly (0 clamped animations, 0 layout
  corrections).
- **Cost:** about $0.45 per video at `high`; `medium` used about 25% fewer output tokens on the comparison run.
- **Caching:** confirmed for the system prompts (`cache_read_input_tokens` > 0 on repeat runs).

## Known limitations

- **Generation time:** typically 1½–4 minutes, dominated by the codegen call's thinking, which varies
  from run to run (one `high`-effort run took 5.7 minutes in total).
- **Composition** is good but not perfect: some videos sit left-of-centre or leave empty space early in a
  scene while they wait for elements that arrive later. The layout guard prevents overlap with the caption
  and header bands, but it can't detect two content objects overlapping each other.
- **Timing guarantees can cost animations:** if the generated code asks for more time than a scene has,
  the trailing animations are shortened or skipped. This is logged in `attempts.json`.
- **Paused video:** the browser's native video controls cover the bottom of the burned-in caption while
  the video is paused.
- **Scale:** single process, in-memory jobs (lost on restart), one render at a time. Non-Latin captions
  depend on the fonts installed in the renderer image.

## Extending it

The stages are deliberately separate, so most extensions add a stage instead of rewriting one:
- **PDF ingestion:** add an extraction step before `plan_lesson`. The planner already treats the request
  as data inside tags, so extracted text can be passed the same way, and the `Storyboard` contract stays
  the same.
- **Voiceover:** each scene already has a caption and an exact `start_time`/`duration`. Generate TTS per
  scene, then mux the audio with ffmpeg after validation; the scene timings already line up with the captions.
- **Longer videos:** raise `TARGET_DURATION_S` and the scene limits in `schema.py` (`MAX_SCENES`,
  `MAX_SCENE_SECONDS`). Rendering and timing scale linearly.

## Project layout

```
backend/
  app/
    main.py, models.py, jobs.py          FastAPI app, API models, in-memory job manager
    config.py, llm.py                    settings from .env; the single Anthropic client wrapper
    pipeline/
      lesson_planner.py, schema.py       prompt → validated Storyboard
      manim_generator.py                 Storyboard → Manim code, static check, LLM repair
      renderer.py, validator.py          Docker sandbox render; ffprobe validation
      production.py                      generate → render → repair → validate loop
      orchestrator.py, offline_demo.py   runs a job end to end
      prompts/                           planner and codegen system prompts
  manim/visual_helpers.py                the design system (mounted read-only into the sandbox)
  manim/offline_demo/                    hand-written sample for the labelled offline demo
  tests/                                 216 tests (LLM mocked; Docker tests skip without Docker)
frontend/                                Next.js app (app/, components/, lib/)
docker/Dockerfile.renderer               Manim CE v0.19.2 + Inter + ffmpeg
docs/INTERFACES.md                       contracts between the pipeline stages
scripts/                                 dev.sh, check.sh (used by the Makefile)
```
