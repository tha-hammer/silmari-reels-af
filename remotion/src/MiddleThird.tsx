import React from "react";
import {
  AbsoluteFill,
  Sequence,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { z } from "zod";
import { AnimStyle, effectFields, enterProgress } from "./effectSchema";

export type Segment = { text: string; from: number; durationInFrames: number };

const segmentSchema = z.object({
  text: z.string(),
  from: z.number(),
  durationInFrames: z.number(),
});

// Runtime-validated prop surface. `defaultProps` (in Root.tsx) equal the old
// hardcoded literals, so an un-tuned render is pixel-identical; a tuned
// `--props` payload overrides individual effect props.
export const middleThirdSchema = z.object({
  segments: z.array(segmentSchema),
  totalFrames: z.number(),
  verticalAnchor: z.number(),
  cardOpacity: z.number(),
  textTransform: z.enum(["none", "uppercase"]),
  ...effectFields,
});

export type MiddleThirdProps = z.infer<typeof middleThirdSchema>;

type CardProps = {
  text: string;
  accent: string;
  dur: number;
  verticalAnchor: number;
  fontScale: number;
  cardOpacity: number;
  accentBarPx: number;
  cornerRadius: number;
  anim: AnimStyle;
  animDamping: number;
  animMass: number;
  textTransform: "none" | "uppercase";
};

// One script phrase as an animated card anchored in the frame: springs up
// + fades/scales in, holds, then eases out. Transparent elsewhere.
const Card: React.FC<CardProps> = ({
  text,
  accent,
  dur,
  verticalAnchor,
  fontScale,
  cardOpacity,
  accentBarPx,
  cornerRadius,
  anim,
  animDamping,
  animMass,
  textTransform,
}) => {
  const frame = useCurrentFrame();
  const { fps, height } = useVideoConfig();

  const enter = enterProgress(anim, frame, fps, animDamping, animMass);
  const flat = anim === "fade" || anim === "none";
  const outStart = Math.max(1, dur - 9);
  const exit = interpolate(frame, [outStart, dur], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const enterScale = flat ? 1 : interpolate(enter, [0, 1], [0.82, 1]);
  const scale = enterScale * interpolate(exit, [0, 1], [1, 0.94]);
  const opacity = interpolate(enter, [0, 1], [0, 1]) * (1 - exit);
  const enterY = flat ? 0 : interpolate(enter, [0, 1], [34, 0]);
  const y = enterY + interpolate(exit, [0, 1], [0, -18]);

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
            background: `rgba(16,16,20,${cardOpacity})`,
            borderRadius: cornerRadius,
            padding: "30px 44px",
            borderBottom: `${accentBarPx}px solid ${accent}`,
            boxShadow: "0 18px 55px rgba(0,0,0,0.5)",
          }}
        >
          <span
            style={{
              color: "#ffffff",
              fontSize: 66 * fontScale,
              fontWeight: 800,
              lineHeight: 1.16,
              letterSpacing: 0.3,
              textAlign: "center",
              display: "block",
              textTransform,
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
  verticalAnchor,
  fontScale,
  cardOpacity,
  accentBarPx,
  cornerRadius,
  anim,
  animDamping,
  animMass,
  textTransform,
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
            fontScale={fontScale}
            cardOpacity={cardOpacity}
            accentBarPx={accentBarPx}
            cornerRadius={cornerRadius}
            anim={anim}
            animDamping={animDamping}
            animMass={animMass}
            textTransform={textTransform}
          />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
