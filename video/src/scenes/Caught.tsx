// 05. Перевод пойман: проверка при зачислении за 0,7 мс, алерт, шаг по ссылке к следующим банкам,
// камера идёт за светящимся следом, затем отъезд к оператору, который собирает дерево цепочки.
import { interpolate, useCurrentFrame } from "remotion";
import { CountUp, Words } from "../kinetic";
import { At, Backdrop, Ground, Layer, Space, useCamera } from "../space";
import { COLOR, FONT } from "../theme";
import { Bank, Coin, PAD, Stage, Title, useSec } from "../ui";

const BANKS = [{ name: "Банк A", x: -1050 }, { name: "Банк B", x: -350 }, { name: "Банк C", x: 350 }, { name: "Банк D", x: 1050 }];
const Y = 120;
const AFC = { x: 0, y: -330 };

function Pulse({ x, y, at }: { x: number; y: number; at: number }) {
  const frame = useCurrentFrame();
  if (frame < at) return null;
  return (
    <>
      {[0, 20].map(lag => {
        const t = ((frame - at + lag) % 40) / 40;
        return <div key={lag} style={{ position: "absolute", left: x - 180 - 80 * t, top: y - 125 - 80 * t, width: 360 + 160 * t, height: 250 + 160 * t,
          borderRadius: 36, border: `5px solid ${COLOR.risk}`, opacity: 1 - t }} />;
      })}
    </>
  );
}

export const Caught = () => {
  const s = useSec();
  const frame = useCurrentFrame();
  const trace = interpolate(frame, [s(4.5), s(8)], [BANKS[1].x, BANKS[3].x], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const tree = interpolate(frame, [s(10), s(12.5)], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const cam = useCamera([
    [0, { x: -700, y: 100, z: 120, ry: 8 }],
    [2, { x: -350, y: 100, z: 300, ry: 2 }],
    [4.5, { x: -350, y: 100, z: 260, ry: 0 }],
    [8, { x: 1050, y: 100, z: 240, ry: -8 }],
    [10.5, { x: 0, y: -40, z: -420, rx: -6 }],
    [18, { x: 0, y: -60, z: -460, rx: -8 }],
  ]);
  return (
    <Stage>
      <Ground />
      <Space cam={cam}>
        <Backdrop seed="caught" />
        <Layer z={-20}>
          <div style={{ position: "absolute", left: BANKS[1].x, top: Y - 4, width: trace - BANKS[1].x, height: 8, borderRadius: 4,
            background: COLOR.risk, boxShadow: `0 0 24px ${COLOR.risk}` }} />
        </Layer>
        <Layer>
          {BANKS.map((bank, i) => (
            <Bank key={bank.name} x={bank.x} y={Y} name={bank.name} width={360} height={250} lit={i <= 1 || bank.x <= trace ? 1 : 0.3}>
              {i === 1 && frame > s(1.8) ? <div style={{ fontFamily: FONT.mono, fontSize: 32, fontWeight: 600, color: COLOR.risk }}>алерт</div> : null}
              {i > 1 && bank.x <= trace ? <div style={{ fontSize: 24, color: COLOR.risk }}>шаг по ссылке</div> : null}
            </Bank>
          ))}
          <Coin x1={BANKS[0].x} y1={Y + 50} x2={BANKS[1].x} y2={Y + 50} start={0} frames={s(1.4)} until={s(1.8)} />
          <Pulse x={BANKS[1].x} y={Y} at={s(1.6)} />
        </Layer>
        <Layer z={160}>
          <At x={BANKS[1].x} y={Y - 220}>
            <div style={{ fontFamily: FONT.mono, fontSize: 64, fontWeight: 600, color: COLOR.risk, opacity: frame > s(1.8) && frame < s(9.5) ? 1 : 0 }}>
              <CountUp to={0.7} at={s(1.8)} frames={s(0.8)} digits={1} suffix=" мс" />
            </div>
          </At>
        </Layer>
        <Layer z={-60}>
          <At x={AFC.x} y={AFC.y}>
            <div style={{ padding: "22px 36px", borderRadius: 20, border: `4px solid ${COLOR.blue}`, background: COLOR.paper, fontSize: 36, fontWeight: 600,
              color: COLOR.blue, opacity: tree, transform: `scale(${0.8 + 0.2 * tree})`, whiteSpace: "nowrap" }}>Антифрод-центр НБК</div>
          </At>
          <svg style={{ position: "absolute", left: -1400, top: -600, overflow: "visible" }} width={2800} height={1200}>
            {BANKS.slice(1).map((bank, i) => {
              const p = interpolate(tree, [i * 0.2, i * 0.2 + 0.5], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
              const x1 = AFC.x + 1400, y1 = AFC.y + 600 + 50, x2 = bank.x + 1400, y2 = Y + 600 - 125;
              return <line key={bank.name} x1={x1} y1={y1} x2={x1 + (x2 - x1) * p} y2={y1 + (y2 - y1) * p} stroke={COLOR.blue} strokeWidth={5} strokeDasharray="12 8" />;
            })}
          </svg>
        </Layer>
      </Space>
      <Title kicker="Перевод пойман" headline="Алерт при зачислении и шаг по ссылке" at={s(0.2)} hideAt={s(9.5)} />
      <div style={{ position: "absolute", left: PAD, bottom: 70, width: 1500 }}>
        <Words text="Полную цепочку собирает только оператор с мандатом; банки видят свой сегмент" at={s(12)} stagger={2} style={{ fontSize: 36, color: COLOR.ink }} />
      </div>
    </Stage>
  );
};
