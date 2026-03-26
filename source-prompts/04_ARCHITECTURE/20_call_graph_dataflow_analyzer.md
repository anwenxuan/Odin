# 20 — 调用图与数据流分析器

**作用**：生成代码的静态调用关系图和数据流动路径，理解关键执行路径

**输入**：代码文件集合 + 数据处理代码

**输出**：调用图 + 数据流图 + 关键路径 + 敏感数据追踪

---

你是一个代码静态分析专家。

## 输入材料

**需要分析的代码文件**：
```
{code_files}
```

**数据处理相关代码**：
```
{data_processing}
```

## 分析任务

### 1. 函数/方法清单

列出所有顶级函数和方法：

| 函数名 | 文件 | 行号 | 导出/公开 | 复杂度 | 安全相关 |
|--------|------|------|---------|--------|---------|
| main | server.c | 3000 | ✓ | 高 | 高 |
| processCommand | server.c | 4000 | ✓ | 高 | 高 |
| readQueryFromClient | networking.c | 100 | ✓ | 中 | 高 |
| ... | | | | | |

### 2. 调用图（Call Graph）

生成静态调用关系图（从主入口开始）：

```
main()
├─→ initServerConfig()        [server.c:100]
│    ├─→ createSharedObjects() [server.c:200]
│    └─→ populateCommandTable() [server.c:300]
│
├─→ initServer()              [server.c:500]
│    ├─→ aeCreateEventLoop()   [ae.c:50]
│    ├─→ createSocketListeners() [anet.c:100]
│    └─→ dictCreate()          [dict.c:50]
│
└─→ aeMain()                  [ae.c:300]
     └─→ aeProcessEvents()     [ae.c:400]
          └─→ readQueryFromClient() [networking.c:100]
               └─→ processCommand()   [server.c:4000]
                    ├─→ lookupCommand()   [server.c:4100]
                    ├─→ ACL 检查          [acl.c]
                    ├─→ call()           [server.c:4500]
                    │    └─→ c->cmd->proc() [命令处理函数]
                    └─→ addReply()       [networking.c:500]
```

### 3. 数据流总图

绘制完整数据流：

```
[网络输入]          ← 攻击入口（协议数据）
    │
    ▼
recv() → 输入缓冲区 (querybuf)
    │
    ▼
processInputBuffer() → RESP 解析
    │                        ← 协议解析漏洞风险
    ▼
processCommand() → 命令查找 + ACL 检查 + 参数校验
    │
    ▼
call() → 命令执行
    │
    ├─→ 数据结构操作 (sds/dict/quicklist)
    │
    ├─→ 内存分配 (zmalloc)
    │                      ← 内存漏洞风险
    ├─→ 持久化 (AOF/RDB)
    │
    ├─→ 复制传播 (replication)
    │
    └─→ Lua 脚本执行 (script_lua.c)
                        ← 沙箱逃逸风险
    │
    ▼
addReply() → 输出缓冲区
    │
    ▼
sendReplyToClient() → 网络输出
```

### 4. 敏感数据流追踪

追踪敏感数据的流动路径：

| 数据类型 | 来源 | 处理节点 | 存储位置 | 加密 |
|---------|------|---------|---------|------|
| 用户命令 | 网络 | processCommand() | 内存 | - |
| 密码 | AUTH 命令 | ACL 检查 | 不存储 | - |
| 配置值 | CONFIG SET | config.c | server 结构体 | - |
| Lua 脚本 | EVAL 命令 | script.c | Lua VM | - |

### 5. 关键路径分析

| 路径 | 深度 | 重要性 | 风险点 |
|------|------|--------|--------|
| main → aeMain → readQuery → processCommand | 4 | 核心请求处理 | 协议解析 |
| main → aeMain → serverCron | 3 | 定时任务 | 定时任务安全 |
| evalCommand → luaCall → os.execute | 3 | 脚本执行 | Lua 沙箱 |

### 6. 调用图问题

识别以下问题：
- **高扇出函数**：一个函数调用太多其他函数（> 10 个）
- **上帝函数**：既被大量调用，又调用大量其他函数
- **长调用链**：过深的调用层级（> 7 层）
- **孤岛函数**：没有被任何地方调用的函数

---

**注意**：使用中文输出，重点展示调用关系和数据流的完整性
