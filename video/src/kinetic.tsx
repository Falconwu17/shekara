// Кинетическая типографика: слова выезжают из-под маски по очереди, числа считаются на глазах.
import type { CSSProperties } from "react";
import { Easing, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";

/** Текст, который появляется по словам: каждое слово выезжает снизу из-под маски. */
export function Words({ text, at, stagger = 3, style }: { text: string; at: number; stagger?: number; style?: CSSProperties }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  return (
    <div style={{ display: "flex", flexWrap: "wrap", columnGap: "0.28em", ...style }}>
      {text.split(" ").map((word, i) => {
        const p = spring({ frame: frame - at - i * stagger, fps, config: { damping: 18, mass: 0.6 } });
        return (
          <span key={i} style={{ display: "inline-block", overflow: "hidden", paddingBottom: "0.08em" }}>
            <span style={{ display: "inline-block", transform: `translateY(${(1 - p) * 110}%) rotate(${(1 - p) * 6}deg)`, opacity: Math.min(1, p * 1.5) }}>
              {word}
            </span>
          </span>
        );
      })}
    </div>
  );
}

/** Число, которое досчитывается от `from` до `to` за `frames` кадров. */
export function CountUp({ to, from = 0, at, frames = 36, digits = 0, prefix = "", suffix = "", style }:
  { to: number; from?: number; at: number; frames?: number; digits?: number; prefix?: string; suffix?: string; style?: CSSProperties }) {
  const frame = useCurrentFrame();
  const value = interpolate(frame, [at, at + frames], [from, to], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.cubic) });
  const text = value.toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return <span style={{ fontVariantNumeric: "tabular-nums", ...style }}>{prefix}{text}{suffix}</span>;
}

/** Штамп: появляется с ударом (масштаб и поворот), как печать. */
export function useStamp(at: number) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = spring({ frame: frame - at, fps, config: { damping: 9, stiffness: 160 } });
  return { opacity: Math.min(1, p * 2), transform: `scale(${2 - p}) rotate(${(1 - p) * -12}deg)` };
}
