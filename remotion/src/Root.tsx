import React from "react";
import { Composition } from "remotion";
import { LowerThird } from "./LowerThird";

// 6s lower-third overlay at 1920x1080/30fps. Title + accent come from --props.
export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="LowerThird"
      component={LowerThird}
      durationInFrames={180}
      fps={30}
      width={1920}
      height={1080}
      defaultProps={{
        title: "Lower Third Title",
        accent: "#7E22CE",
      }}
    />
  );
};
