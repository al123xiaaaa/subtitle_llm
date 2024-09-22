from enum import Enum


class LLMClientType(Enum):
    OPENAI = "openai"
    CUSTOM = "custom"
    GEMINI = "gemini"
