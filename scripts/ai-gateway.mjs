import { experimental_evaluate as evaluate } from 'ai';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

export const JEV_MODEL = 'typesafe-ai/jev';
export const JEV_CONTEXT_TOKENS = 32_000;

export async function evaluateJev(state, questions) {
  const envFile = fileURLToPath(new URL('../.env.local', import.meta.url));
  if (existsSync(envFile)) process.loadEnvFile(envFile);
  if (!process.env.AI_GATEWAY_API_KEY) throw new Error('gateway_key_missing');
  // 使用 UTF-8 字节数作保守预算，并预留协议开销；不截断待检查的原文。
  if (Buffer.byteLength(JSON.stringify({ state, questions }), 'utf8') > JEV_CONTEXT_TOKENS - 2048) {
    throw new Error('gateway_context_exceeded');
  }
  return evaluate({
    model: JEV_MODEL,
    state,
    questions,
    maxRetries: 0,
    abortSignal: AbortSignal.timeout(45_000),
  });
}

export function safeGatewayError(error) {
  // SDK 异常可能包含请求头和响应正文，只输出白名单错误码与 HTTP 状态。
  const allowed = new Set(['gateway_key_missing', 'gateway_context_exceeded']);
  if (allowed.has(error?.message)) return error.message;
  if (error?.statusCode === 403 && /valid credit card on file/i.test(error?.message ?? '')) {
    return 'gateway_billing_setup_required（请在 Vercel AI Gateway 账户绑定有效信用卡后重试）';
  }
  return Number.isInteger(error?.statusCode) ? `gateway_http_${error.statusCode}` : 'gateway_request_failed';
}
