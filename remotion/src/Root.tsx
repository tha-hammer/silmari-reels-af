import React from "react";
import { Composition } from "remotion";
import { LowerThird } from "./LowerThird";
import { MiddleThird, Segment } from "./MiddleThird";

// 6s lower-third overlay at 1920x1080/30fps. Title + accent come from --props.
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
        defaultProps={{ title: "Lower Third Title", accent: "#7E22CE" }}
      />
      <Composition
        id="MiddleThird"
        component={MiddleThird}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{
          segments: [
            { text: "Script-synced overlay", from: 0, durationInFrames: 60 },
          ] as Segment[],
          accent: "#7E22CE",
          totalFrames: 0,
          verticalAnchor: 0.5,
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
