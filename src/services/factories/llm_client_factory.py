from src.models.enums import LLMClientType
from src.services.llm_clients.openai_client import OpenAIClient
from src.services.llm_clients.custom_llm_client import CustomLLMClient
from src.services.llm_clients.gemini_client import GeminiClient
from src.services.llm_clients.llm_client import LLMClient
from src.utils.rate_limiter import RateLimiter  # Import RateLimiter
from typing import Dict, Any, List


class LLMClientFactory:
    """
    Factory class to create LLM client instances based on configuration.
    """

    @staticmethod
    def create_client(config: Dict[str, Any]) -> LLMClient:
        """
        Creates an instance of LLMClient based on the provided configuration.

        Args:
            config (Dict[str, Any]): Configuration dictionary containing client type and parameters.

        Returns:
            LLMClient: An instance of a subclass of LLMClient.

        Raises:
            ValueError: If the client type is unsupported or if the API key is missing.
        """
        client_type = config.get("type", LLMClientType.OPENAI.value)
        try:
            client_enum = LLMClientType(client_type)
        except ValueError:
            raise ValueError(f"Unsupported client type: {client_type}")

        api_key = config.get("api_key")
        if not api_key:
            raise ValueError("API key is required")

        base_url = config.get("endpoint")  # endpoint 是可选的

        # Initialize RateLimiter if rate_limit is specified
        rate_limit = config.get("rate_limit")
        if rate_limit:
            max_calls = rate_limit
            period = 60  # Assuming rate_limit is per minute
            rate_limiter = RateLimiter(max_calls=max_calls, period=period)
        else:
            rate_limiter = None

        if client_enum == LLMClientType.OPENAI:
            return OpenAIClient(
                api_key=api_key, base_url=base_url, rate_limiter=rate_limiter
            )
        elif client_enum == LLMClientType.CUSTOM:
            return CustomLLMClient(
                api_key=api_key, base_url=base_url, rate_limiter=rate_limiter
            )
        elif client_enum == LLMClientType.GEMINI:
            return GeminiClient(
                api_key=api_key, base_url=base_url, rate_limiter=rate_limiter
            )
        else:
            raise ValueError(f"Unsupported client type: {client_type}")

    @staticmethod
    def create_completion(
        client: LLMClient, config: Dict[str, Any], messages: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """
        Creates a completion using the specified LLM client.

        Args:
            client (LLMClient): The LLM client instance.
            config (Dict[str, Any]): Configuration dictionary containing model parameters.
            messages (List[Dict[str, str]]): List of message dictionaries.

        Returns:
            Dict[str, Any]: Dictionary containing the completion content and usage information.
        """
        return client.create_completion(config, messages)
