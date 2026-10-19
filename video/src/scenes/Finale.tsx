// 08. Финал: два банка соединяются светящейся линией (пилот), буквы логотипа влетают из глубины, камера отъезжает.
import { spring, useCurrentFrame, useVideoConfig } from "remotion";
import { Words } from "../kinetic";
import { At, Backdrop, Ground, Layer, Space, useCamera } from "../space";
import { COLOR, FONT } from "../theme";
import { Stage, useProgress, useSec } from "../ui";

function Letter({ ch, i, at }: { ch: string; i: number; at: number }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = spring({ frame: frame - at - i * 3, fps, config: { damping: 14, mass: 0.8 } });
  return (
    <span style={{ display: "inline-block", opacity: Math.min(1, p * 1.6), transform: `translateZ(${(1 - p) * 900}px) translateY(${(1 - p) * 60}px) rotateX(${(1 - p) * 50}deg)` }}>
      {ch}
    </span>
  );
}

export const Finale = () => {
  const s = useSec();
  const join = useProgress(s(0.6), s(1.6));
  const cam = useCamera([
    [0, { x: 0, y: -330, z: 260, rx: 10 }],
    [3, { x: 0, y: -200, z: 60, rx: 6 }],
    [7, { x: 0, y: 0, z: -40, rx: 0 }],
    [15, { x: 0, y: 20, z: -180, ry: -4 }],
  ]);
  return (
    <Stage dark>
      <Ground dark />
      <Space cam={cam}>
        <Backdrop dark seed="finale" />
        <Layer z={-60}>
          {[-420, 420].map((x, i) => (
            <At key={x} x={x} y={-330}>
              <div style={{ width: 280, height: 130, borderRadius: 22, border: `3px dashed ${COLOR.soft}`, display: "flex", alignItems: "center",
                justifyContent: "center", fontFamily: FONT.mono, fontSize: 32, color: COLOR.cream }}>Банк {i + 1}</div>
            </At>
          ))}
          <div style={{ position: "absolute", left: -280, top: -333, width: 560 * join, height: 6, borderRadius: 3, background: COLOR.amber,
            boxShadow: `0 0 30px ${COLOR.amber}` }} />
          <At x={0} y={-200}>
            <Words text="Следующий шаг: пилот с двумя банками в песочнице НБК" at={s(1.6)} stagger={2}
              style={{ fontSize: 36, color: COLOR.amber, whiteSpace: "nowrap", flexWrap: "nowrap" }} />
          </At>
        </Layer>
        <Layer z={40}>
          <At x={0} y={20}>
            <div style={{ fontFamily: FONT.serif, fontSize: 220, fontWeight: 700, color: COLOR.cream, lineHeight: 1, transformStyle: "preserve-3d", whiteSpace: "nowrap" }}>
              {"ШЕКАРА".split("").map((ch, i) => <Letter key={i} ch={ch} i={i} at={s(3.6)} />)}
            </div>
          </At>
          <At x={0} y={190}>
            <Words text="граница для мошенников, а не для банков" at={s(5.4)} stagger={3}
              style={{ fontSize: 54, color: COLOR.cream, whiteSpace: "nowrap", flexWrap: "nowrap" }} />
          </At>
        </Layer>
        <Layer z={-20}>
          <At x={0} y={400}>
            <div style={{ textAlign: "center", opacity: useProgress(s(7.5), s(0.8)) }}>
              <div style={{ fontSize: 32, color: COLOR.soft }}>Джусупов Сагир · ЗКАТУ имени Жангир хана · Уральск</div>
              <div style={{ fontFamily: FONT.mono, fontSize: 40, fontWeight: 600, color: COLOR.amber, marginTop: 14 }}>shekara.21restobar.kz</div>
            </div>
          </At>
        </Layer>
      </Space>
    </Stage>
  );
};
