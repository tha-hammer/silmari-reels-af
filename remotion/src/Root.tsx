import React from "react";
import { Composition } from "remotion";
import { LowerThird, lowerThirdSchema } from "./LowerThird";
import { MiddleThird, Segment, middleThirdSchema } from "./MiddleThird";

// Effect-prop defaults equal to the components' previous hardcoded literals, so a
// render that omits a prop is pixel-identical to before. A tuned `--props` payload
// (built by the Python render modules) overrides individual props; the Zod schema
// validates the merged result at render time.
export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="LowerThird"
        component={LowerThird}
        durationInFrames={180}
        fps={30}
        width={1920}
        height={1080}
        schema={lowerThirdSchema}
        defaultProps={{
          title: "Lower Third Title",
          accent: "#7E22CE",
          fontScale: 1,
          boxOpacity: 0.88,
          accentBarPx: 8,
          cornerRadius: 12,
          anim: "spring" as const,
          animDamping: 200,
          animMass: 0.6,
        }}
      />
      <Composition
        id="MiddleThird"
        component={MiddleThird}
        fps={30}
        width={1080}
        height={1920}
        schema={middleThirdSchema}
        defaultProps={{
          segments: [
            { text: "Script-synced overlay", from: 0, durationInFrames: 60 },
          ] as Segment[],
          accent: "#7E22CE",
          totalFrames: 0,
          verticalAnchor: 0.5,
          fontScale: 1,
          cardOpacity: 0.84,
          accentBarPx: 9,
          cornerRadius: 22,
          anim: "spring" as const,
          animDamping: 200,
          animMass: 0.5,
          textTransform: "none" as const,
        }}
        calculateMetadata={({ props }) => {
          const segs = (props.segments ?? []) as Segment[];
          const end = segs.reduce(
            (m, s) => Math.max(m, s.from + s.durationInFrames),
            0,
          );
          const total = (props as { totalFrames?: number }).totalFrames ?? 0;
          return { durationInFrames: Math.max(total, end, 30) };
        }}
      />
    </>
  );
};
