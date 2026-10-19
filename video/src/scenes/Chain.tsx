// 02. Деньги прыгают по дропам в разных банках. Камера летит за деньгами; светится только банк, где они сейчас:
// каждый банк видит лишь своё звено. В конце отъезд на всю цепочку и цифры потерь.
import { useCurrentFrame } from "remotion";
import { CountUp } from "../kinetic";
import { At, Backdrop, Ground, Layer, Space, useCamera } from "../space";
import { COLOR, FONT } from "../theme";
import { Bank, Coin, PAD, Stage, Title, useSec } from "../ui";

const BANKS = [
  { name: "Банк A", x: -1050, role: "жертва" },
  { name: "Банк B", x: -350, role: "дроп 1" },
  { name: "Банк C", x: 350, role: "дроп 2" },
  { name: "Банк D", x: 1050, role: "дроп 3" },
];
const Y = 60;

/** Граница банка: вертикальная пунктирная линия с подписью. */
function Border({ x }: { x: number }) {
  return (
    <div style={{ position: "absolute", left: x - 2, top: Y - 260, height: 520, borderLeft: `4px dashed ${COLOR.navyLine}`, opacity: 0.5 }}>
      <div style={{ position: "absolute", top: -40, left: -60, width: 120, textAlign: "center", fontSize: 22, color: COLOR.muted }}>граница</div>
    </div>
  );
}

export const Chain = () => {
  const s = useSec();
  const frame = useCurrentFrame();
  const hops = [s(1), s(3), s(5), s(7)];
  const holder = hops.filter(t => frame >= t).length - 1;
  const cam = useCamera([
    [0, { x: -1050, y: 40, z: 260, ry: 8 }],
    [1, { x: -1050, y: 40, z: 300, ry: 6 }],
    [3, { x: -350, y: 40, z: 300, ry: 2 }],
    [5, { x: 350, y: 40, z: 300, ry: -2 }],
    [7, { x: 1050, y: 40, z: 300, ry: -6 }],
    [9.5, { x: 0, y: 160, z: -330, rx: 12 }],
    [18, { x: 0, y: 170, z: -380, rx: 14 }],
  ]);
  return (
    <Stage>
      <Ground />
      <Space cam={cam}>
        <Backdrop seed="chain" />
        <Layer z={-80}>{[-700, 0, 700].map(x => <Border key={x} x={x} />)}</Layer>
        <Layer>
          {BANKS.map((bank, i) => (
            <Bank key={bank.name} x={bank.x} y={Y} name={bank.name} width={360} height={250}
              lit={frame < hops[0] || frame > s(9) || holder === i ? 1 : 0.18}>
              <div style={{ fontSize: 26, color: COLOR.muted }}>{bank.role}</div>
            </Bank>
          ))}
          {BANKS.slice(0, -1).map((bank, i) => (
            <Coin key={bank.name} x1={bank.x} y1={Y + 50} x2={BANKS[i + 1].x} y2={Y + 50} start={hops[i + 1] - s(1)} frames={s(1)}
              until={i < 2 ? hops[i + 2] - s(1) : s(8)} />
          ))}
          <Coin x1={BANKS[3].x} y1={Y + 50} x2={1900} y2={Y - 60} start={s(8)} frames={s(1.2)} label="₿" />
        </Layer>
        <Layer z={140}>
          {BANKS.map((bank, i) => (
            <At key={bank.name} x={bank.x} y={Y - 210}>
              <div style={{ fontSize: 26, fontWeight: 600, color: COLOR.accent, opacity: holder === i && frame < s(9) ? 1 : 0, whiteSpace: "nowrap" }}>
                {i === 0 ? "видит списание" : "видит обычный перевод"}
              </div>
            </At>
          ))}
        </Layer>
      </Space>
      <Title kicker="Цепочка дропов" headline="Каждый банк видит только своё звено" at={s(0.3)} size={68} />
      <div style={{ position: "absolute", left: PAD, bottom: 70, display: "flex", gap: 120, opacity: frame > s(9.5) ? 1 : 0 }}>
        <div>
          <div style={{ fontFamily: FONT.serif, fontSize: 92, fontWeight: 700 }}>≈ <CountUp to={45} at={s(9.6)} frames={s(1.4)} /> млрд ₸</div>
          <div style={{ fontSize: 30, color: COLOR.muted }}>потеряли казахстанцы в 2024 году</div>
        </div>
        <div style={{ opacity: frame > s(11.5) ? 1 : 0 }}>
          <div style={{ fontFamily: FONT.serif, fontSize: 92, fontWeight: 700, color: COLOR.accent }}>≈ <CountUp to={2} at={s(11.6)} frames={s(0.8)} /> млрд ₸</div>
          <div style={{ fontSize: 30, color: COLOR.muted }}>удалось вернуть</div>
        </div>
      </div>
    </Stage>
  );
};
