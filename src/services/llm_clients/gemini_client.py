from src.services.llm_clients.llm_client import LLMClient
from typing import List, Dict, Any
import google.generativeai as genai
import logging

logger = logging.getLogger(__name__)


class GeminiClient(LLMClient):
    def __init__(self, api_key: str, base_url: str) -> None:
        self.api_key = api_key
        self.base_url = base_url
        genai.configure(api_key=self.api_key)

    def create_completion(
        self, config: Dict[str, Any], messages: List[Dict[str, str]]
    ) -> Dict[str, Any]:
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
        )

        # 准备聊天历史
        history = []
        for message in messages[:-1]:  # 除了最后一条消息
            history.append({"role": message["role"], "parts": [message["content"]]})

        try:
            # 开始聊天会话
            chat_session = model.start_chat(history=history)

            # 发送最后一条消息
            response = chat_session.send_message(messages[-1]["content"])
            content = response.text.strip()

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
            return {"content": content, "usage": usage}
        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            raise
