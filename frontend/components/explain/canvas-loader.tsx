"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import type { CSSProperties } from "react";

/*
 * An indeterminate, Manim-flavoured animation that fills the 16:9 frame while the
 * video is being made. Each pipeline stage gets its own motif, but nothing here
 * implies how far along a stage is — real progress comes only from the API.
 */

const W = 1600;
const H = 900;
const PLOT_Y = 380; // vertical centre of the drawing area (the bottom band is reserved for the caption)

function curvePoint(x: number): [number, number] {
  const y = 1.55 * Math.sin(1.05 * x) * Math.exp(-0.018 * x * x) + 0.12 * x;
  return [W / 2 + x * 100, PLOT_Y - y * 100];
}

function buildCurve(): string {
  const points: string[] = [];
  const steps = 180;
  for (let i = 0; i <= steps; i++) {
    const [px, py] = curvePoint(-6.4 + (12.8 * i) / steps);
    points.push(`${px.toFixed(1)} ${py.toFixed(1)}`);
  }
  return `M${points.join(" L")}`;
}

const CURVE = buildCurve();
const [CURVE_END_X, CURVE_END_Y] = curvePoint(6.4);

function Grid({ axes }: { axes: boolean }) {
  const vertical = Array.from({ length: 17 }, (_, i) => i * 100);
  const horizontal = Array.from({ length: 10 }, (_, i) => PLOT_Y - 400 + i * 100);
  return (
    <g aria-hidden>
      {vertical.map((x) => (
        <line key={`v${x}`} x1={x} y1={0} x2={x} y2={H} stroke="#58c4dd" strokeOpacity={0.07} strokeWidth={2} />
      ))}
      {horizontal.map((y) => (
        <line key={`h${y}`} x1={0} y1={y} x2={W} y2={y} stroke="#58c4dd" strokeOpacity={0.07} strokeWidth={2} />
      ))}
      {axes && (
        <>
          <line x1={0} y1={PLOT_Y} x2={W} y2={PLOT_Y} stroke="#a6aec4" strokeOpacity={0.35} strokeWidth={3} />
          <line x1={W / 2} y1={0} x2={W / 2} y2={H} stroke="#a6aec4" strokeOpacity={0.35} strokeWidth={3} />
        </>
      )}
    </g>
  );
}

/** Stage 0 — understanding: a small network with signals travelling along its edges. */
function NetworkMotif() {
  const layers = [
    { x: 470, ys: [230, 370, 510] },
    { x: 800, ys: [160, 300, 440, 580] },
    { x: 1130, ys: [230, 370, 510] },
  ];
  const colors = ["#58c4dd", "#b394f6", "#5cd0b3"];
  const edges: Array<[number, number, number, number]> = [];
  for (let l = 0; l < layers.length - 1; l++) {
    for (const y1 of layers[l].ys) {
      for (const y2 of layers[l + 1].ys) edges.push([layers[l].x, y1, layers[l + 1].x, y2]);
    }
  }
  return (
    <g>
      {edges.map(([x1, y1, x2, y2], i) => (
        <line key={`e${i}`} x1={x1} y1={y1} x2={x2} y2={y2} stroke="#a6aec4" strokeOpacity={0.16} strokeWidth={2.5} />
      ))}
      {edges.map(([x1, y1, x2, y2], i) => (
        <line
          key={`p${i}`}
          x1={x1}
          y1={y1}
          x2={x2}
          y2={y2}
          pathLength={1}
          stroke={i % 4 === 0 ? "#f4d345" : "#7fe3f0"}
          strokeWidth={4}
          strokeLinecap="round"
          className="edge-pulse"
          style={{ animationDelay: `${((i * 0.37) % 2.6).toFixed(2)}s` } as CSSProperties}
        />
      ))}
      {layers.map((layer, l) =>
        layer.ys.map((y, i) => (
          <g key={`n${l}-${i}`}>
            <circle cx={layer.x} cy={y} r={30} fill={colors[l]} fillOpacity={0.12} />
            <circle
              cx={layer.x}
              cy={y}
              r={18}
              fill="#0c1020"
              stroke={colors[l]}
              strokeWidth={5}
              className="node-breathe"
              style={{ animationDelay: `${(l * 0.4 + i * 0.25).toFixed(2)}s` } as CSSProperties}
            />
          </g>
        )),
      )}
    </g>
  );
}

/** Stage 1 — designing: storyboard frames appearing one after another. */
function StoryboardMotif() {
  const tiles = [0, 1, 2, 3];
  const tileW = 290;
  const tileH = 190;
  const gap = 36;
  const startX = (W - (tileW * 4 + gap * 3)) / 2;
  const y = PLOT_Y - tileH / 2 - 20;
  const accents = ["#58c4dd", "#5cd0b3", "#f4d345", "#b394f6"];
  return (
    <g>
      {tiles.map((i) => {
        const x = startX + i * (tileW + gap);
        const c = accents[i];
        return (
          <g key={i} className="tile-in" style={{ animationDelay: `${i * 0.35}s` } as CSSProperties}>
            <rect x={x} y={y} width={tileW} height={tileH} rx={22} fill="#0c1020" stroke={c} strokeOpacity={0.55} strokeWidth={3} />
            {i === 0 && <circle cx={x + tileW / 2} cy={y + 84} r={44} fill="none" stroke={c} strokeWidth={6} />}
            {i === 1 && (
              <path
                d={`M${x + 60} ${y + 120} L${x + tileW - 60} ${y + 50} M${x + tileW - 92} ${y + 46} L${x + tileW - 60} ${y + 50} L${x + tileW - 74} ${y + 80}`}
                fill="none"
                stroke={c}
                strokeWidth={6}
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            )}
            {i === 2 && (
              <path
                d={`M${x + 50} ${y + 130} C ${x + 110} ${y + 130}, ${x + 120} ${y + 40}, ${x + tileW / 2} ${y + 40} S ${x + tileW - 110} ${y + 130}, ${x + tileW - 50} ${y + 130}`}
                fill="none"
                stroke={c}
                strokeWidth={6}
                strokeLinecap="round"
              />
            )}
            {i === 3 &&
              [0, 1, 2].map((b) => (
                <rect key={b} x={x + 70 + b * 56} y={y + 130 - (b + 1) * 30} width={36} height={(b + 1) * 30} rx={6} fill={c} fillOpacity={0.85} />
              ))}
            <rect x={x + 40} y={y + tileH + 26} width={tileW - 80} height={12} rx={6} fill="#a6aec4" fillOpacity={0.22} />
            <rect x={x + 70} y={y + tileH + 50} width={tileW - 140} height={12} rx={6} fill="#a6aec4" fillOpacity={0.12} />
          </g>
        );
      })}
    </g>
  );
}

/** Stage 2 — creating animations: a curve being drawn with a point tracing it. */
function CurveMotif({ reduced }: { reduced: boolean }) {
  const dur = "5.2s";
  return (
    <g>
      <path d={CURVE} fill="none" stroke="#58c4dd" strokeOpacity={0.12} strokeWidth={22} strokeLinecap="round" />
      <path
        d={CURVE}
        pathLength={1}
        fill="none"
        stroke="url(#loader-curve)"
        strokeWidth={7}
        strokeLinecap="round"
        strokeDasharray={1}
        strokeDashoffset={reduced ? 0 : 1}
      >
        {!reduced && (
          <>
            <animate
              attributeName="stroke-dashoffset"
              values="1;0;0;0"
              keyTimes="0;0.55;0.85;1"
              calcMode="spline"
              keySplines="0.65 0 0.35 1;0 0 1 1;0 0 1 1"
              dur={dur}
              repeatCount="indefinite"
            />
            <animate attributeName="opacity" values="1;1;1;0" keyTimes="0;0.55;0.85;1" dur={dur} repeatCount="indefinite" />
          </>
        )}
      </path>
      <g>
        <circle r={26} fill="#f4d345" fillOpacity={0.2} cx={reduced ? CURVE_END_X : 0} cy={reduced ? CURVE_END_Y : 0} />
        <circle r={12} fill="#f4d345" cx={reduced ? CURVE_END_X : 0} cy={reduced ? CURVE_END_Y : 0} />
        {!reduced && (
          <>
            <animateMotion
              path={CURVE}
              keyPoints="0;1;1;1"
              keyTimes="0;0.55;0.85;1"
              calcMode="spline"
              keySplines="0.65 0 0.35 1;0 0 1 1;0 0 1 1"
              dur={dur}
              repeatCount="indefinite"
            />
            <animate attributeName="opacity" values="1;1;1;0" keyTimes="0;0.55;0.85;1" dur={dur} repeatCount="indefinite" />
          </>
        )}
      </g>
    </g>
  );
}

/** Stage 3 — rendering: the finished drawing with a light sweeping across it. */
function RenderMotif() {
  return (
    <g>
      <path d={CURVE} fill="none" stroke="#58c4dd" strokeOpacity={0.14} strokeWidth={22} strokeLinecap="round" />
      <path d={CURVE} fill="none" stroke="url(#loader-curve)" strokeWidth={7} strokeLinecap="round" />
      <g className="scan-sweep">
        <rect x={60} y={0} width={140} height={H} fill="url(#loader-sweep)" />
        <line x1={130} y1={0} x2={130} y2={H} stroke="#7fe3f0" strokeOpacity={0.55} strokeWidth={3} />
      </g>
    </g>
  );
}

function motifFor(stageIndex: number): number {
  return Math.max(0, Math.min(3, stageIndex));
}

export function CanvasLoader({ stageIndex }: { stageIndex: number }) {
  const reduced = useReducedMotion() ?? false;
  const motif = motifFor(stageIndex);

  return (
    <div aria-hidden className="absolute inset-0">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_50%_40%,#10162b_0%,#070a14_70%)]" />
      <AnimatePresence initial={false}>
        <motion.svg
          key={motif}
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="xMidYMid slice"
          className="absolute inset-0 size-full"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.8, ease: "easeInOut" }}
        >
          <defs>
            <linearGradient id="loader-curve" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0" stopColor="#7fe3f0" />
              <stop offset="0.5" stopColor="#58c4dd" />
              <stop offset="1" stopColor="#b394f6" />
            </linearGradient>
            <linearGradient id="loader-sweep" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0" stopColor="#7fe3f0" stopOpacity="0" />
              <stop offset="0.5" stopColor="#7fe3f0" stopOpacity="0.12" />
              <stop offset="1" stopColor="#7fe3f0" stopOpacity="0" />
            </linearGradient>
          </defs>
          <Grid axes={motif >= 2} />
          {motif === 0 && <NetworkMotif />}
          {motif === 1 && <StoryboardMotif />}
          {motif === 2 && <CurveMotif reduced={reduced} />}
          {motif === 3 && <RenderMotif />}
        </motion.svg>
      </AnimatePresence>
    </div>
  );
}
