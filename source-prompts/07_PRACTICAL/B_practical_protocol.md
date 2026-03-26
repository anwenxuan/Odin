# 实战 B：通信协议分析

**作用**：深入理解项目使用的通信协议，掌握协议规范和调试技能

---

## B.1 协议概述

### B.1.1 项目常见协议类型

| 协议类型 | 示例 | 特征 |
|---------|------|------|
| **文本协议** | RESP、HTTP、STMP | 人类可读，便于调试 |
| **二进制协议** | Protocol Buffers、Thrift、MsgPack | 高效，体积小 |
| **混合协议** | Redis RESP2/RESP3 | 文本头 + 二进制数据 |

### B.1.2 协议文档位置

| 文件 | 说明 |
|------|------|
| `docs/protocol.md` | 协议规范文档 |
| `src/networking.c` | 网络 I/O 和协议解析 |
| `src/protocol.c` | 协议编解码 |
| `deps/hiredis/read.c` | RESP 解析器 |

---

## B.2 协议规范解析

### B.2.1 常见协议类型

#### RESP（Redis 序列化协议）

**请求格式**：
```
*<参数个数>\r\n
$<参数1长度>\r\n<参数1>\r\n
$<参数2长度>\r\n<参数2>\r\n
...
```

**示例**：
```
SET foo bar
→ *3\r\n$3\r\nSET\r\n$3\r\nfoo\r\n$3\r\nbar\r\n

GET foo
→ *2\r\n$3\r\nGET\r\n$3\r\nfoo\r\n
```

**响应格式**：
```
+PONG\r\n      # 简单字符串
$3\r\nbar\r\n  # 批量字符串
:1000\r\n      # 整数
*3\r\n...\r\n  # 数组
-ERR\r\n      # 错误
```

#### HTTP 协议

**请求格式**：
```
GET /api/users HTTP/1.1\r\n
Host: localhost:8080\r\n
\r\n
```

**响应格式**：
```
HTTP/1.1 200 OK\r\n
Content-Type: application/json\r\n
Content-Length: 27\r\n
\r\n
{"id": 1, "name": "foo"}
```

### B.2.2 协议字段分析

| 字段 | 类型 | 说明 |
|------|------|------|
| Magic | 4 bytes | 协议标识 |
| Length | 4 bytes | 载荷长度 |
| Type | 1 byte | 消息类型 |
| Sequence | 4 bytes | 序列号 |
| Payload | variable | 实际数据 |

---

## B.3 协议解析流程

### B.3.1 解析器架构

```
网络缓冲区
    │
    ▼
recv() → 输入缓冲区
    │
    ▼
协议解析器（状态机）
    │
    ├─→ 状态 1：解析头部
    ├─→ 状态 2：解析长度
    └─→ 状态 3：解析数据
    │
    ▼
完整消息对象
    │
    ▼
业务逻辑处理
```

### B.3.2 关键解析代码位置

| 文件 | 说明 |
|------|------|
| `networking.c` | `readQueryFromClient()` 读取数据 |
| `networking.c` | `processInputBuffer()` 解析协议 |
| `read.c` (hiredis) | `redisReaderFeed()` 接收数据 |
| `read.c` (hiredis) | `redisReaderGetReply()` 提取消息 |

---

## B.4 协议调试技巧

### B.4.1 使用 netcat 手动发送

**Redis RESP**：
```bash
nc localhost 6379
# 输入（每行后按回车）：
*1\r\n$4\r\nPING\r\n
# 响应：+PONG

*3\r\n$3\r\nSET\r\n$3\r\nfoo\r\n$3\r\nbar\r\n
# 响应：+OK
```

### B.4.2 使用 xxd 查看二进制

```bash
echo -e '*1\r\n$4\r\nPING\r\n' | xxd

# 输出：
00000000: 2a31 0d0a 2434 0d0a 5049 4e47 0d0a       *1..$4..PING..
```

### B.4.3 Wireshark/tcpdump 抓包

**tcpdump**：
```bash
sudo tcpdump -i lo0 -A 'tcp port 6379' -c 10
```

**Wireshark 过滤**：
```
tcp.port == 6379
```

### B.4.4 Python 解析 RESP

```python
import socket

def parse_resp(sock):
    data = sock.recv(1024)
    if data.startswith(b'+'):
        return data.decode()[1:].strip()
    elif data.startswith(b'$'):
        lines = data.split(b'\r\n')
        return lines[1].decode()
    elif data.startswith(b':'):
        return int(data.decode()[1:].strip())

sock = socket.socket()
sock.connect(('localhost', 6379))

# 发送 PING
sock.send(b'*1\r\n$4\r\nPING\r\n')
print("PING:", parse_resp(sock))
```

---

## B.5 协议快速参考

### B.5.1 RESP 协议速查

| 类型标识 | 名称 | 格式 |
|---------|------|------|
| `+` | 简单字符串 | `+<text>\r\n` |
| `-` | 错误 | `-<error>\r\n` |
| `:` | 整数 | `:<num>\r\n` |
| `$` | 批量字符串 | `$<len>\r\n<data>\r\n` |
| `*` | 数组 | `*<count>\r\n...` |
| `_` | 空值 | `_\r\n` (RESP3) |

### B.5.2 常用命令协议格式

```
SET foo bar    → *3\r\n$3\r\nSET\r\n$3\r\nfoo\r\n$3\r\nbar\r\n
GET foo        → *2\r\n$3\r\nGET\r\n$3\r\nfoo\r\n
DEL foo bar   → *3\r\n$3\r\nDEL\r\n$3\r\nfoo\r\n$3\r\nbar\r\n
```
