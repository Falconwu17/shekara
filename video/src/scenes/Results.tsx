// 06. Результаты третьей версии эксперимента (docs/submission/numbers.md): столбцы растут на полу сцены,
// камера облетает их, значения досчитываются, «0,337 против 0,251» ставится штампом.
import { interpolate, useCurrentFrame } from "remotion";
import { CountUp, Words, useStamp } from "../kinetic";
import { At, Backdrop, Ground, Layer, Space, useCamera } from "../space";
import { COLOR, FONT } from "../theme";
import type { SceneProps } from "../timeline";
import { Headline, Kicker, PAD, Stage, useSec } from "../ui";

const ROWS = [
  { label: "Центральный оператор", sub: "как Mastercard MITS", value: 0.251, color: COLOR.grey },
  { label: "Общий граф", sub: "данные сложены вместе", value: 0.313, color: COLOR.grey },
  { label: "Изолированный банк", sub: "как сейчас", value: 0.313, color: "#5B6B8C" },
  { label: "ШЕКАРА", sub: "обмен оценками", value: 0.337, color: COLOR.accent },
];
const MAX_H = 520;
const FLOOR = 380;
const GAP = 420;

function Column({ i, at, grow }: { i: number; at: number; grow: number }) {
  const frame = useCurrentFrame();
  const row = ROWS[i];
  const p = interpolate(frame, [at, at + grow], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const h = (row.value / 0.337) * MAX_H * (1 - Math.pow(1 - p, 3));
  const x = (i - 1.5) * GAP;
  const strong = row.label === "ШЕКАРА";
  return (
    <>
      <div style={{ position: "absolute", left: x - 110, top: FLOOR - h, width: 220, height: h, borderRadius: "16px 16px 4px 4px",
        background: `linear-gradient(${row.color}, ${row.color}CC)`, boxShadow: strong ? `0 0 60px ${COLOR.accent}55` : "0 20px 40px rgba(20,33,61,.15)" }} />
      <At x={x} y={FLOOR - h - 50}>
        <div style={{ fontFamily: FONT.mono, fontSize: 52, fontWeight: 600, color: strong ? COLOR.accent : COLOR.ink, opacity: p > 0.05 ? 1 : 0 }}>
          <CountUp to={row.value} at={at} frames={grow} digits={3} />
        </div>
      </At>
      <At x={x} y={FLOOR + 70}>
        <div style={{ textAlign: "center", width: 380, opacity: p > 0.05 ? 1 : 0 }}>
          <div style={{ fontSize: 32, fontWeight: strong ? 700 : 500, color: COLOR.ink }}>{row.label}</div>
          <div style={{ fontSize: 24, color: COLOR.muted }}>{row.sub}</div>
        </div>
      </At>
    </>
  );
}

export const Results = ({ short }: SceneProps) => {
  const s = useSec();
  const step = short ? 0.6 : 2;
  const stamp = useStamp(s(short ? 3.6 : 9.5));
  const cam = useCamera(short
    ? [[0, { x: 0, y: 150, z: -300, rx: 8, ry: 10 }], [10, { x: 0, y: 150, z: -140, rx: 8, ry: -6 }]]
    : [[0, { x: -620, y: 160, z: 40, rx: 6, ry: 16 }], [8, { x: 300, y: 140, z: -80, rx: 8, ry: -6 }], [12, { x: 0, y: 150, z: -300, rx: 8, ry: 0 }],
       [25, { x: 0, y: 150, z: -220, rx: 6, ry: -4 }]]);
  return (
    <Stage>
      <Ground />
      <Space cam={cam}>
        <Backdrop seed="results" />
        <Layer z={-30}>
          <div style={{ position: "absolute", left: -900, top: FLOOR, width: 1800, height: 6, borderRadius: 3, background: COLOR.navyLine, opacity: 0.4 }} />
        </Layer>
        <Layer>{ROWS.map((_, i) => <Column key={i} i={i} at={s(1 + step * i)} grow={s(short ? 0.8 : 1.4)} />)}</Layer>
        <Layer z={160}>
          <At x={-650} y={-150}>
            <div style={{ ...stamp, padding: "22px 36px", borderRadius: 22, background: COLOR.accent, color: COLOR.paper, fontSize: 52, fontWeight: 700,
              whiteSpace: "nowrap", boxShadow: "0 24px 50px rgba(168,74,20,.35)" }}>0,337 против 0,251</div>
          </At>
        </Layer>
      </Space>
      <div style={{ position: "absolute", left: PAD, top: 70, display: "flex", flexDirection: "column", gap: 16 }}>
        <Kicker at={s(0.2)}>Результаты · 5,08 млн переводов · 30 банков</Kicker>
        <Headline at={s(0.5)} size={62}>Сложить данные в одно место не помогает</Headline>
      </div>
      {short ? null : (
        <div style={{ position: "absolute", left: PAD, bottom: 96, width: 1600 }}>
          <Words text="Обмен оценками добавляет ещё +8% во всех запусках; статистически это пока не доказано, проверит пилот" at={s(14)} stagger={2}
            style={{ fontSize: 34, color: COLOR.ink }} />
        </div>
      )}
      <div style={{ position: "absolute", left: PAD, bottom: 40, fontSize: 22, color: COLOR.muted }}>
        PR-AUC, IBM AMLworld HI-Small, тест 8-10.09, среднее по 3 запускам
      </div>
    </Stage>
  );
};
