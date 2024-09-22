from src.services.llm_clients.llm_client import LLMClient
from typing import List, Dict, Any
from openai import OpenAI


class OpenAIClient(LLMClient):
    def __init__(self, api_key: str, base_url: str) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def create_completion(
        self, config: Dict[str, Any], messages: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        response = self.client.chat.completions.create(
            model=config["model"],
            messages=messages,
            max_tokens=config["max_tokens"],
            temperature=config.get("temperature", 0.5),
            top_p=config.get("top_p", 1.0),
            frequency_penalty=config.get("frequency_penalty", 0.0),
            presence_penalty=config.get("presence_penalty", 0.0),
        )
        content = response.choices[0].message.content.strip()
        usage = response.usage
        return {"content": content, "usage": usage}
