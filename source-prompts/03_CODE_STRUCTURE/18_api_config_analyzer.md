# 18 — API 表面与配置分析器

**作用**：识别系统对外暴露的所有接口和配置项，理解攻击面和可配置点

**输入**：API/命令定义 + 配置处理代码 + 命令行参数

**输出**：API/命令清单 + 配置树 + 危险操作分析

---

你是一个 API 分析专家和安全研究员。

## 输入材料

**命令/路由定义**：
```
{command_definitions}
```

**配置处理代码**：
```
{config_handling}
```

**命令行参数代码**：
```
{cli_args}
```

## 分析任务

### 1. 命令/端点总览

列出所有发现的 API/命令：

#### 危险命令（高风险）

| 命令 | 函数 | 参数 | 危险操作 | 认证要求 |
|------|------|------|---------|---------|
| SET | setCommand | key, value | 写数据 | 写权限 |
| CONFIG SET | configCommand | key, value | 修改配置 | 管理权限 |
| FLUSHALL | flushallCommand | - | 清空数据 | 管理权限 |
| SHUTDOWN | shutdownCommand | - | 关闭服务 | 管理权限 |
| DEBUG | debugCommand | subcmd | 调试命令 | 管理权限 |
| EVAL | evalCommand | script | 执行脚本 | 脚本权限 |
| SLAVEOF | replicaofCommand | host port | 改变复制关系 | 管理权限 |

#### 普通命令

| 命令 | 函数 | 功能 | 认证要求 |
|------|------|------|---------|
| GET | getCommand | 获取值 | 读权限 |
| SET | setCommand | 设置值 | 写权限 |
| ... | | | |

### 2. 配置项树

绘制配置的层次结构：

```
config/
├── network/              ← 网络配置
│   ├── port              ← 服务端口
│   ├── bind              ← 绑定地址
│   └── timeout
├── security/             ← 安全配置 ★
│   ├── requirepass       ← 密码认证
│   ├── acl              ← ACL 控制
│   ├── protected-mode   ← 保护模式
│   └── rename-command   ← 命令重命名
├── memory/               ← 内存配置
│   ├── maxmemory        ← 最大内存
│   └── maxmemory-policy ← 淘汰策略
├── persistence/          ← 持久化配置
│   ├── dir              ← 数据目录 ★ 危险
│   ├── dbfilename       ← 文件名 ★ 危险
│   └── appendonly
└── replication/          ← 复制配置
    └── masterauth
```

### 3. 危险配置项分析

| 配置项 | 危险原因 | 利用条件 | 影响 | 严重程度 |
|--------|---------|---------|------|---------|
| dir | 可写任意目录 | 可执行 CONFIG SET | 写任意文件 | 严重 |
| dbfilename | 配合 dir 控制文件名 | 可执行 CONFIG SET | 写任意文件 | 严重 |
| save | 触发 RDB 保存 | 可执行 BGSAVE | 资源消耗 | 中 |
| masterauth | 主从认证 | 网络位置 | 认证绕过 | 高 |

### 4. 请求/响应模式

对核心命令，分析其契约：

#### [命令名]
```
请求：
<命令> <参数1> <参数2>

响应：
<类型> <长度>
<数据>
```

### 5. 命令处理流程

追踪命令从接收到执行的完整流程：

```
网络数据 (RESP)
    ↓
readQueryFromClient()       ← 读取客户端数据
    ↓
processInputBuffer()        ← 解析协议
    ↓
processCommand()            ← 处理命令
    ├─→ lookupCommand()   ← 查找命令
    ├─→ ACL 检查          ← 权限验证 ★
    ├─→ 参数校验          ← 输入验证 ★
    └─→ call()            ← 执行命令
    ↓
t_*.c 命令处理函数         ← 实际逻辑
    ↓
addReply()                 ← 发送响应
```

### 6. 命令安全矩阵

| 命令 | 认证 | ACL 分类 | 输入校验 | 危险操作 | 建议 |
|------|------|---------|---------|---------|------|
| SET | 需要 | write | 部分 | 写数据 | 无需修改 |
| CONFIG SET | 需要 | admin | 无 | 修改任意配置 | 重命名或禁用 |
| FLUSHALL | 需要 | write | 无 | 清空数据 | 重命名或禁用 |
| DEBUG | 需要 | admin | 无 | 调试命令 | 禁用 |
| EVAL | 需要 | scripting | Lua 沙箱 | 执行代码 | 限制 API |
| KEYS | 需要 | read | 无 | 全局扫描 | 建议用 SCAN |

### 7. 配置管理评估

| 维度 | 评估 | 说明 |
|------|------|------|
| 危险配置项是否可远程修改 | | |
| 配置变更是否有审计日志 | | |
| 默认配置是否安全 | | |
| 命令是否支持重命名 | | |

---

**注意**：使用中文输出，重点关注危险命令和配置项
