import React from 'react';
import {
  AbsoluteFill,
  Easing,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

export type Caption = {start: number; end: number; text: string};
export type TimeWindow = {start: number; end: number};
export type FlowerCue = {
  start: number;
  end: number;
  text: string;
  left: number;
  top: number;
  align?: 'left' | 'right';
};
// Everything that depends on the locked master's timing lives in overlay.json /
// captions.json (passed in as props), so a re-timed run changes data, not code.
export type OverlayConfig = {
  fps: number;
  width: number;
  height: number;
  durationInFrames: number;
  title: {text: string; windows: TimeWindow[]};
  flowerCues: FlowerCue[];
};
export type RecapOverlayProps = {
  captions: Caption[];
  overlay: OverlayConfig;
};

const captionJoinGap = 0.12;
const captionExitSeconds = 0.22;

const clamp = {
  extrapolateLeft: 'clamp' as const,
  extrapolateRight: 'clamp' as const,
};

const windowOpacity = (time: number, start: number, end: number) => {
  return Math.min(
    interpolate(time, [start, start + 0.32], [0, 1], clamp),
    interpolate(time, [end - 0.32, end], [1, 0], clamp),
  );
};

const TitleMark: React.FC<{time: number; title: OverlayConfig['title']}> = ({
  time,
  title,
}) => {
  const opacity = Math.max(
    0,
    ...title.windows.map((window) => windowOpacity(time, window.start, window.end)),
  );
  if (opacity <= 0) return null;

  const rise = interpolate(opacity, [0, 1], [10, 0], clamp);
  return (
    <div
      style={{
        position: 'absolute',
        left: 52,
        top: 28,
        width: 430,
        height: 82,
        opacity,
        transform: `translateX(${-rise}px)`,
        pointerEvents: 'none',
        display: 'flex',
        alignItems: 'center',
      }}
    >
      <div
        style={{
          fontFamily: 'Xingkai SC, Kaiti SC, STKaiti, serif',
          fontSize: 52,
          fontWeight: 400,
          lineHeight: 1,
          letterSpacing: 5,
          color: '#e9e0d3',
          textShadow: '0 3px 9px rgba(0,0,0,.84), 0 1px 2px rgba(0,0,0,.96)',
          whiteSpace: 'nowrap',
        }}
      >
        {title.text}
      </div>
    </div>
  );
};

const FlowerText: React.FC<{cue: FlowerCue; frame: number; fps: number}> = ({
  cue,
  frame,
  fps,
}) => {
  const startFrame = Math.floor(cue.start * fps);
  const localFrame = Math.max(0, frame - startFrame);
  const duration = cue.end - cue.start;
  const time = frame / fps;
  const opacity = windowOpacity(time, cue.start, cue.end);
  const settle = spring({
    frame: localFrame,
    fps,
    durationInFrames: 16,
    config: {damping: 24, stiffness: 170, mass: 0.72},
  });
  const scale = interpolate(settle, [0, 1], [0.96, 1], clamp);
  const translateX = interpolate(settle, [0, 1], [16, 0], clamp);
  const shimmer = interpolate(
    Math.min(duration, Math.max(0, time - cue.start)),
    [0, Math.min(0.8, duration)],
    [0.82, 1],
    clamp,
  );

  return (
    <div
      style={{
        position: 'absolute',
        left: cue.left,
        top: cue.top,
        opacity,
        transform: `translateX(${translateX}px) scale(${scale}) rotate(-1deg)`,
        transformOrigin: cue.align === 'right' ? '100% 50%' : '0 50%',
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          whiteSpace: 'pre-line',
          fontFamily: 'Xingkai SC, Kaiti SC, STKaiti, serif',
          fontSize: 54,
          fontWeight: 600,
          lineHeight: 1.08,
          letterSpacing: 2.5,
          color: '#d9bc76',
          WebkitTextStroke: '0.4px rgba(76,43,23,.72)',
          paintOrder: 'stroke fill',
          textShadow: '0 1px 0 rgba(102,57,27,.72), 0 4px 9px rgba(0,0,0,.5)',
          filter: `brightness(${shimmer})`,
        }}
      >
        {cue.text}
      </div>
      <div
        style={{
          width: 64,
          height: 1,
          marginTop: 10,
          marginLeft: 3,
          background:
            'linear-gradient(to right, rgba(151,51,40,.52), rgba(197,102,64,.28), rgba(197,102,64,0))',
        }}
      />
    </div>
  );
};

const FlameDoodle: React.FC = () => (
  <svg width="41" height="42" viewBox="0 0 49 50" fill="none">
    <path
      d="M29.4 3.8c1.1 6.7-2 9.4-5 12.2-2.1-4.2-5.3-6.7-5.3-6.7.2 6-10.3 10.7-10.3 21.1 0 8.7 7 15 15.9 15 8.8 0 15.8-6.1 15.8-15 0-9.5-5.2-17.5-11.1-26.6Z"
      fill="#D06B5B"
      stroke="#2A2622"
      strokeWidth="1.8"
      strokeLinejoin="round"
    />
    <path
      d="M26.9 20.3c.4 4.3-2.8 6.2-5.1 8.8-1.5-1.9-2-4.2-2-4.2-2.7 3-3.2 5.5-3.2 8.1 0 4.4 3.4 7.7 8.1 7.7 4.6 0 8.1-3.2 8.1-7.7 0-4.9-2.6-8.9-5.9-12.7Z"
      fill="#72A6A3"
      stroke="#2A2622"
      strokeWidth="1.6"
      strokeLinejoin="round"
    />
    <path d="M16.2 42.4 13.5 47M32.5 42.4l2.9 4.3" stroke="#2A2622" strokeWidth="1.8" strokeLinecap="round" />
    <path d="m4.9 17.4 4.4 1.5M42 15l3.9-2.2M39.7 8.3l1.6-4.1" stroke="#D06B5B" strokeWidth="1.6" strokeLinecap="round" />
  </svg>
);

const SubtitleStrip: React.FC<{
  cue: Caption;
  chainStart: number;
  chainEnd: number;
  frame: number;
  fps: number;
}> = ({cue, chainStart, chainEnd, frame, fps}) => {
  const chainLocalFrame = frame - Math.floor(chainStart * fps);
  const appear = spring({
    frame: Math.max(0, chainLocalFrame),
    fps,
    durationInFrames: 13,
    config: {damping: 29, stiffness: 165, mass: 0.68},
  });
  const time = frame / fps;
  const opacity = Math.min(
    interpolate(time, [chainStart, chainStart + 0.1], [0, 1], {
      ...clamp,
      easing: Easing.out(Easing.cubic),
    }),
    interpolate(time, [chainEnd, chainEnd + captionExitSeconds], [1, 0], {
      ...clamp,
      easing: Easing.in(Easing.cubic),
    }),
  );
  const translateY = interpolate(appear, [0, 1], [7, 0], clamp);
  const textUnits = Array.from(cue.text).length;
  const width = 1390;

  return (
    <div
      style={{
        position: 'absolute',
        left: '50%',
        bottom: 28,
        width,
        height: 94,
        transform: `translateX(-50%) translateY(${translateY}px)`,
        opacity,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          position: 'absolute',
          inset: 0,
          background: '#f2ede7',
          border: '1px solid rgba(104,82,62,.14)',
          borderRadius: 4,
          boxShadow: '0 4px 12px rgba(0,0,0,.22)',
        }}
      />
      <div
        style={{
          position: 'relative',
          maxWidth: width - 168,
          paddingLeft: 66,
          paddingRight: 102,
          color: '#181614',
          fontFamily: 'Hannotate SC, HanziPen SC, Kaiti SC, sans-serif',
          fontWeight: 400,
          fontSize: textUnits >= 16 ? 46 : 51,
          lineHeight: 1,
          letterSpacing: 0.15,
          textAlign: 'center',
          whiteSpace: 'nowrap',
          transform: 'translateY(-1px)',
        }}
      >
        {cue.text}
      </div>
      <div
        style={{
          position: 'absolute',
          right: 34,
          top: 26,
          width: 41,
          height: 42,
          opacity: 0.76,
          transform: 'rotate(2deg)',
        }}
      >
        <FlameDoodle />
      </div>
    </div>
  );
};

export const RecapOverlay: React.FC<RecapOverlayProps> = ({captions: cues, overlay}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const time = frame / fps;
  let cueIndex = cues.findIndex((item) => time >= item.start && time < item.end);
  if (cueIndex < 0) {
    for (let index = cues.length - 1; index >= 0; index--) {
      const item = cues[index];
      const next = cues[index + 1];
      const closesChain = !next || next.start - item.end > captionJoinGap;
      const bridgesChain =
        next &&
        next.start - item.end <= captionJoinGap &&
        time >= item.end &&
        time < next.start;
      if (
        bridgesChain ||
        (closesChain &&
          time >= item.end &&
          time < item.end + captionExitSeconds)
      ) {
        cueIndex = index;
        break;
      }
    }
  }
  const cue = cueIndex >= 0 ? cues[cueIndex] : null;
  let chainStart = cue?.start ?? 0;
  let chainEnd = cue?.end ?? 0;
  if (cue) {
    let index = cueIndex;
    while (index > 0 && cues[index].start - cues[index - 1].end <= captionJoinGap) {
      index--;
    }
    chainStart = cues[index].start;
    index = cueIndex;
    while (
      index < cues.length - 1 &&
      cues[index + 1].start - cues[index].end <= captionJoinGap
    ) {
      index++;
    }
    chainEnd = cues[index].end;
  }
  const flower = overlay.flowerCues.find((item) => time >= item.start && time < item.end);

  return (
    <AbsoluteFill style={{backgroundColor: 'transparent', pointerEvents: 'none'}}>
      <TitleMark time={time} title={overlay.title} />
      {flower ? <FlowerText cue={flower} frame={frame} fps={fps} /> : null}
      {cue ? (
        <SubtitleStrip
          cue={cue}
          chainStart={chainStart}
          chainEnd={chainEnd}
          frame={frame}
          fps={fps}
        />
      ) : null}
    </AbsoluteFill>
  );
};
