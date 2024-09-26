from src.services.llm_clients.llm_client import LLMClient
from src.utils.rate_limiter import RateLimiter
from typing import List, Dict, Any
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
import logging
import time
from google.api_core import exceptions

logger = logging.getLogger(__name__)


class GeminiClient(LLMClient):
    def __init__(
        self, api_key: str, base_url: str, rate_limiter: RateLimiter = None
    ) -> None:
        super().__init__(rate_limiter)
        self.api_key = api_key
        self.base_url = base_url
        genai.configure(api_key=self.api_key)

    def create_completion(
        self, config: Dict[str, Any], messages: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        logger.info("Starting create_completion in GeminiClient")
        if self.rate_limiter:
            logger.debug("Acquiring rate limiter")
            self.rate_limiter.acquire()

        # 创建生成配置
        generation_config = {
            "temperature": config.get("temperature", 0.45),
            "top_p": config.get("top_p", 0.95),
            "top_k": config.get("top_k", 64),
            "max_output_tokens": config.get("max_tokens", 8192),
        }

        # 创建模型
        model = genai.GenerativeModel(
            model_name=config["model"],
            generation_config=generation_config,
            safety_settings={
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            },
        )

        # 准备聊天历史
        history = []
        for message in messages[:-1]:  # 除了最后一条消息
            history.append({"role": message["role"], "parts": [message["content"]]})

        max_retries = 6
        retry_delay = 40

        for attempt in range(max_retries):
            try:
                # 开始聊天会话
                chat_session = model.start_chat(history=history)

                # 发送最后一条消息
                response = chat_session.send_message(messages[-1]["content"])
                content = response.text.strip()
                logger.debug(
                    f"Received response: {content[:50]}..."
                )  # Log first 50 chars

                # 计算令牌使用情况（注意：Gemini 可能没有提供精确的令牌计数）
                prompt_tokens = self.num_tokens_from_messages(messages, config["model"])
                completion_tokens = self.num_tokens_from_messages(
                    [{"content": content}], config["model"]
                )
                usage = {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                }
                logger.info("Successfully completed create_completion")
                return {"content": content, "usage": usage}
            except (exceptions.ResourceExhausted, Exception) as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Error occurred: {e}. Retrying in {retry_delay} seconds..."
                    )
                    time.sleep(retry_delay)
                else:
                    logger.error(f"Max retries exceeded. Gemini API error: {e}")
                    raise

        raise Exception("Max retries exceeded")
