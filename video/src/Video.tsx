// Ролик целиком: сцены друг за другом, у каждой свой голос (если запись уже есть).
import { AbsoluteFill, Audio, Series, staticFile } from "remotion";
import { COLOR } from "./theme";
import { VERSIONS, type VersionId } from "./timeline";

export type VideoProps = {
  version: VersionId;
  frames: number[]; // длительность каждой сцены в кадрах (считает Root.calculateMetadata)
  voiced: boolean[]; // есть ли запись голоса для сцены
};

export const Video = ({ version, frames, voiced }: VideoProps) => (
  <AbsoluteFill style={{ background: COLOR.navy }}>
    <Series>
      {VERSIONS[version].map((scene, i) => (
        <Series.Sequence key={scene.id} durationInFrames={frames[i]} name={scene.id}>
          <scene.component short={scene.short} />
          {voiced[i] ? <Audio src={staticFile(`voice/${scene.id}.m4a`)} /> : null}
        </Series.Sequence>
      ))}
    </Series>
  </AbsoluteFill>
);
