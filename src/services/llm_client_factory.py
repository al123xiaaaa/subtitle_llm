from openai import OpenAI
from src.services.custom_llm_client import CustomLLMClient
import tiktoken


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
                api_key=config["api_key"], base_url=config["endpoint"]
            )
        else:
            raise ValueError(f"Unsupported client type: {client_type}")

    @staticmethod
    def num_tokens_from_messages(messages, model):
        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")  # 默认编码
        if model.startswith("gpt-3.5-turbo"):
            tokens_per_message = 4
            tokens_per_name = -1
        elif model.startswith("gpt-4"):
            tokens_per_message = 3
            tokens_per_name = 1
        else:
            # 对未知模型的处理
            tokens_per_message = 3
            tokens_per_name = 1
        num_tokens = 0
        for message in messages:
            num_tokens += tokens_per_message
            for key, value in message.items():
                num_tokens += len(encoding.encode(value))
                if key == "name":
                    num_tokens += tokens_per_name
        num_tokens += 3  # 每个回复都有额外的令牌
        return num_tokens

    @staticmethod
    def create_completion(client, config, messages):
        model = config["model"]
        if isinstance(client, OpenAI):
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=config["max_tokens"],
                temperature=config["temperature"],
                top_p=config.get("top_p", 1.0),
                frequency_penalty=config.get("frequency_penalty", 0.0),
                presence_penalty=config.get("presence_penalty", 0.0),
            )
            content = response.choices[0].message.content.strip()
            usage = response.usage
            return {"content": content, "usage": usage}
        elif isinstance(client, CustomLLMClient):
            response = client.chat_completion(
                model=model,
                messages=messages,
                max_tokens=config["max_tokens"],
                temperature=config["temperature"],
                top_p=config.get("top_p", 1.0),
                top_k=config.get("top_k", 50),
                frequency_penalty=config.get("frequency_penalty", 0.0),
                n=config.get("n", 1),
                stream=config.get("stream", False),
            )
            content = response["choices"][0]["message"]["content"].strip()
            # 计算令牌使用情况
            prompt_tokens = LLMClientFactory.num_tokens_from_messages(messages, model)
            completion_tokens = LLMClientFactory.num_tokens_from_messages(
                [{"content": content}], model
            )
            usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            }
            return {"content": content, "usage": usage}
        else:
            raise ValueError(f"Unsupported client type: {type(client)}")
