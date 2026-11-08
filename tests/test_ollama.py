import ollama

response = ollama.chat(
    model="qwen3:30b-a3b",
    messages=[
        {
            "role": "user",
            "content": "안녕하세요. 간단하게 자기소개를 해주세요."
        }
    ],
)

print(response["message"]["content"])