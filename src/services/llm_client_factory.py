from openai import OpenAI
from src.services.custom_llm_client import CustomLLMClient

class LLMClientFactory:
    @staticmethod
    def create_client(config):
        client_type = config.get("type", "openai")
        if client_type == "openai":
            return OpenAI(
                api_key=config["api_key"],
                base_url=config["endpoint"],
            )
        elif client_type == "custom":
            return CustomLLMClient(
                api_key=config["api_key"],
                base_url=config["endpoint"]
            )
        else:
            raise ValueError(f"Unsupported client type: {client_type}")

    @staticmethod
    def create_completion(client, config, messages):
        if isinstance(client, OpenAI):
            response = client.chat.completions.create(
                model=config["model"],
                messages=messages,
                max_tokens=config["max_tokens"],
                temperature=config["temperature"],
                top_p=config.get("top_p", 1.0),
                frequency_penalty=config.get("frequency_penalty", 0.0),
                presence_penalty=config.get("presence_penalty", 0.0),
            )
            return response.choices[0].message.content.strip()
        elif isinstance(client, CustomLLMClient):
            response = client.chat_completion(
                model=config["model"],
                messages=messages,
                max_tokens=config["max_tokens"],
                temperature=config["temperature"],
                top_p=config.get("top_p", 1.0),
                top_k=config.get("top_k", 50),
                frequency_penalty=config.get("frequency_penalty", 0.0),
                n=config.get("n", 1),
                stream=config.get("stream", False)
            )
            return response["choices"][0]["message"]["content"].strip()
        else:
            raise ValueError(f"Unsupported client type: {type(client)}")
