from abc import ABC, abstractmethod
from typing import List, Dict, Any
import tiktoken


class LLMClient(ABC):
    @abstractmethod
    def create_completion(
        self, config: Dict[str, Any], messages: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """
        创建一个完成（completion）请求。

        Args:
            config (Dict[str, Any]): 配置字典，包含模型参数。
            messages (List[Dict[str, str]]): 消息字典列表。

        Returns:
            Dict[str, Any]: 包含完成内容和使用情况的信息字典。
        """
        pass

    def num_tokens_from_messages(
        self, messages: List[Dict[str, str]], model: str
    ) -> int:
        """
        计算消息中的令牌数量。

        Args:
            messages (List[Dict[str, str]]): 消息字典列表。
            model (str): 模型名称。

        Returns:
            int: 总令牌数量。
        """
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
