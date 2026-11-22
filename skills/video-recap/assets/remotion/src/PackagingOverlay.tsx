import React from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";

export interface RectNorm {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface PaletteTokens {
  bg?: string;
  border?: string;
  title?: string;
  accent?: string;
  muted?: string;
  coral?: string;
}

export interface CardItem {
  title: string;
  subtitle?: string;
  kicker?: string;
  kind?: "chapter" | "quote" | "scene" | "huazi" | "reaction";
  side?: "left" | "right";
  start: number;
  end: number;
  rect?: RectNorm;
}

export interface CalloutItem {
  text: string;
  kicker?: string;
  slot?: string;
  start: number;
  end: number;
  rect?: RectNorm;
}

export interface ClipSpan {
  id: string;
  start: number;
  end: number;
}

export type HeaderStyleKind = "drama_minimal" | "editorial_cinema" | "frosted";

export interface OverlayProps {
  width: number;
  height: number;
  fps: number;
  durationInFrames: number;
  themeName?: string;
  theme?: { remotion?: { scrim?: boolean } };
  headerStyle?: HeaderStyleKind;
  scrim?: boolean;
  viewport?: RectNorm;
  safeY?: [number, number];
  palette?: PaletteTokens;
  slots?: Record<string, RectNorm>;
  header?: { kicker?: string; title?: string; subhead?: string };
  cards?: CardItem[];
  callouts?: CalloutItem[];
  clips?: ClipSpan[];
  showProgress?: boolean;
}

const isEditorialCinema = (style?: HeaderStyleKind): boolean =>
  style === "drama_minimal" || style === "editorial_cinema";

const FONT_DISPLAY =
  '"PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
const FONT_CONDENSED =
  '"DIN Condensed", "Bebas Neue", "Anton", "Avenir Next Condensed", "Arial Narrow", "PingFang SC", sans-serif';
const FONT_SERIF = '"Songti SC", "Noto Serif CJK SC", "STSong", Georgia, serif';

const toCss = (hex: string | undefined, fallback: string): string => {
  if (!hex) return fallback;
  const clean = hex.trim().replace(/^#/, "");
  if (clean.length === 8) {
    const r = parseInt(clean.slice(0, 2), 16);
    const g = parseInt(clean.slice(2, 4), 16);
    const b = parseInt(clean.slice(4, 6), 16);
    const a = +(parseInt(clean.slice(6, 8), 16) / 255).toFixed(3);
    return `rgba(${r}, ${g}, ${b}, ${a})`;
  }
  return hex;
};

const clampAboveSafeY = (rect: RectNorm, safeY?: [number, number], gap = 0.014): RectNorm => {
  if (!safeY || safeY.length !== 2) return rect;
  const ceiling = Math.max(0.08, safeY[0] - gap);
  if (rect.y + rect.h <= ceiling) return rect;
  return { ...rect, y: Math.max(0.02, ceiling - rect.h) };
};

const rectStyle = (rect: RectNorm, cw: number, ch: number): React.CSSProperties => ({
  position: "absolute",
  left: Math.round(rect.x * cw),
  top: Math.round(rect.y * ch),
  width: Math.round(rect.w * cw),
  height: Math.round(rect.h * ch),
});

const frostedSurface = (bg: string, border: string): React.CSSProperties => ({
  background: `linear-gradient(145deg, rgba(255,255,255,0.10) 0%, rgba(255,255,255,0.02) 38%, rgba(0,0,0,0.16) 100%), ${bg}`,
  border: `1px solid ${border}`,
  boxShadow: "0 16px 38px rgba(0, 0, 0, 0.42), inset 0 1px 0 rgba(255, 255, 255, 0.22)",
  backdropFilter: "blur(16px) saturate(1.15)",
});

const HeaderBlock: React.FC<{
  header: { kicker?: string; title?: string; subhead?: string };
  headerStyle: HeaderStyleKind;
  rect: RectNorm;
  palette: PaletteTokens;
}> = ({ header, headerStyle, rect, palette }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const titleLines = (header.title || "").split("\n").map((s) => s.trim()).filter(Boolean);
  if (!header.kicker && titleLines.length === 0 && !header.subhead) return null;

  const enter = spring({ frame, fps, config: { damping: 20, stiffness: 110, mass: 0.9 } });
  const ruleScale = spring({ frame: Math.max(0, frame - 4), fps, config: { damping: 20, stiffness: 90 } });
  const subFade = interpolate(frame, [6, 18], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  const titleCol = toCss(palette.title, "#FAF6EE");
  const accent = toCss(palette.accent, "#F5C76B");
  const muted = toCss(palette.muted, "rgba(250, 246, 238, 0.92)");
  const isPortrait = height >= width;

  if (isEditorialCinema(headerStyle)) {
    const pureWhite = toCss(palette.title, "#FFFFFF");
    const accentBar = toCss(palette.accent, "#F3CE7A");
    const taglineCol = toCss(palette.muted, "rgba(255, 255, 255, 0.88)");
    const titleSize = Math.round((isPortrait ? width : height) * (titleLines.length > 1 ? 0.034 : 0.037));
    const tagline = [header.kicker, header.subhead].filter(Boolean).join(" · ");

    const titleReveal = spring({ frame, fps, config: { damping: 22, stiffness: 150, mass: 0.85 } });
    const barDraw = spring({ frame: Math.max(0, frame - 2), fps, config: { damping: 22, stiffness: 140, mass: 0.8 } });
    const tagReveal = spring({ frame: Math.max(0, frame - 4), fps, config: { damping: 22, stiffness: 145, mass: 0.85 } });
    const titleClip = titleReveal >= 0.992 ? undefined : `inset(0 0 ${((1 - titleReveal) * 100).toFixed(1)}% 0)`;
    const tagClip = tagReveal >= 0.992 ? undefined : `inset(0 0 ${((1 - tagReveal) * 100).toFixed(1)}% 0)`;
    const barWidth = Math.round(28 * Math.min(1, barDraw));

    return (
      <div
        style={{
          ...rectStyle(rect, width, height),
          display: "flex",
          flexDirection: "column",
          alignItems: "flex-start",
          justifyContent: "flex-start",
          fontFamily: FONT_DISPLAY,
        }}
      >
        {titleLines.length > 0 ? (
          <div style={{ clipPath: titleClip }}>
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                opacity: Math.min(1, titleReveal * 1.15),
                transform: `translateY(${interpolate(titleReveal, [0, 1], [14, 0]).toFixed(2)}px)`,
              }}
            >
              {titleLines.map((line, idx) => (
                <div
                  key={idx}
                  style={{
                    color: pureWhite,
                    fontSize: titleSize,
                    fontWeight: 700,
                    lineHeight: 1.16,
                    letterSpacing: "0.06em",
                    textShadow: "0 4px 18px rgba(0, 0, 0, 0.55)",
                  }}
                >
                  {line}
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {titleLines.length > 0 ? (
          <div
            style={{
              width: barWidth,
              height: 2.5,
              borderRadius: 2,
              background: accentBar,
              margin: "10px 0 10px 0",
              opacity: Math.min(1, barDraw * 1.2),
              boxShadow: "0 2px 10px rgba(0, 0, 0, 0.45)",
            }}
          />
        ) : null}

        {tagline ? (
          <div style={{ clipPath: tagClip }}>
            <div
              style={{
                color: taglineCol,
                fontSize: Math.max(22, Math.round(titleSize * 0.60)),
                fontWeight: 500,
                lineHeight: 1.32,
                letterSpacing: "0.02em",
                opacity: Math.min(1, tagReveal * 1.15),
                transform: `translateY(${interpolate(tagReveal, [0, 1], [12, 0]).toFixed(2)}px)`,
                textShadow: "0 2px 12px rgba(0, 0, 0, 0.68)",
              }}
            >
              {tagline}
            </div>
          </div>
        ) : null}
      </div>
    );
  }

  const bg = toCss(palette.bg, "rgba(12, 16, 26, 0.72)");
  const border = toCss(palette.border, "rgba(246, 214, 132, 0.45)");
  const titleSize = Math.round((isPortrait ? width : height) * (titleLines.length > 1 ? 0.044 : 0.052));
  const kickerSize = Math.max(18, Math.round(titleSize * 0.44));

  return (
    <div
      style={{
        ...rectStyle(rect, width, height),
        ...frostedSurface(bg, border),
        display: "flex",
        flexDirection: "column",
        alignItems: isPortrait ? "center" : "flex-start",
        justifyContent: "center",
        padding: `${Math.round(height * 0.011)}px ${Math.round(width * 0.032)}px`,
        borderRadius: 18,
        opacity: enter,
        transform: `translateY(${interpolate(enter, [0, 1], [-16, 0])}px)`,
        fontFamily: FONT_DISPLAY,
        textAlign: isPortrait ? "center" : "left",
      }}
    >
      {header.kicker ? (
        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            padding: "3px 14px",
            marginBottom: 7,
            borderRadius: 999,
            border: `1px solid ${accent}99`,
            background: "linear-gradient(90deg, rgba(246,214,132,0.16), rgba(246,214,132,0.06))",
            color: accent,
            fontFamily: FONT_CONDENSED,
            fontSize: kickerSize,
            fontWeight: 700,
            letterSpacing: "0.11em",
            textTransform: "uppercase",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {header.kicker}
        </div>
      ) : null}
      {titleLines.map((line, idx) => (
        <div
          key={idx}
          style={{
            color: titleCol,
            fontSize: titleSize,
            fontWeight: 800,
            lineHeight: 1.16,
            letterSpacing: "0.02em",
            textShadow: "0 2px 6px rgba(0, 0, 0, 0.65), 0 8px 24px rgba(0, 0, 0, 0.45)",
          }}
        >
          {line}
        </div>
      ))}
      <div
        style={{
          width: isPortrait ? "44%" : "58%",
          height: 2.5,
          marginTop: 9,
          borderRadius: 2,
          background: `linear-gradient(90deg, transparent, ${accent}, transparent)`,
          transform: `scaleX(${ruleScale})`,
          opacity: 0.92,
        }}
      />
      {header.subhead ? (
        <div
          style={{
            marginTop: 6,
            color: muted,
            fontSize: Math.max(17, Math.round(titleSize * 0.42)),
            fontWeight: 500,
            letterSpacing: "0.05em",
            opacity: subFade,
          }}
        >
          {header.subhead}
        </div>
      ) : null}
    </div>
  );
};

const HuaziBlock: React.FC<{
  card: CardItem;
  rect: RectNorm;
  palette: PaletteTokens;
}> = ({ card, rect, palette }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const startF = Math.round(card.start * fps);
  const endF = Math.round(card.end * fps);
  if (frame < startF || frame > endF || endF <= startF) return null;

  const local = frame - startF;
  const rem = endF - frame;

  const parts = card.title.trim().split(/\s+/).filter(Boolean);
  const hasTwoTiers = parts.length >= 2;
  const setupLine = hasTwoTiers ? parts.slice(0, -1).join(" ") : "";
  const punchLine = hasTwoTiers ? parts[parts.length - 1] : card.title.trim();

  // Clean smooth opacity + tiny downward drift exit over the last 10 frames
  const exitOpacity = interpolate(rem, [0, 10], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const exitDrop = interpolate(rem, [0, 10], [8, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  // Tier 1 (setup): clean upward clip-mask reveal
  const beat1 = spring({ frame: local, fps, config: { damping: 22, stiffness: 165, mass: 0.85 } });
  const b1Clip = beat1 >= 0.992 ? undefined : `inset(0 0 ${((1 - beat1) * 100).toFixed(1)}% 0)`;

  // Tier 2 (punchline): 5 frames later for 2-tier, sculptural rise + subtle tracking settle (0.10em -> 0.04em)
  const b2Delay = hasTwoTiers ? 5 : 0;
  const beat2 = spring({ frame: Math.max(0, local - b2Delay), fps, config: { damping: 22, stiffness: 170, mass: 0.85 } });
  const b2Clip = beat2 >= 0.992 ? undefined : `inset(0 0 ${((1 - beat2) * 100).toFixed(1)}% 0)`;
  const punchTracking = interpolate(beat2, [0, 1], [0.10, 0.04], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  const baseUnit = Math.min(width, height);
  const setupSize = Math.round(baseUnit * 0.0241); // 26px on 1080w
  const punchSize = Math.round(baseUnit * 0.0518); // 56px on 1080w
  const muted = toCss(palette.muted, "rgba(255, 255, 255, 0.88)");

  return (
    <div
      style={{
        ...rectStyle(rect, width, height),
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-start",
        justifyContent: "flex-start",
        fontFamily: FONT_DISPLAY,
        opacity: exitOpacity,
        transform: `translateY(${exitDrop.toFixed(2)}px)`,
      }}
    >
      {hasTwoTiers ? (
        <div style={{ clipPath: b1Clip, marginBottom: 4 }}>
          <div
            style={{
              color: "rgba(255, 255, 255, 0.90)",
              fontSize: setupSize,
              fontWeight: 500,
              lineHeight: 1.25,
              letterSpacing: "0.18em",
              opacity: Math.min(1, beat1 * 1.15),
              transform: `translateY(${interpolate(beat1, [0, 1], [14, 0]).toFixed(2)}px)`,
              textShadow: "0 2px 12px rgba(0, 0, 0, 0.72)",
              whiteSpace: "nowrap",
            }}
          >
            {setupLine}
          </div>
        </div>
      ) : null}

      <div style={{ clipPath: b2Clip }}>
        <div
          style={{
            opacity: Math.min(1, beat2 * 1.15),
            transform: `translateY(${interpolate(beat2, [0, 1], [18, 0]).toFixed(2)}px)`,
            filter: "drop-shadow(0 6px 20px rgba(0, 0, 0, 0.68))",
          }}
        >
          <span
            style={{
              display: "inline-block",
              fontSize: punchSize,
              fontWeight: 800,
              lineHeight: 1.18,
              letterSpacing: `${punchTracking.toFixed(3)}em`,
              backgroundImage: "linear-gradient(180deg, #FFFDF8 15%, #F5D27B 100%)",
              WebkitBackgroundClip: "text",
              backgroundClip: "text",
              WebkitTextFillColor: "transparent",
              color: "transparent",
              paddingRight: 4,
              whiteSpace: "nowrap",
            }}
          >
            {punchLine}
          </span>
        </div>
      </div>

      {card.subtitle ? (
        <div
          style={{
            marginTop: 6,
            color: muted,
            fontSize: Math.max(20, Math.round(setupSize * 0.85)),
            fontWeight: 500,
            letterSpacing: "0.04em",
            opacity: Math.min(1, beat2),
            textShadow: "0 2px 10px rgba(0, 0, 0, 0.75)",
          }}
        >
          {card.subtitle}
        </div>
      ) : null}
    </div>
  );
};

const ReactionBlock: React.FC<{
  card: CardItem;
  rect: RectNorm;
  palette: PaletteTokens;
}> = ({ card, rect, palette }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const startF = Math.round(card.start * fps);
  const endF = Math.round(card.end * fps);
  if (frame < startF || frame > endF || endF <= startF) return null;

  const local = frame - startF;
  const rem = endF - frame;
  const pop = spring({ frame: local, fps, config: { damping: 11, stiffness: 220, mass: 0.55 } });
  const exit = interpolate(rem, [0, 6], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const punchCol = toCss(palette.coral, "#FFD84D");
  const fontSize = Math.round(Math.min(width, height) * 0.058);
  const outlineShadow =
    "0 4px 16px rgba(0,0,0,0.92), -2px -2px 0 #141210, 2px -2px 0 #141210, -2px 2px 0 #141210, 2px 2px 0 #141210";

  return (
    <div
      style={{
        ...rectStyle(rect, width, height),
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        opacity: Math.min(1, pop * 1.2) * exit,
        transform: `scale(${interpolate(pop, [0, 1], [0.76, 1]).toFixed(4)}) rotate(${interpolate(pop, [0, 1], [-2.5, 0]).toFixed(2)}deg)`,
        fontFamily: FONT_DISPLAY,
        textAlign: "center",
      }}
    >
      <div
        style={{
          color: punchCol,
          fontSize,
          fontWeight: 900,
          lineHeight: 1.15,
          letterSpacing: "0.03em",
          textShadow: outlineShadow,
        }}
      >
        {card.title}
      </div>
      {card.subtitle ? (
        <div
          style={{
            marginTop: 4,
            color: "#FFFFFF",
            fontSize: Math.max(24, Math.round(fontSize * 0.62)),
            fontWeight: 800,
            textShadow: outlineShadow,
          }}
        >
          {card.subtitle}
        </div>
      ) : null}
    </div>
  );
};

const CardBlock: React.FC<{
  card: CardItem;
  rect: RectNorm;
  palette: PaletteTokens;
}> = ({ card, rect, palette }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const startF = Math.round(card.start * fps);
  const endF = Math.round(card.end * fps);
  if (frame < startF || frame > endF || endF <= startF) return null;

  const span = Math.max(1, endF - startF);
  const local = frame - startF;
  const rem = endF - frame;
  const enter = spring({ frame: local, fps, config: { damping: 16, stiffness: 150, mass: 0.72 } });
  const exit = interpolate(rem, [0, 8], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const push = interpolate(local, [0, span], [1.0, 1.04], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const dir = card.side === "right" ? 1 : -1;
  const shiftX = interpolate(enter, [0, 1], [34 * dir, 0]);

  const bg = toCss(palette.bg, "rgba(15, 20, 32, 0.78)");
  const border = toCss(palette.border, "rgba(255, 255, 255, 0.22)");
  const accent = toCss(palette.accent, "#F6D684");
  const coral = toCss(palette.coral, accent);
  const titleCol = toCss(palette.title, "#FFF9EE");
  const muted = toCss(palette.muted, "#CFD6E2");
  const base = Math.round(Math.min(width, height) * 0.031);
  const kind = card.kind || "chapter";

  return (
    <div
      style={{
        ...rectStyle(rect, width, height),
        ...frostedSurface(bg, border),
        display: "flex",
        alignItems: "center",
        gap: 14,
        padding: "12px 20px",
        borderRadius: 15,
        opacity: enter * exit,
        transform: `translateX(${shiftX}px) scale(${push.toFixed(4)})`,
        transformOrigin: card.side === "right" ? "right center" : "left center",
        fontFamily: FONT_DISPLAY,
      }}
    >
      {kind === "quote" ? (
        <div
          style={{
            fontFamily: FONT_SERIF,
            fontSize: Math.round(base * 1.9),
            lineHeight: 0.8,
            color: accent,
            opacity: 0.9,
            flexShrink: 0,
            alignSelf: "flex-start",
            marginTop: 2,
          }}
        >
          “
        </div>
      ) : (
        <div
          style={{
            width: 5,
            height: "74%",
            borderRadius: 4,
            background: `linear-gradient(180deg, ${accent}, ${coral})`,
            boxShadow: `0 0 12px ${accent}55`,
            flexShrink: 0,
          }}
        />
      )}
      <div style={{ minWidth: 0, flex: 1 }}>
        {card.kicker ? (
          <div
            style={{
              fontFamily: FONT_CONDENSED,
              color: accent,
              fontSize: Math.max(15, Math.round(base * 0.62)),
              fontWeight: 700,
              letterSpacing: "0.10em",
              textTransform: "uppercase",
              fontVariantNumeric: "tabular-nums",
              marginBottom: 2,
            }}
          >
            {card.kicker}
          </div>
        ) : null}
        <div
          style={{
            color: titleCol,
            fontFamily: kind === "scene" ? FONT_CONDENSED : kind === "quote" ? FONT_SERIF : FONT_DISPLAY,
            fontSize: kind === "scene" ? Math.round(base * 1.15) : base,
            fontWeight: kind === "quote" ? 700 : 800,
            lineHeight: 1.2,
            letterSpacing: kind === "scene" ? "0.06em" : "0.015em",
            fontVariantNumeric: "tabular-nums",
            textShadow: "0 2px 8px rgba(0,0,0,0.5)",
          }}
        >
          {card.title}
        </div>
        {card.subtitle ? (
          <div
            style={{
              color: muted,
              fontSize: Math.max(15, Math.round(base * 0.66)),
              marginTop: 3,
              fontWeight: 500,
              textAlign: kind === "quote" ? "right" : "left",
            }}
          >
            {kind === "quote" ? `— ${card.subtitle}` : card.subtitle}
          </div>
        ) : null}
      </div>
    </div>
  );
};

const CalloutBlock: React.FC<{
  item: CalloutItem;
  rect: RectNorm;
  palette: PaletteTokens;
}> = ({ item, rect, palette }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const startF = Math.round(item.start * fps);
  const endF = Math.round(item.end * fps);
  if (frame < startF || frame > endF || endF <= startF) return null;

  const span = Math.max(1, endF - startF);
  const local = frame - startF;
  const rem = endF - frame;
  const pop = spring({ frame: local, fps, config: { damping: 14, stiffness: 180, mass: 0.65 } });
  const exit = interpolate(rem, [0, 7], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const push = interpolate(local, [0, span], [1.0, 1.03], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  const bg = toCss(palette.bg, "rgba(12, 16, 26, 0.78)");
  const accent = toCss(palette.accent, "#F6D684");
  const titleCol = toCss(palette.title, "#FFF9EE");
  const fontSize = Math.round(Math.min(width, height) * 0.033);

  return (
    <div
      style={{
        ...rectStyle(rect, width, height),
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        opacity: pop * exit,
        transform: `scale(${(interpolate(pop, [0, 1], [0.9, 1]) * push).toFixed(4)}) translateY(${interpolate(pop, [0, 1], [12, 0])}px)`,
        fontFamily: FONT_DISPLAY,
      }}
    >
      <div
        style={{
          ...frostedSurface(bg, `${accent}B8`),
          display: "inline-flex",
          alignItems: "center",
          gap: 10,
          padding: "9px 24px",
          borderRadius: 999,
          color: titleCol,
          fontSize,
          fontWeight: 800,
          letterSpacing: "0.04em",
          textShadow: "0 2px 10px rgba(0, 0, 0, 0.55)",
        }}
      >
        {item.kicker ? (
          <span
            style={{
              fontFamily: FONT_CONDENSED,
              color: accent,
              fontSize: Math.max(16, Math.round(fontSize * 0.78)),
              letterSpacing: "0.09em",
              textTransform: "uppercase",
              fontVariantNumeric: "tabular-nums",
              paddingRight: 10,
              borderRight: `1px solid ${accent}66`,
            }}
          >
            {item.kicker}
          </span>
        ) : null}
        <span>{item.text}</span>
      </div>
    </div>
  );
};

const ProgressBar: React.FC<{
  viewport: RectNorm;
  safeY?: [number, number];
  clips: ClipSpan[];
  palette: PaletteTokens;
}> = ({ viewport, safeY, clips, palette }) => {
  const frame = useCurrentFrame();
  const { durationInFrames, width, height } = useVideoConfig();
  const pct = Math.min(100, Math.max(0, (frame / Math.max(1, durationInFrames - 1)) * 100));
  const totalS = clips.length ? clips[clips.length - 1].end : 0;
  const accent = toCss(palette.accent, "#F6D684");
  const maxY = safeY && safeY.length === 2 ? Math.round((safeY[0] - 0.01) * height) : height - 6;
  const y = Math.min(maxY, Math.round((viewport.y + viewport.h) * height) - 3);
  const x = Math.round(viewport.x * width);
  const w = Math.round(viewport.w * width);

  return (
    <div style={{ position: "absolute", left: x, top: y, width: w, height: 3, background: "rgba(255,255,255,0.16)" }}>
      <div
        style={{
          width: `${pct.toFixed(2)}%`,
          height: "100%",
          background: `linear-gradient(90deg, ${accent}88, ${accent})`,
          boxShadow: `0 0 10px ${accent}`,
        }}
      />
      {totalS > 0 &&
        clips.slice(0, -1).map((c) => (
          <div
            key={c.id}
            style={{
              position: "absolute",
              left: `${((c.end / totalS) * 100).toFixed(2)}%`,
              top: -1,
              width: 2,
              height: 5,
              background: "rgba(255,255,255,0.58)",
            }}
          />
        ))}
    </div>
  );
};

export const PackagingOverlay: React.FC<OverlayProps> = ({
  theme,
  headerStyle = "frosted",
  scrim,
  viewport = { x: 0, y: 0.1, w: 1, h: 0.8 },
  safeY = [0.84, 0.96],
  palette = {},
  slots = {},
  header,
  cards = [],
  callouts = [],
  clips = [],
  showProgress = true,
}) => {
  const editorial = isEditorialCinema(headerStyle);
  const effectiveProgress = showProgress && !editorial;
  const showScrim = scrim ?? theme?.remotion?.scrim ?? editorial;
  const headerRect = clampAboveSafeY(
    slots.header || (editorial ? { x: 0.041, y: 0.029, w: 0.84, h: 0.095 } : { x: 0.06, y: 0.06, w: 0.88, h: 0.12 }),
    safeY,
  );

  return (
    <AbsoluteFill style={{ backgroundColor: "transparent", pointerEvents: "none" }}>
      {showScrim ? (
        <>
          <div
            style={{
              position: "absolute",
              left: 0,
              top: 0,
              width: "100%",
              height: "26%",
              background: "linear-gradient(180deg, rgba(0, 0, 0, 0.50) 0%, rgba(0, 0, 0, 0.22) 56%, rgba(0, 0, 0, 0) 100%)",
            }}
          />
          <div
            style={{
              position: "absolute",
              left: 0,
              bottom: 0,
              width: "100%",
              height: "24%",
              background: "linear-gradient(0deg, rgba(0, 0, 0, 0.40) 0%, rgba(0, 0, 0, 0.14) 55%, rgba(0, 0, 0, 0) 100%)",
            }}
          />
        </>
      ) : null}
      {header ? <HeaderBlock header={header} headerStyle={headerStyle} rect={headerRect} palette={palette} /> : null}
      {cards.map((card, i) => {
        if (card.kind === "huazi") {
          const raw = card.rect || slots.huazi || { x: 0.041, y: 0.135, w: 0.88, h: 0.088 };
          return <HuaziBlock key={`card-${i}`} card={card} rect={clampAboveSafeY(raw, safeY)} palette={palette} />;
        }
        if (card.kind === "reaction") {
          const raw = card.rect || slots.reaction || { x: 0.08, y: 0.21, w: 0.84, h: 0.08 };
          return <ReactionBlock key={`card-${i}`} card={card} rect={clampAboveSafeY(raw, safeY)} palette={palette} />;
        }
        const slotName = card.side === "right" ? "card_right" : "card_left";
        const raw = card.rect || slots[slotName] || { x: 0.04, y: 0.72, w: 0.44, h: 0.08 };
        return <CardBlock key={`card-${i}`} card={card} rect={clampAboveSafeY(raw, safeY)} palette={palette} />;
      })}
      {callouts.map((item, i) => {
        const slotName = item.slot || (i % 2 === 0 ? "callout_upper" : "callout_lower");
        const raw = item.rect || slots[slotName] || { x: 0.14, y: 0.21, w: 0.72, h: 0.06 };
        return <CalloutBlock key={`callout-${i}`} item={item} rect={clampAboveSafeY(raw, safeY)} palette={palette} />;
      })}
      {effectiveProgress ? <ProgressBar viewport={viewport} safeY={safeY} clips={clips} palette={palette} /> : null}
    </AbsoluteFill>
  );
};
