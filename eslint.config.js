import tsParser from "@typescript-eslint/parser";
import pluginVue from "eslint-plugin-vue";

const vueFiles = ["electron/renderer/src/**/*.vue"];
const vueRecommended = pluginVue.configs["flat/recommended"].map((config) => Object.assign({}, config, { files: vueFiles }));

export default [
  {
    ignores: ["dist/**", "node_modules/**", "coverage/**"],
  },
  ...vueRecommended,
  {
    files: vueFiles,
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      parserOptions: {
        parser: tsParser,
      },
    },
    rules: {
      "vue/multi-word-component-names": ["error", { ignores: ["App"] }],
    },
  },
];
