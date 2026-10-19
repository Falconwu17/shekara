// 01. Вечерний звонок «службы безопасности»: камера от часов к телефону, к счёту и за улетающими деньгами.
import { interpolate, useCurrentFrame } from "remotion";
import { CountUp, Words } from "../kinetic";
import { At, Backdrop, Ground, Layer, Space, useCamera } from "../space";
import { COLOR, FONT } from "../theme";
import { Stage, useProgress, useSec } from "../ui";

function Rings({ active }: { active: boolean }) {
  const frame = useCurrentFrame();
  if (!active) return null;
  return (
    <>
      {[0, 1, 2].map(i => {
        const t = ((frame + i * 12) % 36) / 36;
        return <div key={i} style={{ position: "absolute", left: 115 - 160 * t, top: 210 - 160 * t, width: 320 * t, height: 320 * t,
          borderRadius: "50%", border: `3px solid ${COLOR.amber}`, opacity: 1 - t }} />;
      })}
    </>
  );
}

function Phone({ ring }: { ring: boolean }) {
  const frame = useCurrentFrame();
  const shake = ring ? Math.sin(frame * 1.7) * 5 : 0;
  return (
    <div style={{ position: "relative", width: 230, height: 420 }}>
      <Rings active={ring} />
      <div style={{ position: "absolute", inset: 0, borderRadius: 40, border: `6px solid ${COLOR.soft}`, transform: `rotate(${shake}deg)`,
        display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 18, background: "#1C2B4C",
        boxShadow: "0 40px 80px rgba(0,0,0,.45)" }}>
        <div style={{ width: 92, height: 92, borderRadius: 46, background: ring ? COLOR.ok : COLOR.navyLine }} />
        <div style={{ fontSize: 24, color: COLOR.cream, textAlign: "center", lineHeight: 1.3 }}>Служба<br />безопасности<br />банка</div>
      </div>
    </div>
  );
}

/** Монета со шлейфом: несколько полупрозрачных копий позади. */
function FlyingMoney({ start, frames }: { start: number; frames: number }) {
  const frame = useCurrentFrame();
  if (frame < start) return null;
  return (
    <>
      {[0, 3, 6, 9].map((lag, i) => {
        const p = interpolate(frame - lag, [start, start + frames], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
        const x = 300 + 1500 * p * p;
        const y = 40 - Math.sin(p * Math.PI) * 140;
        return <div key={lag} style={{ position: "absolute", left: x - 40, top: y - 40, width: 80, height: 80, borderRadius: 40,
          background: COLOR.accent, opacity: i === 0 ? 1 : 0.35 / i, color: COLOR.paper, display: "flex", alignItems: "center",
          justifyContent: "center", fontFamily: FONT.mono, fontSize: 32, fontWeight: 600, boxShadow: i === 0 ? "0 10px 30px rgba(0,0,0,.4)" : undefined }}>₸</div>;
      })}
    </>
  );
}

export const Hook = () => {
  const s = useSec();
  const frame = useCurrentFrame();
  const cam = useCamera([
    [0, { x: -560, y: -390, z: 520, ry: 6 }],
    [2.4, { x: -420, y: -110, z: 120, ry: 3 }],
    [5.2, { x: -140, y: -40, z: 60, ry: -2 }],
    [7.2, { x: 80, y: -10, z: 140, ry: -4 }],
    [10.5, { x: 420, y: 40, z: -60, ry: -8 }],
  ]);
  const drained = useProgress(s(6.4), s(1.2));
  return (
    <Stage dark>
      <Ground dark />
      <Space cam={cam}>
        <Backdrop dark seed="hook" />
        <Layer z={60}>
          <At x={-560} y={-400}>
            <Words text="Пятница, 19:40" at={s(0.2)} stagger={4} style={{ fontFamily: FONT.mono, fontSize: 84, fontWeight: 600, color: COLOR.amber, flexWrap: "nowrap", whiteSpace: "nowrap" }} />
          </At>
        </Layer>
        <Layer z={0}>
          <At x={-560} y={-20}><Phone ring={frame > s(1) && frame < s(4.6)} /></At>
        </Layer>
        <Layer z={-40}>
          <div style={{ position: "absolute", left: -120, top: -120, width: 580, padding: 36, borderRadius: 26, background: COLOR.cream, color: COLOR.ink,
            boxShadow: "0 40px 80px rgba(0,0,0,.4)", opacity: interpolate(frame, [s(4.2), s(4.8)], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }) }}>
            <div style={{ fontSize: 28, color: COLOR.muted }}>Счёт пенсионерки, банк А</div>
            <div style={{ fontFamily: FONT.mono, fontSize: 66, fontWeight: 600, marginTop: 8, color: drained > 0.5 ? COLOR.risk : COLOR.ink }}>
              <CountUp from={2_400_000} to={0} at={s(6.4)} frames={s(1.2)} suffix=" ₸" />
            </div>
          </div>
          <FlyingMoney start={s(6.6)} frames={s(2.6)} />
        </Layer>
        <Layer z={120}>
          <At x={520} y={260}>
            <Words text="Через 15 минут деньги уже в другом банке" at={s(8.4)} style={{ fontSize: 50, color: COLOR.cream, width: 900 }} />
          </At>
        </Layer>
      </Space>
    </Stage>
  );
};
