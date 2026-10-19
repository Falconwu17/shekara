// 07. Работающий продукт: интерфейс стенда в 2,5D. Курсор запрашивает выпуск, приходит оценка,
// в журнале хаба появляется запись, подпись журнала проверяется. Подписи взяты с настоящего стенда.
import type { CSSProperties, ReactNode } from "react";
import { AbsoluteFill, Easing, interpolate, useCurrentFrame } from "remotion";
import { COLOR, FONT } from "../theme";
import type { SceneProps } from "../timeline";
import { Lock, Stage, useSec } from "../ui";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.bezier(0.45, 0, 0.2, 1) } as const;

// Мир сцены: координаты панелей и кнопок, по ним движутся камера и курсор.
const FEED = { x: 0, y: 0, w: 1500 };
const PEER = { x: 1640, y: 40, w: 520 };
const JOURNAL = { x: 260, y: 740, w: 1700 };
const ASK_BUTTON = { x: 1310, y: 190 };
const VERIFY_BUTTON = { x: 1425, y: 1067 };

/** Ключевые моменты полной версии (секунды); короткая версия идёт быстрее и без журнала. */
const FULL = { hover: 1.4, click: 2.6, flyEnd: 4.4, answer: 4.8, panJournal: 7, row: 8.4, hoverVerify: 10, verify: 11, ok: 11.4 };
const SHORT = { hover: 0.6, click: 1.3, flyEnd: 2.4, answer: 2.7, panJournal: 90, row: 92, hoverVerify: 94, verify: 96, ok: 98 }; // журнал в короткой версии не показывается

function Panel({ x, y, w, title, aside, children }: { x: number; y: number; w: number; title: string; aside?: string; children: ReactNode }) {
  return (
    <div style={{ position: "absolute", left: x, top: y, width: w, padding: 28, borderRadius: 22, background: COLOR.paper,
      boxShadow: "0 30px 60px rgba(20,33,61,.16), 0 4px 12px rgba(20,33,61,.08)", display: "flex", flexDirection: "column", gap: 18 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div style={{ fontSize: 30, fontWeight: 600, color: COLOR.ink }}>{title}</div>
        {aside ? <div style={{ fontSize: 22, color: COLOR.muted }}>{aside}</div> : null}
      </div>
      {children}
    </div>
  );
}

function Chip({ children, tone }: { children: ReactNode; tone: string }) {
  return <span style={{ padding: "4px 12px", borderRadius: 999, fontSize: 20, fontWeight: 600, color: tone, background: `${tone}1A` }}>{children}</span>;
}

function Button({ children, pressed = 0, style }: { children: ReactNode; pressed?: number; style?: CSSProperties }) {
  return (
    <div style={{ padding: "12px 22px", borderRadius: 12, border: `2px solid ${COLOR.blue}`, fontSize: 24, fontWeight: 600,
      color: pressed > 0.5 ? COLOR.paper : COLOR.blue, background: pressed > 0.5 ? COLOR.blue : COLOR.paper,
      transform: `scale(${1 - 0.06 * Math.sin(Math.min(1, pressed) * Math.PI)})`, whiteSpace: "nowrap", ...style }}>{children}</div>
  );
}

const ROWS = [
  { time: "09.10 20:41", side: "вход", bank: "0211", amount: "184 500 ₸" },
  { time: "09.10 20:44", side: "вход", bank: "0213", amount: "2 400 000 ₸", partner: true },
  { time: "09.10 20:47", side: "выход", bank: "01292", amount: "96 000 ₸" },
];

function Cursor({ x, y, down }: { x: number; y: number; down: boolean }) {
  return (
    <svg width={64} height={64} viewBox="0 0 24 24" style={{ position: "absolute", left: x - 6, top: y - 4, transform: `scale(${down ? 0.88 : 1})`,
      filter: "drop-shadow(0 6px 10px rgba(0,0,0,.35))", zIndex: 10 }}>
      <path d="M3 2 L3 19 L8 14.5 L11.5 22 L14.5 20.6 L11 13.4 L18 13.4 Z" fill={COLOR.ink} stroke={COLOR.paper} strokeWidth={1.4} strokeLinejoin="round" />
    </svg>
  );
}

function Ripple({ x, y, at }: { x: number; y: number; at: number }) {
  const frame = useCurrentFrame();
  const p = interpolate(frame, [at, at + 18], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  if (frame < at || p >= 1) return null;
  return <div style={{ position: "absolute", left: x - 70 * p, top: y - 70 * p, width: 140 * p, height: 140 * p, borderRadius: "50%",
    border: `4px solid ${COLOR.blue}`, opacity: 1 - p, zIndex: 9 }} />;
}

export const Product = ({ short }: SceneProps) => {
  const s = useSec();
  const frame = useCurrentFrame();
  const T = Object.fromEntries(Object.entries(short ? SHORT : FULL).map(([k, v]) => [k, s(v)])) as Record<keyof typeof FULL, number>;
  const at = (a: number, b: number) => interpolate(frame, [a, b], [0, 1], clamp);

  // Камера: следит за лентой, потом показывает ответ банка-контрагента, потом журнал.
  const toPeer = at(T.click, T.flyEnd);
  const toJournal = at(T.panJournal, T.panJournal + s(1.4));
  const cx = 860 + 260 * toPeer + 40 * toJournal;
  const cy = 330 + 560 * toJournal;
  const zoom = 1.02 - 0.14 * toPeer + 0.1 * toJournal;
  const tilt = interpolate(frame, [0, s(12)], [-9, -4], { extrapolateRight: "clamp" });

  // Курсор: из угла к «Запросить выпуск», потом к «Проверить подпись».
  const toAsk = at(0, T.hover);
  const toVerify = at(T.row, T.hoverVerify);
  const curX = interpolate(toAsk, [0, 1], [ASK_BUTTON.x + 500, ASK_BUTTON.x]) + (VERIFY_BUTTON.x - ASK_BUTTON.x) * toVerify;
  const curY = interpolate(toAsk, [0, 1], [ASK_BUTTON.y + 420, ASK_BUTTON.y]) + (VERIFY_BUTTON.y - ASK_BUTTON.y) * toVerify;
  const down = Math.abs(frame - T.click) < 4 || Math.abs(frame - T.verify) < 4;

  const fly = at(T.click + 4, T.flyEnd);
  const answer = at(T.answer, T.answer + s(0.5));
  const newRow = at(T.row, T.row + s(0.6));
  const ok = at(T.ok, T.ok + s(0.4));
  const envX = interpolate(fly, [0, 1], [ASK_BUTTON.x - 40, PEER.x + PEER.w / 2]);
  const envY = interpolate(fly, [0, 1], [ASK_BUTTON.y, PEER.y + 150]) - Math.sin(fly * Math.PI) * 120;

  return (
    <Stage>
    <AbsoluteFill style={{ background: `radial-gradient(circle at 40% 30%, #FFFFFF, ${COLOR.cream})`, fontFamily: FONT.sans, perspective: 2200, overflow: "hidden" }}>
      <div style={{ position: "absolute", left: 0, top: 0, transformOrigin: "0 0",
        transform: `translate(${960 - cx * zoom}px, ${560 - cy * zoom}px) scale(${zoom}) rotateX(8deg) rotateY(${tilt}deg)` }}>
        <Panel {...FEED} title="Лента переводов банка 070" aside="только свои переводы">
          {ROWS.map(row => (
            <div key={row.time} style={{ display: "grid", gridTemplateColumns: "190px 110px 260px 220px 1fr", alignItems: "center", fontSize: 24,
              padding: "14px 0", borderTop: "1px solid #E4E2DA", color: COLOR.ink }}>
              <span style={{ fontFamily: FONT.mono }}>{row.time}</span>
              <Chip tone={row.side === "вход" ? COLOR.blue : COLOR.muted}>{row.side}</Chip>
              <span>{row.bank} {row.partner ? <Chip tone={COLOR.ok}>участник</Chip> : null}</span>
              <span style={{ fontFamily: FONT.mono }}>{row.amount}</span>
              {row.partner ? <Button pressed={at(T.click - 2, T.click + 6)} style={{ justifySelf: "end" }}>Запросить выпуск</Button> : <span />}
            </div>
          ))}
          <div style={{ opacity: answer, transform: `translateY(${(1 - answer) * 16}px)`, padding: "16px 20px", borderRadius: 14, background: "#EEF3FA",
            borderLeft: `5px solid ${COLOR.blue}`, fontSize: 25, color: COLOR.ink, display: "flex", gap: 18, alignItems: "center" }}>
            <span>Банк 0213 ответил: выпуск L3, балл</span>
            <span style={{ fontFamily: FONT.mono, fontWeight: 600, color: COLOR.accent }}>0,87</span>
            <div style={{ width: 200, height: 14, borderRadius: 7, background: "#DCE3EE", overflow: "hidden" }}>
              <div style={{ width: `${87 * answer}%`, height: "100%", background: COLOR.accent }} />
            </div>
            <span style={{ color: COLOR.muted }}>квота на пару: {answer > 0.6 ? 48 : 49}</span>
          </div>
        </Panel>

        <Panel {...PEER} title="Банк 0213" aside="отвечает о своём счёте">
          <div style={{ fontSize: 24, color: COLOR.muted, lineHeight: 1.4 }}>Ссылка на зачисленный перевод найдена. Клиентские данные не передаются.</div>
          <div style={{ display: "flex", gap: 12, alignItems: "center", opacity: at(T.click, T.flyEnd) }}>
            <Lock size={34} color={COLOR.blue} />
            <span style={{ fontSize: 24, color: COLOR.blue }}>ответ зашифрован для банка 070</span>
          </div>
        </Panel>

        {short ? null : (
          <Panel {...JOURNAL} title="Журнал хаба · последние записи" aside="хэш-цепочка SHA-256, подпись Ed25519">
            {[
              { seq: "69 855", kind: "query", from: "070", to: "0213", hash: "7c4e91d0a2f3", fresh: true },
              { seq: "69 854", kind: "incident_soft", from: "012", to: "007", hash: "75775ac601ff" },
              { seq: "69 853", kind: "release", from: "019", to: "0214", hash: "62ce73c1a930" },
            ].map(e => (
              <div key={e.seq} style={{ display: "grid", gridTemplateColumns: "140px 240px 120px 120px 1fr", fontSize: 24, padding: "10px 0",
                borderTop: "1px solid #E4E2DA", color: COLOR.ink, opacity: e.fresh ? newRow : 1,
                transform: e.fresh ? `translateX(${(1 - newRow) * -40}px)` : undefined, background: e.fresh ? `rgba(168,74,20,${0.1 * newRow})` : undefined }}>
                <span style={{ fontFamily: FONT.mono }}>{e.seq}</span><span>{e.kind}</span><span>{e.from}</span><span>{e.to}</span>
                <span style={{ fontFamily: FONT.mono }}>{e.hash}</span>
              </div>
            ))}
            <div style={{ display: "flex", gap: 20, alignItems: "center" }}>
              <Button pressed={at(T.verify - 2, T.verify + 6)} style={{ marginLeft: "auto" }}>Проверить подпись</Button>
              <div style={{ opacity: ok, fontSize: 26, fontWeight: 600, color: COLOR.ok }}>✓ Подпись головы верна</div>
            </div>
          </Panel>
        )}

        {fly > 0 && fly < 1 ? (
          <div style={{ position: "absolute", left: envX - 70, top: envY - 44, width: 140, height: 88, borderRadius: 14, background: COLOR.navy,
            display: "flex", alignItems: "center", justifyContent: "center", boxShadow: "0 16px 30px rgba(20,33,61,.3)", zIndex: 8 }}>
            <Lock size={40} color={COLOR.amber} />
          </div>
        ) : null}
        <Ripple x={ASK_BUTTON.x} y={ASK_BUTTON.y} at={T.click} />
        <Ripple x={VERIFY_BUTTON.x} y={VERIFY_BUTTON.y} at={T.verify} />
        <Cursor x={curX} y={curY} down={down} />
      </div>

      <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: 200, background: `linear-gradient(rgba(246,244,238,0), ${COLOR.cream} 55%)` }} />
      <div style={{ position: "absolute", left: 120, bottom: 44, display: "flex", gap: 32, alignItems: "center" }}>
        <div style={{ padding: "12px 24px", borderRadius: 14, background: COLOR.accent, color: COLOR.paper, fontFamily: FONT.mono, fontSize: 34, fontWeight: 600 }}>
          shekara.21restobar.kz
        </div>
        <div style={{ fontSize: 30, color: COLOR.ink }}>30 банков · 442 тыс. переводов · журнал с подписью</div>
      </div>
    </AbsoluteFill>
    </Stage>
  );
};
