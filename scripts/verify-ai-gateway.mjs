import assert from 'node:assert/strict';
import { evaluateJev, JEV_MODEL, safeGatewayError } from './ai-gateway.mjs';

try {
  const result = await evaluateJev(
    { source: "I don't think this will work.", translation: '我认为这会奏效。' },
    {
      meaning_reversed: {
        type: 'boolean',
        instructions: 'Does translation reverse the negative meaning expressed in source?',
      },
    },
  );
  const probability = result.answers.meaning_reversed.probability;
  assert.ok(Number.isFinite(probability) && probability >= 0 && probability <= 1);
  // 这是根据真实判断格式化的文字，不是 Jev 自由生成的回答。
  console.log(`${JEV_MODEL} 调用成功：译文反转原文否定含义的概率为 ${probability}。`);
  console.log(JSON.stringify({ answers: result.answers, usage: result.usage }));
} catch (error) {
  console.error(`Gateway 验证失败：${safeGatewayError(error)}`);
  process.exitCode = 1;
}
