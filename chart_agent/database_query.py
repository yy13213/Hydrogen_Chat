import psycopg2
from psycopg2 import Error

# 数据库连接配置
# ⚠️ 安全提示：在生产环境中，强烈建议将这些敏感信息移至 .env 文件中，通过 os.getenv() 获取，避免密码硬编码在代码库里。
DB_CONFIG = {
    "host": "127.0.0.1",
    "port": "5433",
    "dbname": "digital-hydrogen",
    "user": "hydrogen_chat",
    "password": "TJhc123,."
}

def execute_query(query: str, fetch_results: bool = True, params: tuple = None):
    """
    连接到 PostgreSQL 数据库并执行指定的 SQL 查询。
    
    参数:
        query (str): 需要执行的 SQL 语句。
        fetch_results (bool): 是否需要获取返回结果（SELECT 语句通常为 True，UPDATE/INSERT 为 False）。
        params (tuple): 用于防注入的 SQL 参数。
        
    返回:
        list/tuple: 如果 fetch_results 为 True，返回查询结果的数据行；否则返回 None。
    """
    connection = None
    results = None
    
    try:
        # 建立数据库连接
        connection = psycopg2.connect(**DB_CONFIG)
        
        # 使用 context manager 自动管理游标
        with connection.cursor() as cursor:
            # 执行查询，支持参数化以防止 SQL 注入
            cursor.execute(query, params)
            
            if fetch_results:
                # 获取所有结果
                results = cursor.fetchall()
                # 如果你需要列名，可以取消下方注释：
                # col_names = [desc[0] for desc in cursor.description]
                # return [dict(zip(col_names, row)) for row in results]
                
            # 提交事务（对于 INSERT/UPDATE/DELETE 语句是必需的）
            connection.commit()
            
    except (Exception, Error) as error:
        print(f"❌ 数据库操作异常: {error}")
        
    finally:
        # 确保无论是否发生异常，数据库连接都会被安全关闭
        if connection:
            connection.close()
            
    return results

# ---------------------------------------------------------
# 直接运行此脚本时的默认测试逻辑
# ---------------------------------------------------------
if __name__ == "__main__":
    print("⏳ 正在测试数据库连接...\n")
    
    # 使用你提供的测试 SQL
    test_query = "SELECT * FROM article_and_methods LIMIT 5;"
    print(f"执行查询: {test_query}\n")
    
    try:
        data = execute_query(test_query)
        
        if data is not None:
            print("✅ 连接成功！获取到的数据如下：")
            for index, row in enumerate(data, start=1):
                print(f"第 {index} 行: {row}")
        else:
            print("⚠️ 连接已建立，但查询未返回任何数据，或者语句执行失败。")
            
    except Exception as e:
        print(f"❌ 连接测试失败: {e}")