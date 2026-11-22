import React from "react";
import { Composition } from "remotion";
import { PackagingOverlay, type OverlayProps } from "./PackagingOverlay";
import rawProps from "./props.json";

const props = rawProps as unknown as OverlayProps;

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="PackagingOverlay"
      component={PackagingOverlay}
      durationInFrames={Math.max(1, Math.round(props.durationInFrames || 300))}
      fps={Math.max(1, Math.round(props.fps || 30))}
      width={Math.max(2, Math.round(props.width || 1080))}
      height={Math.max(2, Math.round(props.height || 1920))}
      defaultProps={props}
    />
  );
};
