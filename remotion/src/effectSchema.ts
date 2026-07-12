import { z } from "zod";
import { zColor } from "@remotion/zod-types";

// The animation style shared by both overlays. Kept in one place so the Python
// `anim_style` enum and both compositions stay in lockstep.
export const animEnum = z.enum(["spring", "fade", "slide", "none"]);

// Effect props shared by MiddleThird + LowerThird. Each composition supplies its
// own `defaultProps` equal to the previous hardcoded literal, so an un-tuned
// render (props omitted → defaultProps fill in) is pixel-identical to before.
export const effectFields = {
  accent: zColor(),
  fontScale: z.number(),
  accentBarPx: z.number(),
  cornerRadius: z.number(),
  anim: animEnum,
  animDamping: z.number(),
  animMass: z.number(),
};

export type AnimStyle = z.infer<typeof animEnum>;

// A 0→1 "enter" progress for a given animation style. `spring` reproduces the
// original spring exactly (default path); the others are alternative reveals.
import { spring, interpolate } from "remotion";

export function enterProgress(
  anim: AnimStyle,
  frame: number,
  fps: number,
  damping: number,
  mass: number,
): number {
  if (anim === "none") return 1;
  if (anim === "fade") {
    return interpolate(frame, [0, Math.round(fps * 0.4)], [0, 1], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    });
  }
  // spring + slide both ride the spring curve
  return spring({ frame, fps, config: { damping, mass } });
}
