import React from 'react';
import {CalculateMetadataFunction, Composition, registerRoot} from 'remotion';
import {Caption, OverlayConfig, RecapOverlay, RecapOverlayProps} from './RecapOverlay';
import captions from './captions.json';
import overlay from './overlay.json';

const defaultProps: RecapOverlayProps = {
  captions: captions as Caption[],
  overlay: overlay as OverlayConfig,
};

// Canvas and length come from the props, so `remotion render --props=<run>.json` with a
// re-timed overlay/captions pair renders the new length without touching this file.
const calculateMetadata: CalculateMetadataFunction<RecapOverlayProps> = ({props}) => ({
  durationInFrames: props.overlay.durationInFrames,
  fps: props.overlay.fps,
  width: props.overlay.width,
  height: props.overlay.height,
});

const Root: React.FC = () => (
  <Composition
    id="RecapOverlay"
    component={RecapOverlay}
    defaultProps={defaultProps}
    calculateMetadata={calculateMetadata}
    durationInFrames={defaultProps.overlay.durationInFrames}
    fps={defaultProps.overlay.fps}
    width={defaultProps.overlay.width}
    height={defaultProps.overlay.height}
  />
);

registerRoot(Root);
