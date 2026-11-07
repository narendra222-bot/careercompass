import type { CSSProperties } from "react";

/** CareerCompass mark: a clay compass. */
export function Mark({
  size = 26,
  style,
  className = "mark",
}: {
  size?: number;
  style?: CSSProperties;
  className?: string;
}) {
  return (
    <svg
      className={className}
      viewBox="0 0 32 32"
      width={size}
      height={size}
      style={{ width: size, height: size, ...style }}
      aria-hidden="true"
    >
      <circle cx="16" cy="16" r="12.5" fill="none" stroke="var(--accent)" strokeWidth="2.4" />
      <path d="M16 1.5v4M16 26.5v4M1.5 16h4M26.5 16h4" stroke="var(--accent)" strokeWidth="2.4" strokeLinecap="round" />
      <path d="M21.8 10.2L17.9 17.9 10.2 21.8 14.1 14.1z" fill="var(--accent)" />
      <circle cx="16" cy="16" r="1.8" fill="var(--bg)" />
    </svg>
  );
}

export function Logo({ size = 26, fontSize }: { size?: number; fontSize?: number }) {
  return (
    <div className="logo" style={fontSize ? { fontSize } : undefined}>
      <Mark size={size} />
      CareerCompass
    </div>
  );
}
