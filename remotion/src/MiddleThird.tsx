import React from "react";
import {
  AbsoluteFill,
  Sequence,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export type Segment = { text: string; from: number; durationInFrames: number };
export type MiddleThirdProps = {
  segments: Segment[];
  accent: string;
  // Vertical placement of the card's centre as a fraction of frame height
  // (0 = top, 0.5 = centre, 1 = bottom). Lets the card be nudged into the gap
  // between stacked video regions (e.g. a talking-head cam over a screenshare)
  // per source, rather than hardcoding one position. Defaults to 0.5 (centre).
  verticalAnchor?: number;
};

const DEFAULT_VERTICAL_ANCHOR = 0.5;

// One script phrase as an animated card anchored in the frame: springs up
// + fades/scales in, holds, then eases out. Transparent elsewhere.
const Card: React.FC<{ text: string; accent: string; dur: number; verticalAnchor: number }> = ({
  text,
  accent,
  dur,
  verticalAnchor,
}) => {
  const frame = useCurrentFrame();
  const { fps, height } = useVideoConfig();

  const enter = spring({ frame, fps, config: { damping: 200, mass: 0.5 } });
  const outStart = Math.max(1, dur - 9);
  const exit = interpolate(frame, [outStart, dur], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const scale = interpolate(enter, [0, 1], [0.82, 1]) * interpolate(exit, [0, 1], [1, 0.94]);
  const opacity = interpolate(enter, [0, 1], [0, 1]) * (1 - exit);
  const y = interpolate(enter, [0, 1], [34, 0]) + interpolate(exit, [0, 1], [0, -18]);

  return (
    <AbsoluteFill>
      <div
        style={{
          position: "absolute",
          top: verticalAnchor * height,
          left: 0,
          right: 0,
          display: "flex",
          justifyContent: "center",
          transform: "translateY(-50%)",
        }}
      >
        <div
          style={{
            maxWidth: 900,
            margin: "0 90px",
            transform: `translateY(${y}px) scale(${scale})`,
            opacity,
            background: "rgba(16,16,20,0.84)",
            borderRadius: 22,
            padding: "30px 44px",
            borderBottom: `9px solid ${accent}`,
            boxShadow: "0 18px 55px rgba(0,0,0,0.5)",
          }}
        >
          <span
            style={{
              color: "#ffffff",
              fontSize: 66,
              fontWeight: 800,
              lineHeight: 1.16,
              letterSpacing: 0.3,
              textAlign: "center",
              display: "block",
              fontFamily: "Arial, Helvetica, sans-serif",
            }}
          >
            {text}
          </span>
        </div>
      </div>
    </AbsoluteFill>
  );
};

// Script-synced middle-third overlay: a sequence of phrase cards, each shown at
// its `from` frame for `durationInFrames`. Only one is visible at a time.
export const MiddleThird: React.FC<MiddleThirdProps> = ({
  segments,
  accent,
  verticalAnchor = DEFAULT_VERTICAL_ANCHOR,
}) => {
  return (
    <AbsoluteFill style={{ backgroundColor: "transparent" }}>
      {segments.map((s, i) => (
        <Sequence key={i} from={s.from} durationInFrames={s.durationInFrames}>
          <Card
            text={s.text}
            accent={accent}
            dur={s.durationInFrames}
            verticalAnchor={verticalAnchor}
          />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
