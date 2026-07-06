import os
from google import genai

def list_models():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY not set")
        return
    
    client = genai.Client(api_key=api_key)
    print("Available models:")
    for model in client.models.list():
        print(f"- {model.name} (Supported actions: {model.supported_actions})")

if __name__ == "__main__":
    list_models()
