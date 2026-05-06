import { useState } from "react";

type Size = "sm" | "md";

const SIZE_CLASS: Record<Size, string> = {
  sm: "h-8 w-8 text-[10px]",
  md: "h-9 w-9 text-xs",
};

function initials(name: string): string {
  if (!name) return "??";
  const parts = name.trim().split(/\s+/).slice(0, 2);
  return parts.map((p) => p[0]?.toUpperCase() ?? "").join("") || name[0].toUpperCase();
}

type Props = {
  name: string;
  src?: string | null;
  size?: Size;
};

/**
 * Round avatar with an `<img>` first, initials fallback second. The image is
 * lazy-loaded and uses `referrerpolicy=no-referrer` so we don't leak the
 * panel's origin to FunPay's CDN. If the URL is missing or fails to load,
 * we render the initials over the same neumorphic disc.
 */
export default function Avatar({ name, src, size = "md" }: Props) {
  const [errored, setErrored] = useState(false);
  const showImage = !!src && !errored;
  const sizeClass = SIZE_CLASS[size];
  const safeName = name?.trim() || "?";

  return (
    <div
      className={`grid flex-shrink-0 place-items-center overflow-hidden rounded-full bg-surface shadow-neu-sm font-semibold uppercase ${sizeClass}`}
      title={safeName}
      aria-label={safeName}
    >
      {showImage ? (
        <img
          src={src!}
          alt=""
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          onError={() => setErrored(true)}
          className="h-full w-full object-cover"
        />
      ) : (
        <span className="text-ink2">{initials(safeName)}</span>
      )}
    </div>
  );
}
