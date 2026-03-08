#由于dify不支持vllm的Reranker，使用9583做中转，将请求格式更改，然后再发送到9582。


import math
import httpx
import uvicorn
import asyncio
from fastapi import FastAPI, Request

app = FastAPI()
VLLM_API_URL = "http://127.0.0.1:9582/v1/completions"

# 封装单个请求为一个异步函数
async def fetch_score(client, i, query, doc, model_name):
    prefix = '<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
    suffix = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
    instruction = "Given a web search query, retrieve relevant passages that answer the query."
    prompt = f"{prefix}<Instruct>: {instruction}\n<Query>: {query}\n<Document>: {doc}{suffix}"
    
    payload = {
        "model": model_name,
        "prompt": prompt,
        "max_tokens": 1,
        "logprobs": 10,
        "temperature": 0.0,
        "echo": False
    }
    
    try:
        resp = await client.post(VLLM_API_URL, json=payload, timeout=60.0)
        resp_json = resp.json()
        top_logprobs = resp_json["choices"][0]["logprobs"]["top_logprobs"][0]
        logprob_yes = top_logprobs.get("yes", top_logprobs.get("Yes", top_logprobs.get(" yes", -100)))
        logprob_no = top_logprobs.get("no", top_logprobs.get("No", top_logprobs.get(" no", -100)))
        score = math.exp(logprob_yes) / (math.exp(logprob_yes) + math.exp(logprob_no))
    except Exception as e:
        print(f"请求失败: {e}")
        score = 0.0001
        
    return {"index": i, "document": {"text": doc}, "relevance_score": score}

@app.post("/v1/rerank")
async def rerank_endpoint(request: Request):
    data = await request.json()
    query = data.get("query")
    documents = data.get("documents", [])
    model_name = data.get("model", "Qwen3-Reranker-0.6B")
    top_n = data.get("top_n", len(documents))
    
    # 使用 asyncio.gather 实现并发请求
    async with httpx.AsyncClient() as client:
        tasks = [fetch_score(client, i, query, doc, model_name) for i, doc in enumerate(documents)]
        results = await asyncio.gather(*tasks)
            
    results = sorted(results, key=lambda x: x["relevance_score"], reverse=True)[:top_n]
    
    return {"id": "qwen3-reranker-proxy-async", "model": model_name, "results": results}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9583)
