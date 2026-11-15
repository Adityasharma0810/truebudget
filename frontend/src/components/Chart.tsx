import { memo, useId, useMemo, useState } from "react";
import { scaleLinear } from "d3-scale";
import { motion } from "motion/react";
import type { MetricPoint, Snapshot } from "../types";
import { dateTime } from "../lib/format";
import { useEffectiveTier } from "../motion/store";
import { motionTokens as t } from "../motion/tokens";
export const CapacityChart = memo(function CapacityChart({
  points,
  forecast,
  compact = false,
  metric = "throughput",
  baseline = false,
}: {
  points: MetricPoint[];
  forecast?: Snapshot["link"]["forecast"];
  compact?: boolean;
  metric?: "throughput" | "latency" | "jitter" | "loss" | "queue" | "fairness";
  baseline?: boolean;
}) {
  const id = useId().replaceAll(":", "");
  const tier = useEffectiveTier();
  const [hover, setHover] = useState<number | null>(null);
  const width = 800,
    height = compact ? 170 : 210,
    left = 34,
    right = 12,
    top = 14,
    bottom = 30;
  const isThroughput = metric === "throughput";
  const max = isThroughput
    ? 120
    : metric === "fairness"
      ? 1
      : Math.max(
          metric === "latency" ? 40 : 10,
          ...points.map((p) => p[metric]),
          ...(baseline ? points.map((p) => p.baseline) : []),
        ) * 1.15;
  const x = scaleLinear()
    .domain([0, Math.max(1, points.length - 1)])
    .range([left, width - right - (forecast && isThroughput ? 105 : 0)]);
  const y = scaleLinear()
    .domain([0, max])
    .range([height - bottom, top]);
  const line = (key: keyof MetricPoint, subset = points) =>
    subset.map((p, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(Number(p[key]))}`).join(" ");
  const path = useMemo(() => line(metric), [points, metric, height]);
  const endX = x(points.length - 1);
  const endY = y(points[points.length - 1]?.[metric] ?? 0);
  const capacityPath = line("capacity");
  const bottomY = height - bottom;
  const band =
    points.map((p, i) => `${x(i)},${y(p.hi)}`).join(" ") +
    " " +
    points
      .slice()
      .reverse()
      .map((p, i) => `${x(points.length - 1 - i)},${y(p.lo)}`)
      .join(" ");
  const item = hover === null ? null : points[hover];
  return (
    <div className={`chart ${compact ? "compact-chart" : ""}`}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`${metric} over time${baseline ? ", unmanaged baseline versus Experience Budget" : ""}`}
        onPointerLeave={() => setHover(null)}
        onPointerMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const index = Math.round(x.invert(((e.clientX - rect.left) / rect.width) * width));
          setHover(Math.max(0, Math.min(points.length - 1, index)));
        }}
      >
        <defs>
          <linearGradient id={`fill${id}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#6993ff" stopOpacity=".24" />
            <stop offset="100%" stopColor="#6993ff" stopOpacity=".015" />
          </linearGradient>
        </defs>
        {[0, 0.25, 0.5, 0.75, 1].map((v) => (
          <g key={v}>
            <line
              x1={left}
              x2={width - right}
              y1={y(v * max)}
              y2={y(v * max)}
              stroke="#eaf0f6"
              strokeDasharray="3 5"
            />
            <text x={left - 10} y={y(v * max) + 4} textAnchor="end" className="chart-label">
              {metric === "fairness" ? (v * max).toFixed(1) : Math.round(v * max)}
            </text>
          </g>
        ))}
        {isThroughput && (
          <>
            <polygon points={band} fill="#a8b7cf" opacity=".10" />
            <path
              d={capacityPath}
              fill="none"
              stroke="#a6b3c8"
              strokeWidth="1.5"
              strokeDasharray="5 5"
            />
          </>
        )}
        {baseline && (
          <path
            d={line("baseline")}
            stroke="#b69ae8"
            strokeWidth="2"
            fill="none"
            strokeDasharray="5 4"
          />
        )}
        <path d={`${path}L${endX},${bottomY}L${left},${bottomY}Z`} fill={`url(#fill${id})`} />
        <motion.path
          key={metric}
          d={path}
          fill="none"
          stroke="#5686f7"
          strokeWidth="2.6"
          strokeLinejoin="round"
          initial={tier === "Full" || tier === "Balanced" ? { pathLength: 0 } : false}
          animate={{ pathLength: 1 }}
          transition={{ duration: t.duration.epic }}
        />
        <circle cx={endX} cy={endY} r="3.8" fill="#4c7dff" stroke="white" strokeWidth="2" />
        {forecast && isThroughput && (
          <>
            <rect
              x={endX + 5}
              y={top}
              width={100}
              height={bottomY - top}
              fill="#4c7dff"
              opacity=".025"
              rx="5"
            />
            <text
              x={endX + 48}
              y={top + 12}
              textAnchor="middle"
              className="chart-label forecast-label"
            >
              FORECAST
            </text>
            <polygon
              points={`${endX},${y(forecast[0]?.hi ?? 100)} ${width - right},${y(forecast[forecast.length - 1]?.hi ?? 100)} ${width - right},${y(forecast[forecast.length - 1]?.lo ?? 90)} ${endX},${y(forecast[0]?.lo ?? 90)}`}
              fill="#7697ee"
              opacity=".10"
            />
            <path
              d={`M${endX},${y(points[points.length - 1]?.capacity ?? 100)} ${forecast.map((p, i) => `L${endX + ((i + 1) / forecast.length) * 105},${y(p.mbps)}`).join(" ")}`}
              fill="none"
              stroke="#7b97d9"
              strokeDasharray="4 4"
              strokeWidth="1.6"
            />
          </>
        )}
        {points
          .filter((_, i) => i % 14 === 0 || i === points.length - 1)
          .map((p, i, arr) => (
            <text
              key={p.ts}
              x={left + (i / (arr.length - 1)) * (endX - left)}
              y={height - 7}
              textAnchor={i === 0 ? "start" : "middle"}
              className="chart-label"
            >
              {dateTime(p.ts)}
            </text>
          ))}
        {item && hover !== null && (
          <g>
            <line
              x1={x(hover)}
              x2={x(hover)}
              y1={top}
              y2={bottomY}
              stroke="#8eabeb"
              strokeDasharray="3 3"
            />
            <circle
              cx={x(hover)}
              cy={y(item[metric])}
              r="4"
              fill="#4c7dff"
              stroke="#fff"
              strokeWidth="2"
            />
            <rect
              x={Math.min(width - 150, x(hover) + 9)}
              y={Math.max(10, y(item[metric]) - 40)}
              width="128"
              height="31"
              rx="7"
              fill="#27354e"
            />
            <text
              x={Math.min(width - 140, x(hover) + 19)}
              y={Math.max(10, y(item[metric]) - 40) + 20}
              fill="white"
              fontSize="11"
            >
              {item[metric].toFixed(1)} · {dateTime(item.ts)}
            </text>
          </g>
        )}
      </svg>
    </div>
  );
});
export function Sparkline({ points, color = "#5885ed" }: { points: number[]; color?: string }) {
  const min = Math.min(...points),
    max = Math.max(...points);
  const line = points
    .map(
      (p, i) =>
        `${i === 0 ? "M" : "L"}${(i / Math.max(1, points.length - 1)) * 100},${27 - ((p - min) / Math.max(1, max - min)) * 22}`,
    )
    .join(" ");
  return (
    <svg className="sparkline" viewBox="0 0 100 32" aria-hidden="true">
      <path d={line} stroke={color} strokeWidth="1.8" fill="none" strokeLinecap="round" />
    </svg>
  );
}
