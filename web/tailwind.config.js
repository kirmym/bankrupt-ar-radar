/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        class: {
          A: "#10b981",
          B: "#eab308",
          C: "#f97316",
          D: "#ef4444",
        },
      },
    },
  },
  plugins: [],
};
