/**
 * The Lantern mark, as an inline SVG so it inherits the ClaimMate tokens.
 *
 * Shape: a handle, a cap, a glass body with a visible frame and a small flame,
 * on a base. Dark brown (--brand-900) carries the identity; the glass panel is
 * white with neutral grey detail; --brand-700 is used only for the flame.
 * On dark backgrounds (tone="dark") the frame uses --brand-on-dark so the
 * silhouette stays legible. Keep the 32x40 aspect ratio; never stretch it.
 */
export function LanternMark({ size = 28, tone = "light", title = "Lantern" }: { size?: number; tone?: "light" | "dark"; title?: string }) {
  const frame = tone === "dark" ? "var(--brand-on-dark)" : "var(--brand-900)";
  const glass = tone === "dark" ? "rgba(255,255,255,0.92)" : "#FFFFFF";
  const flame = tone === "dark" ? "#FFFFFF" : "var(--brand-700)";
  const detail = tone === "dark" ? "rgba(227,215,205,0.55)" : "var(--grey-300)";
  const h = Math.round(size * 1.25);
  return (
    <svg width={size} height={h} viewBox="0 0 32 40" role="img" aria-label={title} focusable="false" style={{ display: "block", flexShrink: 0 }}>
      <title>{title}</title>
      {/* handle */}
      <path d="M11 7.5a5 5 0 0 1 10 0" fill="none" stroke={frame} strokeWidth="2.4" strokeLinecap="round" />
      {/* cap */}
      <path d="M9.5 11.5 12 7.5h8l2.5 4z" fill={frame} />
      {/* body frame */}
      <rect x="7" y="11.5" width="18" height="20" rx="3" fill={frame} />
      {/* glass panel */}
      <rect x="10" y="14" width="12" height="15" rx="1.5" fill={glass} />
      {/* vertical mullion detail */}
      <rect x="15.4" y="14" width="1.2" height="15" fill={detail} />
      {/* flame */}
      <path d="M16 18.2c1.9 2.2 3 3.7 3 5.3a3 3 0 0 1-6 0c0-1.1.4-2 1.2-3 .3.9.8 1.4 1.3 1.6-.2-1.3 0-2.6.5-3.9z" fill={flame} />
      {/* base */}
      <rect x="9" y="31.5" width="14" height="3" rx="1" fill={frame} />
      <rect x="11" y="34.5" width="10" height="2.5" rx="1" fill={frame} />
    </svg>
  );
}

export function BrandLockup({ subtitle, tone = "light", size = 28 }: { subtitle?: string; tone?: "light" | "dark"; size?: number }) {
  return (
    <span className="brand" style={{ padding: 0 }}>
      <LanternMark size={size} tone={tone} />
      <span className="brand-text">
        <b style={tone === "dark" ? { color: "#FFFFFF" } : undefined}>Lantern</b>
        {subtitle && <span className="brand-sub" style={tone === "dark" ? { color: "var(--brand-on-dark)" } : undefined}>{subtitle}</span>}
      </span>
    </span>
  );
}
