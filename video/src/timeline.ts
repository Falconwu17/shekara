// Порядок сцен в обеих версиях ролика. Длительность сцены = длина записи голоса public/voice/<id>.m4a
// плюс пауза; пока записи нет, берётся оценка `seconds` из сценария (video/script_ru.md).
import type { FC } from "react";
import { Chain } from "./scenes/Chain";
import { Finale } from "./scenes/Finale";
import { Hook } from "./scenes/Hook";
import { Idea } from "./scenes/Idea";
import { Caught } from "./scenes/Caught";
import { Product } from "./scenes/Product";
import { Results } from "./scenes/Results";
import { Unsolved } from "./scenes/Unsolved";

export type SceneProps = { short?: boolean };

export type Scene = {
  id: string; // имя файла голоса без расширения
  seconds: number; // оценка длительности без записи
  component: FC<SceneProps>;
  short?: boolean; // короткий вариант сцены (для 60-секундной версии)
};

const FULL: Scene[] = [
  { id: "01", seconds: 12, component: Hook },
  { id: "02", seconds: 18, component: Chain },
  { id: "03", seconds: 15, component: Unsolved },
  { id: "04", seconds: 25, component: Idea },
  { id: "05", seconds: 18, component: Caught },
  { id: "06", seconds: 25, component: Results },
  { id: "07", seconds: 20, component: Product },
  { id: "08", seconds: 15, component: Finale },
];

const SHORT: Scene[] = [
  { id: "01", seconds: 12, component: Hook },
  { id: "04", seconds: 25, component: Idea },
  { id: "06s", seconds: 10, component: Results, short: true },
  { id: "07s", seconds: 8, component: Product, short: true },
  { id: "08", seconds: 15, component: Finale },
];

export const VERSIONS = { full: FULL, short: SHORT };
export type VersionId = keyof typeof VERSIONS;

/** Пауза после реплики, чтобы анимация успела «дожить» до смены сцены. */
export const TAIL_SECONDS = 0.6;
