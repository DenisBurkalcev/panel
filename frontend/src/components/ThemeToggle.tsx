import { useTheme } from "../theme";

/**
 * Animated dark/light theme toggle. The icon swap is a CSS crossfade with a
 * tiny rotate, so the moon → sun transition reads as a smooth swap rather
 * than a hard cut. The button itself uses the same neumorphic primitives as
 * the rest of the panel so it adapts to whichever theme is active.
 */
export default function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const isLight = theme === "light";
  const label = isLight ? "Switch to dark theme" : "Switch to light theme";

  return (
    <button
      type="button"
      onClick={toggle}
      className="btn-icon relative overflow-hidden"
      title={label}
      aria-label={label}
      aria-pressed={isLight}
    >
      {/* Both icons are rendered; CSS swaps which one is visible. The wrapping
          span uses absolute positioning so the rotation pivots around the
          button's centre, which makes the transition feel intentional. */}
      <span
        aria-hidden
        className={`absolute inset-0 grid place-items-center text-base leading-none transition-all duration-300 ease-out ${
          isLight
            ? "rotate-90 scale-50 opacity-0"
            : "rotate-0 scale-100 opacity-100"
        }`}
      >
        🌙
      </span>
      <span
        aria-hidden
        className={`absolute inset-0 grid place-items-center text-base leading-none transition-all duration-300 ease-out ${
          isLight
            ? "rotate-0 scale-100 opacity-100"
            : "-rotate-90 scale-50 opacity-0"
        }`}
      >
        ☀️
      </span>
    </button>
  );
}
