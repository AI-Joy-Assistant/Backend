import os
from openai import OpenAI
from dotenv import load_dotenv

# .env 로드
load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")
model = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")

print(f"Testing OpenAI API...")
print(f"Model: {model}")
print(f"API Key: {api_key[:5]}...{api_key[-5:] if api_key else 'None'}")

if not api_key:
    print("Error: OPENAI_API_KEY not found in environment variables.")
    exit(1)

client = OpenAI(api_key=api_key)

try:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": "Hello! Can you hear me?"}
        ],
        max_tokens=10
    )
    print("\n✅ API Success!")
    print(f"Response: {response.choices[0].message.content}")
except Exception as e:
    print("\n❌ API Failed!")
    print(f"Error Type: {type(e).__name__}")
    print(f"Error Message: {str(e)}")
