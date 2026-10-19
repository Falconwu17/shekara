// Цвета и шрифты ролика: те же, что в презентации, чтобы ролик и дека выглядели одним целым.
import { loadFont as loadPlexMono } from "@remotion/google-fonts/IBMPlexMono";
import { loadFont as loadPlexSans } from "@remotion/google-fonts/IBMPlexSans";
import { loadFont as loadSerif } from "@remotion/google-fonts/SourceSerif4";

const serif = loadSerif("normal", { weights: ["600", "700"], subsets: ["cyrillic", "latin"] });
const sans = loadPlexSans("normal", { weights: ["400", "500", "600"], subsets: ["cyrillic", "latin"] });
const mono = loadPlexMono("normal", { weights: ["500", "600"], subsets: ["cyrillic", "latin"] });

export const FONT = {
  serif: serif.fontFamily,
  sans: sans.fontFamily,
  mono: mono.fontFamily,
};

export const COLOR = {
  navy: "#14213D",
  navyLine: "#3C4E73",
  cream: "#F6F4EE",
  paper: "#FFFFFF",
  ink: "#14213D",
  muted: "#5E6878",
  soft: "#BFC8D9",
  accent: "#A84A14",
  amber: "#F0A35E",
  risk: "#C2412D",
  ok: "#2F7D5B",
  blue: "#2B5C9E",
  grey: "#9AA5B8",
};

export const FPS = 60;
export const WIDTH = 1920;
export const HEIGHT = 1080;
