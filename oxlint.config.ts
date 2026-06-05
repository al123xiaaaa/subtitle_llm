import { defineConfig } from "oxlint";

export default defineConfig({
  plugins: ["eslint", "typescript", "unicorn", "oxc", "import", "promise", "node"],
  env: {
    browser: true,
    es2022: true,
    node: true,
  },
  ignorePatterns: [
    "dist/**",
    "node_modules/**",
    "coverage/**",
    ".venv/**",
    "venv/**",
    "__pycache__/**",
    ".pytest_cache/**",
  ],
  categories: {
    correctness: "error",
    suspicious: "error",
    perf: "warn",
  },
  options: {
    reportUnusedDisableDirectives: "warn",
  },
  rules: {
    "eslint/no-underscore-dangle": ["error", { allow: ["__dirname", "__filename"] }],
    "import/no-cycle": "warn",
    "unicorn/consistent-function-scoping": "off",
  },
  overrides: [
    {
      files: ["**/*.d.ts"],
      rules: {
        "unicorn/require-module-specifiers": "off",
      },
    },
    {
      files: ["electron/renderer/src/main.ts"],
      rules: {
        "import/no-unassigned-import": "off",
      },
    },
  ],
});
