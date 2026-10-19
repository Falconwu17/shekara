// Общие элементы сцен: фон, заголовки, банк, деньги, плавные появления.
import type { CSSProperties, ReactNode } from "react";
import { AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { Words } from "./kinetic";
import { COLOR, FONT } from "./theme";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

/** 0 → 1 за `frames` кадров, начиная с `start`; плавно. */
export function useProgress(start: number, frames = 20) {
  const frame = useCurrentFrame();
  return interpolate(frame, [start, start + frames], [0, 1], { ...clamp, easing: Easing.bezier(0.25, 0.1, 0.25, 1) });
}

/** Пружинное появление (0 → 1) с кадра `start`. */
export function useSpring(start: number) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  return spring({ frame: frame - start, fps, config: { damping: 200 } });
}

/** Секунды в кадры. */
export function useSec() {
  const { fps } = useVideoConfig();
  return (seconds: number) => Math.round(seconds * fps);
}

/** Сцена: фон, цвет текста и переход. Вход: лёгкий наезд из размытия; выход: отъезд в размытие. */
export function Stage({ dark = false, children }: { dark?: boolean; children: ReactNode }) {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const enter = interpolate(frame, [0, 14], [0, 1], { ...clamp, easing: Easing.out(Easing.cubic) });
  const exit = interpolate(frame, [durationInFrames - 12, durationInFrames], [0, 1], { ...clamp, easing: Easing.in(Easing.cubic) });
  const scale = 1 + 0.06 * (1 - enter) - 0.05 * exit;
  const blur = 10 * (1 - enter) + 10 * exit;
  return (
    <AbsoluteFill style={{ background: dark ? COLOR.navy : COLOR.cream, color: dark ? COLOR.cream : COLOR.ink, fontFamily: FONT.sans }}>
      <AbsoluteFill style={{ opacity: Math.min(enter, 1 - exit), transform: `scale(${scale})`, filter: blur > 0.3 ? `blur(${blur}px)` : undefined }}>
        {children}
      </AbsoluteFill>
    </AbsoluteFill>
  );
}

export function Appear({ at, children, from = 24, style }: { at: number; children: ReactNode; from?: number; style?: CSSProperties }) {
  const p = useSpring(at);
  return <div style={{ opacity: p, transform: `translateY(${(1 - p) * from}px)`, ...style }}>{children}</div>;
}

export function Kicker({ children, dark = false, at = 0 }: { children: string; dark?: boolean; at?: number }) {
  return (
    <Words text={children} at={at} stagger={2}
      style={{ fontSize: 28, fontWeight: 600, letterSpacing: 4, textTransform: "uppercase", color: dark ? COLOR.amber : COLOR.accent }} />
  );
}

export function Headline({ children, at = 6, size = 68, width = 1500 }: { children: string; at?: number; size?: number; width?: number }) {
  return <Words text={children} at={at} style={{ fontFamily: FONT.serif, fontSize: size, fontWeight: 600, lineHeight: 1.15, width }} />;
}

/** Банк: карточка с пунктирной границей и названием. `lit` от 0 до 1: насколько банк «видит». */
export function Bank({ x, y, name, lit = 1, width = 300, height = 220, children }:
  { x: number; y: number; name: string; lit?: number; width?: number; height?: number; children?: ReactNode }) {
  return (
    <div style={{
      position: "absolute", left: x - width / 2, top: y - height / 2, width, height, borderRadius: 24,
      border: `3px dashed ${COLOR.navyLine}`, background: COLOR.paper, opacity: 0.25 + 0.75 * lit,
      filter: `blur(${(1 - lit) * 3}px)`, display: "flex", flexDirection: "column", alignItems: "center", paddingTop: 22, gap: 10,
    }}>
      <div style={{ fontFamily: FONT.mono, fontSize: 30, fontWeight: 600, color: COLOR.navy }}>{name}</div>
      {children}
    </div>
  );
}

/** Монета с суммой, летит из (x1, y1) в (x2, y2) за кадры [start, start + frames]; после кадра `until` исчезает. */
export function Coin({ x1, y1, x2, y2, start, frames = 24, until = Infinity, label = "₸", color = COLOR.accent }:
  { x1: number; y1: number; x2: number; y2: number; start: number; frames?: number; until?: number; label?: string; color?: string }) {
  const p = useProgress(start, frames);
  const frame = useCurrentFrame();
  if (frame < start || frame > until) return null;
  const arc = Math.sin(p * Math.PI) * -60;
  return (
    <div style={{
      position: "absolute", left: x1 + (x2 - x1) * p - 34, top: y1 + (y2 - y1) * p + arc - 34, width: 68, height: 68,
      borderRadius: 34, background: color, color: COLOR.paper, display: "flex", alignItems: "center", justifyContent: "center",
      fontFamily: FONT.mono, fontSize: 26, fontWeight: 600, boxShadow: "0 8px 24px rgba(20,33,61,.25)",
    }}>{label}</div>
  );
}

/** Конверт с замком и оценкой риска: то, что банки передают друг другу. */
export function Envelope({ x, y, score, open = 0 }: { x: number; y: number; score: string; open?: number }) {
  return (
    <div style={{ position: "absolute", left: x - 120, top: y - 80, width: 240, height: 160 }}>
      <div style={{
        position: "absolute", inset: 0, borderRadius: 18, background: COLOR.navy, color: COLOR.cream,
        display: "flex", alignItems: "center", justifyContent: "center", gap: 14, boxShadow: "0 12px 30px rgba(20,33,61,.3)",
      }}>
        <Lock size={46} color={COLOR.amber} />
        <span style={{ fontFamily: FONT.mono, fontSize: 46, fontWeight: 600, color: COLOR.amber, opacity: open }}>{score}</span>
      </div>
    </div>
  );
}

/** Замок: шифрование ответа банку-адресату. */
export function Lock({ size = 40, color = COLOR.navy }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={2.2} strokeLinecap="round">
      <rect x="4" y="10" width="16" height="11" rx="2.5" fill={color} fillOpacity={0.15} />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </svg>
  );
}

export function Note({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return <div style={{ fontSize: 34, lineHeight: 1.35, color: COLOR.muted, ...style }}>{children}</div>;
}

export const PAD = 120;

/** Заголовок сцены в экранных координатах; исчезает к моменту `hideAt`, когда камера уходит в другую часть мира. */
export function Title({ kicker, headline, at = 0, hideAt = Infinity, size = 62, dark = false }:
  { kicker: string; headline: string; at?: number; hideAt?: number; size?: number; dark?: boolean }) {
  const frame = useCurrentFrame();
  const out = Number.isFinite(hideAt) ? interpolate(frame, [hideAt, hideAt + 12], [1, 0], clamp) : 1;
  return (
    <div style={{ position: "absolute", left: PAD, top: 70, display: "flex", flexDirection: "column", gap: 16, opacity: out,
      transform: `translateY(${(1 - out) * -30}px)` }}>
      <Kicker at={at} dark={dark}>{kicker}</Kicker>
      <Headline at={at + 8} size={size}>{headline}</Headline>
    </div>
  );
}
