/** @type {import('tailwindcss').Config} */
export default {
  content: ["./frontend/index.html", "./frontend/src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--color-bg)",
        surface: "var(--color-surface)",
        "surface-2": "var(--color-surface-2)",
        border: "var(--color-border)",
        "border-strong": "var(--color-border-strong)",
        text: "var(--color-text)",
        muted: "var(--color-text-muted)",
        faint: "var(--color-text-faint)",
        accent: "var(--color-accent)",
        "accent-hover": "var(--color-accent-hover)",
        "accent-fg": "var(--color-accent-fg)",
        "accent-muted": "var(--color-accent-muted)",
        success: "var(--color-success)",
        "success-muted": "var(--color-success-muted)",
        warning: "var(--color-warning)",
        "warning-muted": "var(--color-warning-muted)",
        danger: "var(--color-danger)",
        "danger-muted": "var(--color-danger-muted)",
        info: "var(--color-info)",
        "info-muted": "var(--color-info-muted)",
        focus: "var(--color-focus)",
        overlay: "var(--color-overlay)",
      },
      borderRadius: {
        sm: "var(--radius-sm)",
        md: "var(--radius-md)",
        lg: "var(--radius-lg)",
      },
      boxShadow: {
        sm: "var(--shadow-sm)",
        md: "var(--shadow-md)",
      },
      fontFamily: {
        sans: ["var(--font-sans)"],
        mono: ["var(--font-mono)"],
      },
      transitionDuration: {
        fast: "var(--duration-fast)",
        normal: "var(--duration-normal)",
      },
    },
  },
  plugins: [],
};
