import { evaluateJev, safeGatewayError } from './ai-gateway.mjs';

// Python 仅通过 stdin 传递字幕与问题；密钥由 Node 从本地环境加载。
try {
  let input = '';
  for await (const part of process.stdin) {
    input += part;
    if (Buffer.byteLength(input, 'utf8') > 32_000) throw new Error('gateway_context_exceeded');
  }
  const { state, questions } = JSON.parse(input);
  const result = await evaluateJev(state, questions);
  console.log(JSON.stringify({ answers: result.answers, usage: result.usage, rounding: result.rounding }));
} catch (error) {
  console.log(JSON.stringify({ error: safeGatewayError(error) }));
  process.exitCode = 1;
}
