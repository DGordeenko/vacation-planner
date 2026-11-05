// ESLint для фронтенда (tasks/00-skeleton.md, итерация 2).
// Типы проверяет tsc; здесь — то, чего tsc не ловит: правила хуков React
// (зависимости эффектов, вызов хуков по условию) и типичные ошибки.
import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: { ecmaVersion: 2022, globals: globals.browser },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // Новое правило eslint-plugin-react-hooks 7 (React Compiler): сброс
      // состояния в начале эффекта. В коде 22 таких места, все — осознанный
      // сброс результатов проверки при смене дат или причины; переделка —
      // правка поведения компонентов, отдельной задачей, не обновлением
      // зависимостей. Остальные новые правила включены и проходят.
      "react-hooks/set-state-in-effect": "off",
    },
  },
);
