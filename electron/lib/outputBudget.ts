// 空值表示不发送输出上限，由服务商决定；0 不是自动模式。
export function parseTranslationMaxTokens(value: unknown): number | null {
  if (value === undefined || value === null || (typeof value === "string" && !value.trim())) {
    return null;
  }
  const number = typeof value === "string" && /^\d+$/.test(value.trim()) ? Number(value) : value;
  if (typeof number !== "number" || !Number.isSafeInteger(number) || number <= 0) {
    throw new Error("翻译输出上限必须为正整数，留空使用服务商默认值");
  }
  return number;
}
