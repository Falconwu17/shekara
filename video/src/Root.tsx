// Две версии ролика: полная (2-3 минуты) и короткая (60 секунд) для начала защиты.
import { getAudioDurationInSeconds } from "@remotion/media-utils";
import { Composition, staticFile } from "remotion";
import { FPS, HEIGHT, WIDTH } from "./theme";
import { TAIL_SECONDS, VERSIONS, type VersionId } from "./timeline";
import { Video } from "./Video";

/** Длительность сцен по записям голоса; сцена без записи получает оценку из сценария. */
async function measure(version: VersionId) {
  const scenes = VERSIONS[version];
  const results = await Promise.all(scenes.map(async scene => {
    try {
      const seconds = await getAudioDurationInSeconds(staticFile(`voice/${scene.id}.m4a`));
      return { seconds: seconds + TAIL_SECONDS, voiced: true };
    } catch {
      return { seconds: scene.seconds, voiced: false };
    }
  }));
  const frames = results.map(r => Math.ceil(r.seconds * FPS));
  return {
    durationInFrames: frames.reduce((a, b) => a + b, 0),
    props: { version, frames, voiced: results.map(r => r.voiced) },
  };
}

const composition = (id: string, version: VersionId) => (
  <Composition
    id={id}
    component={Video}
    width={WIDTH}
    height={HEIGHT}
    fps={FPS}
    durationInFrames={1}
    defaultProps={{ version, frames: VERSIONS[version].map(s => s.seconds * FPS), voiced: VERSIONS[version].map(() => false) }}
    calculateMetadata={() => measure(version)}
  />
);

export const Root = () => (
  <>
    {composition("Full", "full")}
    {composition("Short", "short")}
  </>
);
