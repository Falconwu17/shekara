// 04. Идея: не данные клиента, а одна оценка риска по ссылке на реальный перевод. Камера ведёт конверт
// от банка B к банку A, затем опускается к журналу хаба, где записи ложатся цепочкой.
import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { Words } from "../kinetic";
import { At, Backdrop, Ground, Layer, Space, useCamera } from "../space";
import { COLOR, FONT } from "../theme";
import { Bank, Envelope, Lock, Stage, Title, useProgress, useSec } from "../ui";

const A = { x: -700, y: -40 };
const B = { x: 700, y: -40 };
const HASHES = ["9f3a", "c41e", "07bd", "e5a2", "3c90", "b7f1"];

function JournalBlock({ i, at }: { i: number; at: number }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = spring({ frame: frame - at, fps, config: { damping: 14 } });
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 14, opacity: Math.min(1, p * 2),
      transform: `translateY(${(1 - p) * -120}px) translateZ(${(1 - p) * 200}px)` }}>
      <div style={{ width: 130, height: 84, borderRadius: 14, background: COLOR.navy, color: COLOR.amber, fontFamily: FONT.mono, fontSize: 26,
        display: "flex", alignItems: "center", justifyContent: "center", boxShadow: "0 20px 40px rgba(20,33,61,.25)" }}>{HASHES[i]}</div>
      {i < HASHES.length - 1 ? <div style={{ width: 26, height: 5, background: COLOR.navyLine }} /> : null}
    </div>
  );
}

export const Idea = () => {
  const s = useSec();
  const frame = useCurrentFrame();
  const link = useProgress(s(2.5), s(1.2));
  const fly = useProgress(s(8.6), s(2.6));
  const open = useProgress(s(11.2), s(0.6));
  const strike = interpolate(frame, [s(6.2), s(6.8)], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const envX = interpolate(fly, [0, 1], [B.x - 330, A.x + 340]);
  const envY = A.y + 60 - Math.sin(fly * Math.PI) * 160;
  const cam = useCamera([
    [0, { x: 0, y: -20, z: -260, rx: 4 }],
    [3.8, { x: 0, y: -20, z: -120, rx: 4 }],
    [8.6, { x: 560, y: -20, z: 160, ry: -10 }],
    [11.2, { x: -560, y: -20, z: 200, ry: 10 }],
    [13.5, { x: -300, y: 20, z: 60, ry: 4 }],
    [16, { x: 0, y: 640, z: 160, rx: 14 }],
    [25, { x: 120, y: 660, z: 220, rx: 16 }],
  ]);
  return (
    <Stage>
      <Ground />
      <Space cam={cam}>
        <Backdrop seed="idea" />
        <Layer>
          <Bank x={A.x} y={A.y} name="Банк A" width={380} height={280}><div style={{ fontSize: 26, color: COLOR.muted }}>спрашивает</div></Bank>
          <Bank x={B.x} y={B.y} name="Банк B" width={380} height={280}><div style={{ fontSize: 26, color: COLOR.muted }}>отвечает о своём счёте</div></Bank>
          <div style={{ position: "absolute", left: A.x + 190, top: A.y - 3, width: (B.x - A.x - 380) * link, height: 6, background: COLOR.accent, borderRadius: 3 }} />
        </Layer>
        <Layer z={80}>
          <At x={0} y={A.y - 120}>
            <Words text="ссылка на реальный перевод" at={s(3)} style={{ fontSize: 36, fontWeight: 600, color: COLOR.accent, whiteSpace: "nowrap", flexWrap: "nowrap" }} />
          </At>
          <At x={0} y={A.y - 70}>
            <Words text="уже зачисленный, есть у обоих банков" at={s(3.6)} stagger={2} style={{ fontSize: 26, color: COLOR.muted, whiteSpace: "nowrap", flexWrap: "nowrap" }} />
          </At>
          <At x={60} y={A.y + 150}>
            <div style={{ position: "relative", fontSize: 32, color: COLOR.risk, opacity: interpolate(frame, [s(5.4), s(5.8)], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }) }}>
              ФИО, ИИН, баланс, история счёта
              <div style={{ position: "absolute", left: 0, top: "52%", width: `${strike * 100}%`, height: 4, background: COLOR.risk }} />
            </div>
          </At>
        </Layer>
        <Layer z={160}>{fly > 0 ? <Envelope x={envX} y={envY} score="0,87" open={open} /> : null}</Layer>
        <Layer z={60}>
          <At x={A.x + 260} y={A.y + 230}>
            <div style={{ display: "flex", gap: 12, alignItems: "center", opacity: open, whiteSpace: "nowrap" }}>
              <Lock size={34} color={COLOR.navy} />
              <span style={{ fontSize: 28, color: COLOR.navy }}>расшифровать может только банк A</span>
            </div>
          </At>
        </Layer>
        <Layer z={-40}>
          <At x={0} y={700}>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 26 }}>
              <Words text="Журнал хаба: хаб видит только шифротекст, запись нельзя подделать" at={s(14.5)} stagger={2}
                style={{ fontSize: 34, fontWeight: 600, color: COLOR.navy, whiteSpace: "nowrap", flexWrap: "nowrap" }} />
              <div style={{ display: "flex", alignItems: "center", transformStyle: "preserve-3d" }}>
                {HASHES.map((_, i) => <JournalBlock key={i} i={i} at={s(15.5 + i * 0.9)} />)}
              </div>
            </div>
          </At>
        </Layer>
      </Space>
      <Title kicker="ШЕКАРА по-казахски значит «граница»" headline="Не данные клиента, а одна оценка риска" at={s(0.2)} hideAt={s(13)} />
    </Stage>
  );
};
