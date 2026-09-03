/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "var(--background)",
        foreground: "var(--foreground)",
        primary: {
          50: "#eef2ff",
          100: "#e0e7ff",
          500: "#6366f1",
          600: "#4f46e5",
          700: "#4338ca",
        },
        forest: {
          DEFAULT: "#174c3c",
          light: "#1d5f4b",
          dark: "#103c2f",
        },
        canvas: "#f7f7f2",
        clay: {
          DEFAULT: "#e87544",
          dark: "#d56234",
        },
        moss: "#e5f0e9",
      },
      animation: {
        "fade-in-up": "fadeInUp 0.6s ease-out forwards",
        "fade-in-down": "fadeInDown 0.5s ease-out forwards",
        "fade-in": "fadeIn 0.5s ease-out forwards",
        "slide-in-right": "slideInRight 0.5s ease-out forwards",
        "slide-in-left": "slideInLeft 0.5s ease-out forwards",
        "pulse-glow": "pulseGlow 2.5s ease-in-out infinite",
        "float-gentle": "floatGentle 4s ease-in-out infinite",
        "float-slow": "floatSlow 6s ease-in-out infinite",
        shimmer: "shimmer 2s linear infinite",
        "gradient-shift": "gradientShift 8s ease infinite",
        "scale-in": "scaleIn 0.3s ease-out forwards",
        "bounce-subtle": "bounceSubtle 2s ease-in-out infinite",
        "spin-slow": "spin 8s linear infinite",
        marquee: "marquee 30s linear infinite",
      },
      keyframes: {
        fadeInUp: {
          "0%": { opacity: "0", transform: "translateY(20px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        fadeInDown: {
          "0%": { opacity: "0", transform: "translateY(-10px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        fadeIn: {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        slideInRight: {
          "0%": { opacity: "0", transform: "translateX(20px)" },
          "100%": { opacity: "1", transform: "translateX(0)" },
        },
        slideInLeft: {
          "0%": { opacity: "0", transform: "translateX(-20px)" },
          "100%": { opacity: "1", transform: "translateX(0)" },
        },
        pulseGlow: {
          "0%, 100%": { boxShadow: "0 0 8px rgba(23, 76, 60, 0.3)" },
          "50%": { boxShadow: "0 0 20px rgba(23, 76, 60, 0.6)" },
        },
        floatGentle: {
          "0%, 100%": { transform: "translateY(0px)" },
          "50%": { transform: "translateY(-8px)" },
        },
        floatSlow: {
          "0%, 100%": { transform: "translateY(0px) rotate(0deg)" },
          "50%": { transform: "translateY(-12px) rotate(2deg)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        gradientShift: {
          "0%, 100%": { backgroundPosition: "0% 50%" },
          "50%": { backgroundPosition: "100% 50%" },
        },
        scaleIn: {
          "0%": { opacity: "0", transform: "scale(0.95)" },
          "100%": { opacity: "1", transform: "scale(1)" },
        },
        bounceSubtle: {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-4px)" },
        },
        marquee: {
          "0%": { transform: "translateX(0)" },
          "100%": { transform: "translateX(-50%)" },
        },
      },
      backdropBlur: {
        xs: "2px",
      },
      transitionDuration: {
        400: "400ms",
      },
    },
  },
  plugins: [],
};
