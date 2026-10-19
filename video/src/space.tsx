// 2,5D-пространство сцены: камера с ключевыми кадрами, слои на разной глубине, фон с параллаксом.
// Координаты мира: (0, 0) в центре кадра при камере в нуле; z > 0 ближе к зрителю.
import type { CSSProperties, ReactNode } from "react";
import { AbsoluteFill, Easing, interpolate, random, useCurrentFrame, useVideoConfig } from "remotion";
import { COLOR } from "./theme";

export type Cam = { x?: number; y?: number; z?: number; rx?: number; ry?: number; rz?: number };
type Key = [seconds: number, cam: Cam];

const ease = Easing.bezier(0.45, 0, 0.25, 1);

/** Камера по ключевым кадрам (секунды): между ними плавный разгон и торможение. */
export function useCamera(keys: Key[]): Required<Cam> {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const times = keys.map(([t]) => t * fps);
  const value = (prop: keyof Cam) => {
    const values = keys.map(([, cam]) => cam[prop] ?? 0);
    if (keys.length === 1) return values[0];
    return interpolate(frame, times, values, { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: ease });
  };
  return { x: value("x"), y: value("y"), z: value("z"), rx: value("rx"), ry: value("ry"), rz: value("rz") };
}

/** Мир сцены: всё внутри смотрит через одну камеру с перспективой. */
export function Space({ cam, children }: { cam: Required<Cam>; children: ReactNode }) {
  return (
    <AbsoluteFill style={{ perspective: 1600, perspectiveOrigin: "50% 50%", overflow: "hidden" }}>
      <div style={{
        position: "absolute", left: 960, top: 540, transformStyle: "preserve-3d",
        transform: `translateZ(${cam.z}px) rotateX(${cam.rx}deg) rotateY(${cam.ry}deg) rotateZ(${cam.rz}deg) translate(${-cam.x}px, ${-cam.y}px)`,
      }}>
        {children}
      </div>
    </AbsoluteFill>
  );
}

/** Слой на глубине z: чем дальше слой, тем медленнее он движется при проезде камеры. */
export function Layer({ z = 0, children, style }: { z?: number; children: ReactNode; style?: CSSProperties }) {
  return <div style={{ position: "absolute", left: 0, top: 0, transformStyle: "preserve-3d", transform: `translateZ(${z}px)`, ...style }}>{children}</div>;
}

/** Объект в мире: центр в точке (x, y). */
export function At({ x, y, children, style }: { x: number; y: number; children: ReactNode; style?: CSSProperties }) {
  return <div style={{ position: "absolute", left: x, top: y, width: "max-content", transform: "translate(-50%, -50%)", ...style }}>{children}</div>;
}

/** Дальний фон: сетка точек глубоко за сценой и редкие пылинки перед ней. */
export function Backdrop({ dark = false, seed = "s" }: { dark?: boolean; seed?: string }) {
  const frame = useCurrentFrame();
  const dot = dark ? "rgba(191,200,217,.16)" : "rgba(20,33,61,.10)";
  return (
    <>
      <Layer z={-1400}>
        <div style={{ position: "absolute", left: -4000, top: -2500, width: 8000, height: 5000,
          backgroundImage: `radial-gradient(${dot} 2.4px, transparent 2.6px)`, backgroundSize: "70px 70px" }} />
      </Layer>
      <Layer z={-600}>
        {Array.from({ length: 14 }, (_, i) => {
          const x = (random(`${seed}x${i}`) - 0.5) * 3600;
          const y = (random(`${seed}y${i}`) - 0.5) * 1800;
          const r = 30 + random(`${seed}r${i}`) * 90;
          const drift = Math.sin(frame / 50 + i) * 20;
          return <div key={i} style={{ position: "absolute", left: x, top: y + drift, width: r, height: r, borderRadius: "50%",
            background: dark ? "rgba(240,163,94,.10)" : "rgba(168,74,20,.07)", filter: "blur(6px)" }} />;
        })}
      </Layer>
    </>
  );
}

/** Ровный фон сцены (светлый или тёмный) с мягким световым пятном. */
export function Ground({ dark = false }: { dark?: boolean }) {
  return (
    <AbsoluteFill style={{ background: dark
      ? `radial-gradient(circle at 35% 30%, #1E2E52, ${COLOR.navy} 70%)`
      : `radial-gradient(circle at 40% 30%, #FFFFFF, ${COLOR.cream} 70%)` }} />
  );
}
