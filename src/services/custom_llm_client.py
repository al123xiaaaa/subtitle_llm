import requests
import time

class CustomLLMClient:
    def __init__(self, api_key, base_url):
        self.api_key = api_key
        self.base_url = base_url

    def chat_completion(self, model, messages, max_tokens, temperature, **kwargs):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            **kwargs
        }
        
        max_retries = 6
        retry_delay = 60

        for attempt in range(max_retries):
            try:
                response = requests.post(f"{self.base_url}/chat/completions", headers=headers, json=data)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429 and attempt < max_retries - 1:
                    print(f"Rate limit exceeded. Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    raise

        raise Exception("Max retries exceeded")

class CustomChatCompletion:
    def __init__(self, response):
        self.response = response

    @property
    def choices(self):
        return [CustomChoice(choice) for choice in self.response.get("choices", [])]

class CustomChoice:
    def __init__(self, choice_data):
        self.message = CustomMessage(choice_data.get("message", {}))

class CustomMessage:
    def __init__(self, message_data):
        self.content = message_data.get("content", "")
