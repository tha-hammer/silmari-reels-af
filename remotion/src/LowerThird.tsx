import React from "react";
import {
  AbsoluteFill,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { z } from "zod";
import { effectFields, enterProgress } from "./effectSchema";

// Runtime-validated prop surface. `defaultProps` (in Root.tsx) equal the old
// hardcoded literals, so an un-tuned render is pixel-identical.
export const lowerThirdSchema = z.object({
  title: z.string(),
  boxOpacity: z.number(),
  ...effectFields,
});

export type LowerThirdProps = z.infer<typeof lowerThirdSchema>;

// A tasteful animated lower-third: an accent tick, then a dark card with the
// title slides up + fades in from the lower-left, holds, then eases out.
export const LowerThird: React.FC<LowerThirdProps> = ({
  title,
  accent,
  fontScale,
  boxOpacity,
  accentBarPx,
  cornerRadius,
  anim,
  animDamping,
  animMass,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const enter = enterProgress(anim, frame, fps, animDamping, animMass);
  const flat = anim === "fade" || anim === "none";
  const outStart = durationInFrames - 18;
  const exit = interpolate(frame, [outStart, durationInFrames], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const enterY = flat ? 0 : interpolate(enter, [0, 1], [70, 0]);
  const y = enterY + interpolate(exit, [0, 1], [0, 40]);
  const opacity = interpolate(enter, [0, 1], [0, 1]) * (1 - exit);
  const tickW = interpolate(enter, [0, 1], [0, 96]);

  return (
    <AbsoluteFill style={{ backgroundColor: "transparent" }}>
      <div
        style={{
          position: "absolute",
          left: 96,
          bottom: 132,
          transform: `translateY(${y}px)`,
          opacity,
          display: "flex",
          flexDirection: "column",
          maxWidth: 1240,
        }}
      >
        <div
          style={{
            height: 10,
            width: tickW,
            background: accent,
            borderRadius: 5,
            marginBottom: 16,
          }}
        />
        <div
          style={{
            background: `rgba(17,17,21,${boxOpacity})`,
            borderLeft: `${accentBarPx}px solid ${accent}`,
            padding: "20px 34px",
            borderRadius: cornerRadius,
            boxShadow: "0 12px 40px rgba(0,0,0,0.45)",
          }}
        >
          <span
            style={{
              color: "#ffffff",
              fontSize: 50 * fontScale,
              lineHeight: 1.12,
              fontWeight: 800,
              fontFamily: "Arial, Helvetica, sans-serif",
              letterSpacing: 0.3,
            }}
          >
            {title}
          </span>
        </div>
      </div>
    </AbsoluteFill>
  );
};
