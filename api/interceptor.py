import http.server
import socketserver

# 这里设置你需要监听的端口
PORT = 9621

class RequestHandler(http.server.BaseHTTPRequestHandler):
    # 覆盖默认的日志记录方法，使我们的自定义打印更清晰
    def log_message(self, format, *args):
        pass

    def handle_request(self):
        print("\n" + "="*50)
        print(f"📡 收到请求: {self.command} {self.path} {self.request_version}")
        print(f"🌐 客户端 IP: {self.client_address[0]}")
        print("-" * 50)
        
        # 1. 打印请求头 (Headers)
        print("【请求头 / Headers】")
        for key, value in self.headers.items():
            print(f"{key}: {value}")
        
        # 2. 打印请求体 (Body)
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 0:
            post_data = self.rfile.read(content_length)
            print("-" * 50)
            print("【请求体 / Body】")
            try:
                # 尝试以 UTF-8 文本格式解码打印
                print(post_data.decode('utf-8'))
            except UnicodeDecodeError:
                # 如果是二进制文件等非文本格式，则直接打印字节流
                print(f"<二进制数据或无法解码的内容: {len(post_data)} bytes>")
                print(post_data)
        
        print("="*50 + "\n")
        
        # 3. 给客户端返回一个基础的 200 OK 响应，避免请求方超时报错
        self.send_response(500)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "success", "message": "Request received"}')

    # 将常见的 HTTP 方法都映射到我们的处理函数上
    def do_GET(self): self.handle_request()
    def do_POST(self): self.handle_request()
    def do_PUT(self): self.handle_request()
    def do_DELETE(self): self.handle_request()
    def do_PATCH(self): self.handle_request()

if __name__ == "__main__":
    # 使用 TCPServer 启动服务
    with socketserver.TCPServer(("", PORT), RequestHandler) as httpd:
        print(f"🚀 测试服务器已启动，正在监听端口: {PORT}")
        print("💡 提示: 按 Ctrl+C 可以停止服务")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n🛑 服务已安全停止。")
            httpd.server_close()