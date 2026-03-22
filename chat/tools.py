import httpx
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'chart_agent'))
from database_query import execute_query

KNOWLEDGE_BASE_URL = "http://localhost:6772/triggers/webhook-debug/mnwXLKRp0WDOeH7XjBjAAOO6"


def query_knowledge_base(question: str) -> str:
    """查询知识库，返回相关内容"""
    try:
        response = httpx.post(
            KNOWLEDGE_BASE_URL,
            json={"question": question},
            timeout=30.0
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict):
            return data.get("answer") or data.get("result") or data.get("text") or str(data)
        return str(data)
    except httpx.TimeoutException:
        return "知识库查询超时，请稍后重试。"
    except httpx.HTTPStatusError as e:
        return f"知识库服务返回错误: {e.response.status_code}"
    except Exception as e:
        return f"知识库查询失败: {str(e)}"


def query_database(sql: str) -> str:
    """执行数据库查询，返回格式化结果"""
    try:
        results = execute_query(sql)
        if results is None:
            return "查询执行完成，无返回数据。"
        if len(results) == 0:
            return "查询结果为空。"
        lines = []
        for i, row in enumerate(results, 1):
            lines.append(f"第{i}行: {row}")
        return "\n".join(lines)
    except Exception as e:
        return f"数据库查询失败: {str(e)}"
