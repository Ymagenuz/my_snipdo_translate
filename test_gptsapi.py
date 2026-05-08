from openai import OpenAI
import os

client = OpenAI(
    api_key=os.getenv("GPTSAPI_API_KEY", "").strip(),
    base_url="https://api.gptsapi.net/v1"
)

resp = client.models.list()
print(resp)
