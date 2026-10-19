// 03. Почему не решено. Камера облетает две «очевидные» идеи: общая база упирается в закон (замок),
// центральный оператор видит только линии между банками, а внутри банков у него туман.
import type { ReactNode } from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { useStamp } from "../kinetic";
import { At, Backdrop, Ground, Layer, Space, useCamera } from "../space";
import { COLOR, FONT } from "../theme";
import { Headline, Kicker, Lock, PAD, Stage, useSec } from "../ui";

function Card({ title, tone, children }: { title: string; tone: string; children: ReactNode }) {
  return (
    <div style={{ width: 780, height: 560, padding: 40, borderRadius: 28, background: COLOR.paper, border: `3px solid ${tone}`,
      boxShadow: "0 40px 80px rgba(20,33,61,.15)", position: "relative" }}>
      <div style={{ fontSize: 42, fontWeight: 600, color: tone }}>{title}</div>
      {children}
    </div>
  );
}

function MiniBank({ x, y, label, fog = 0 }: { x: number; y: number; label: string; fog?: number }) {
  return (
    <div style={{ position: "absolute", left: x - 70, top: y - 45, width: 140, height: 90, borderRadius: 14, border: `3px dashed ${COLOR.navyLine}`,
      background: fog ? `color-mix(in srgb, #C3CAD6 ${Math.round(100 * fog)}%, ${COLOR.paper})` : COLOR.paper, display: "flex", alignItems: "center", justifyContent: "center",
      fontFamily: FONT.mono, fontSize: 24, fontWeight: 600, color: COLOR.navy }}>{fog > 0.5 ? "?" : label}</div>
  );
}

function PooledDatabase() {
  const s = useSec();
  const frame = useCurrentFrame();
  const pull = interpolate(frame, [s(1), s(2.6)], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const stamp = useStamp(s(3));
  const banks = [{ x: 120, y: 160 }, { x: 390, y: 140 }, { x: 640, y: 180 }];
  return (
    <Card title="Общая база банков" tone={COLOR.risk}>
      {banks.map((b, i) => (
        <div key={i}>
          <MiniBank x={b.x} y={b.y} label={["A", "B", "C"][i]} />
          <div style={{ position: "absolute", left: b.x, top: b.y + 45, width: 4, height: 160 * pull, background: COLOR.navyLine,
            transformOrigin: "top", transform: `rotate(${(390 - b.x) * -0.12}deg)` }} />
        </div>
      ))}
      <div style={{ position: "absolute", left: 250, top: 330, width: 280, height: 110, borderRadius: 18, background: COLOR.navy, color: COLOR.cream,
        display: "flex", alignItems: "center", justifyContent: "center", textAlign: "center", fontSize: 28, opacity: pull }}>все данные клиентов</div>
      <div style={{ position: "absolute", left: 300, top: 250, ...stamp, display: "flex", gap: 14, alignItems: "center", padding: "14px 22px",
        borderRadius: 14, border: `4px solid ${COLOR.risk}`, background: COLOR.paper, color: COLOR.risk, fontSize: 30, fontWeight: 700 }}>
        <Lock size={40} color={COLOR.risk} /> запрещено
      </div>
      <div style={{ position: "absolute", left: 40, bottom: 34, fontSize: 28, color: COLOR.muted, width: 700 }}>банковская тайна и закон о персональных данных</div>
    </Card>
  );
}

function CentralOperator() {
  const s = useSec();
  const frame = useCurrentFrame();
  const fog = interpolate(frame, [s(8), s(9.5)], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const banks = [{ x: 140, y: 330 }, { x: 390, y: 400 }, { x: 640, y: 330 }];
  return (
    <Card title="Центральный оператор" tone={COLOR.blue}>
      <div style={{ position: "absolute", left: 330, top: 110, width: 120, height: 70, borderRadius: "50%", border: `5px solid ${COLOR.blue}`,
        display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div style={{ width: 30, height: 30, borderRadius: 15, background: COLOR.blue }} />
      </div>
      <svg style={{ position: "absolute", left: 0, top: 0 }} width={780} height={560}>
        <path d="M210 330 L320 400 M460 400 L570 330" stroke={COLOR.accent} strokeWidth={6} fill="none" strokeDasharray="14 10" />
        {banks.map((b, i) => <line key={i} x1={390} y1={180} x2={b.x} y2={b.y - 45} stroke={COLOR.blue} strokeWidth={2} opacity={0.4} />)}
      </svg>
      {banks.map((b, i) => <MiniBank key={i} x={b.x} y={b.y} label={["A", "B", "C"][i]} fog={fog} />)}
      <div style={{ position: "absolute", left: 40, bottom: 34, fontSize: 28, color: COLOR.muted, width: 700 }}>видит переводы между банками, но не то, что внутри</div>
      <div style={{ fontFamily: FONT.mono, fontSize: 24, color: COLOR.blue, marginTop: 6 }}>как Mastercard MITS</div>
    </Card>
  );
}

export const Unsolved = () => {
  const s = useSec();
  const cam = useCamera([
    [0, { x: -520, y: 80, z: 160, ry: 10 }],
    [5.5, { x: -500, y: 80, z: 220, ry: 6 }],
    [7.5, { x: 520, y: 80, z: 200, ry: -8 }],
    [12, { x: 520, y: 80, z: 240, ry: -6 }],
    [15, { x: 0, y: 80, z: -260, ry: 0 }],
  ]);
  return (
    <Stage>
      <Ground />
      <Space cam={cam}>
        <Backdrop seed="unsolved" />
        <Layer><At x={-520} y={120}><PooledDatabase /></At></Layer>
        <Layer z={-120}><At x={560} y={120}><CentralOperator /></At></Layer>
      </Space>
      <div style={{ position: "absolute", left: PAD, top: 70, display: "flex", flexDirection: "column", gap: 16 }}>
        <Kicker at={s(0.2)}>Почему это не решено</Kicker>
        <Headline at={s(0.5)} size={60}>Обе очевидные идеи упираются в стену</Headline>
      </div>
    </Stage>
  );
};
