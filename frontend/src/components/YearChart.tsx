"use client";

import { useEffect, useRef, useState } from "react";
import { colorFor, FIXED_CHEMICAL_COLORS } from "@/lib/chemColors";

/**
 * Stacked columns, one per year, one segment per chemical.
 *
 * Colour follows the chemical, never its rank: the five most-used forestry
 * herbicides always wear the same hue on every page and every filter, and
 * everything else is grey "Other". The legend is always shown, and hovering
 * a year lists every chemical in it, so identity never rests on colour.
 */

export interface YearSeries {
  label: string;
  values: Record<string, number>;
}

const FIXED = FIXED_CHEMICAL_COLORS;

function niceMax(value: number): number {
  if (value <= 0) return 1;
  const exp = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 2, 2.5, 5, 10]) if (step * exp >= value) return step * exp;
  return 10 * exp;
}

function fmt(value: number): string {
  return value.toLocaleString("en-US", { maximumFractionDigits: value >= 100 ? 0 : 1 });
}

export function YearChart({
  years,
  series,
  unit,
  title,
}: {
  years: number[];
  series: YearSeries[];
  unit: string;
  title: string;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    if (!wrap.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(280, entry.contentRect.width)));
    observer.observe(wrap.current);
    return () => observer.disconnect();
  }, []);

  // Fold everything without a fixed hue into one "Other" segment, so the
  // stack never cycles colours.
  const named = FIXED.map(([name]) => name).filter((name) => series.some((s) => s.label === name));
  const other = series.filter((s) => !named.includes(s.label));
  const stacks: YearSeries[] = [
    ...named.map((name) => series.find((s) => s.label === name)!),
    ...(other.length
      ? [{
          label: "Other",
          values: Object.fromEntries(years.map((y) => [
            String(y), other.reduce((sum, s) => sum + (s.values[String(y)] ?? 0), 0),
          ])),
        }]
      : []),
  ].filter((s) => years.some((y) => (s.values[String(y)] ?? 0) > 0));

  const totals = years.map((y) => stacks.reduce((sum, s) => sum + (s.values[String(y)] ?? 0), 0));
  if (totals.every((t) => t === 0)) return null;

  const height = 240;
  const pad = { top: 16, right: 12, bottom: 28, left: 56 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const max = niceMax(Math.max(...totals));
  const band = plotW / years.length;
  const barW = Math.min(24, band * 0.6);
  const y = (v: number) => pad.top + plotH - (v / max) * plotH;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max);
  const GAP = 2;

  return (
    <figure className="year-chart">
      <figcaption className="year-chart-title">{title}</figcaption>
      {stacks.length > 1 && (
        <ul className="year-chart-legend">
          {stacks.map((s) => (
            <li key={s.label}><span className="swatch" style={{ background: colorFor(s.label) }} />{s.label}</li>
          ))}
        </ul>
      )}
      <div ref={wrap} className="year-chart-plot" onMouseLeave={() => setHover(null)}>
        <svg width={width} height={height} role="img" aria-label={`${title}, by year`}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} className="grid" />
              <text x={pad.left - 8} y={y(t)} className="tick" textAnchor="end" dominantBaseline="middle">
                {fmt(t)}
              </text>
            </g>
          ))}
          {years.map((year, i) => {
            const cx = pad.left + band * i + band / 2;
            let base = 0;
            const segments = stacks
              .map((s) => ({ s, v: s.values[String(year)] ?? 0 }))
              .filter(({ v }) => v > 0);
            return (
              <g key={year}>
                {segments.map(({ s, v }, k) => {
                  const top = y(base + v);
                  const bottom = y(base);
                  base += v;
                  const isTop = k === segments.length - 1;
                  const h = Math.max(1, bottom - top - (k > 0 ? GAP : 0));
                  const x = cx - barW / 2;
                  const yTop = bottom - (k > 0 ? GAP : 0) - h;
                  const r = isTop ? Math.min(4, h, barW / 2) : 0;
                  const d = `M${x},${yTop + h} V${yTop + r} Q${x},${yTop} ${x + r},${yTop} ` +
                    `H${x + barW - r} Q${x + barW},${yTop} ${x + barW},${yTop + r} V${yTop + h} Z`;
                  return <path key={s.label} d={d} fill={colorFor(s.label)} opacity={hover === null || hover === i ? 1 : 0.45} />;
                })}
                <text x={cx} y={height - 8} className="tick" textAnchor="middle">{year}</text>
                {/* Hit target: the whole year band, taller than the bar. */}
                <rect x={pad.left + band * i} y={pad.top} width={band} height={plotH} fill="transparent"
                      onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} tabIndex={0}
                      aria-label={`${year}: ${fmt(totals[i])} ${unit}`} />
              </g>
            );
          })}
        </svg>
        {hover !== null && (
          <div
            className="year-chart-tip"
            style={{
              left: Math.min(width - 210, Math.max(0, pad.left + band * hover + band / 2 + 16)),
              top: pad.top,
            }}
          >
            <div className="tip-head">{years[hover]} · {fmt(totals[hover])} {unit}</div>
            {series
              .map((s) => ({ s, v: s.values[String(years[hover])] ?? 0 }))
              .filter(({ v }) => v > 0)
              .sort((a, b) => b.v - a.v)
              .map(({ s, v }) => (
                <div key={s.label} className="tip-row">
                  <span className="swatch" style={{ background: colorFor(s.label) }} />
                  <span className="tip-label">{s.label}</span>
                  <span className="tip-value">{fmt(v)}</span>
                </div>
              ))}
          </div>
        )}
      </div>
    </figure>
  );
}
