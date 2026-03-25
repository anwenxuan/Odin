# 14 — 配置与默认安全分析器

**作用**：分析项目配置文件和默认安全设置，识别不安全默认配置和配置注入风险

**输入**：配置文件 + 默认配置代码 + 命令行参数处理代码

**输出**：配置安全问题清单 + 风险评估 + 安全加固建议

---

你是一个安全研究员。

## 输入材料

**配置文件**：
```
{config_file}
```

**默认配置代码**：
```
{default_config_code}
```

**命令行参数处理**：
```
{cli_args_handling}
```

## 分析任务

### 1. 默认安全配置分析

| 配置项 | 默认值 | 实际行为 | 安全风险 | 建议 |
|--------|--------|---------|---------|------|
| requirepass | (空) | 无密码认证 | 严重 | 设置强密码 |
| bind | 0.0.0.0 | 允许外部访问 | 严重 | 改为 127.0.0.1 |
| protected-mode | yes/no | | | |
| daemonize | no | | | |
| dir | ./ | | | |
| logfile | "" | | | |
| maxmemory | 0 | 无限制 | 高 | 设置合理限制 |

### 2. 危险命令分析

| 命令 | 默认状态 | 危险操作 | 建议 |
|------|---------|---------|------|
| FLUSHALL | 可执行 | 清空所有数据 | 重命名为空或禁用 |
| FLUSHDB | 可执行 | 清空当前数据库 | 重命名 |
| CONFIG | 可执行 | 修改任意配置 | 重命名 |
| DEBUG | 可执行 | 调试命令，可崩溃服务 | 禁用 |
| SHUTDOWN | 可执行 | 关闭服务器 | 重命名 |
| KEYS | 可执行 | 全局扫描，阻塞 | 使用 SCAN |
| BGSAVE | 可执行 | 后台保存 | 限制调用 |
| SLAVEOF | 可执行 | 变成从服务器 | 禁用 |

### 3. 配置注入风险分析

**CONFIG SET 注入**：

```c
// 漏洞：CONFIG SET 可修改任意配置
// 攻击步骤：
// 1. CONFIG SET dir /tmp
// 2. CONFIG SET dbfilename evil.rdb
// 3. SAVE → 写任意内容到 /tmp/evil.rdb
// 4. 利用计划任务 /tmp/evil.rdb 中的 Lua/Shell 脚本
```

| 配置注入路径 | 目标效果 | 利用难度 | 严重程度 |
|-------------|---------|---------|---------|
| dir + dbfilename | 写任意文件 | 低 | 严重 |
| rdbcompression no | 写明文 RDB | 低 | 中 |
| appendonly yes + appendfsync | DoS | 低 | 高 |
| maxmemory 0 | 内存耗尽 | 低 | 中 |

### 4. 文件系统安全分析

| 检查项 | 默认值 | 评估 | 风险 |
|--------|--------|------|------|
| 持久化文件权限 | 644 | 宽泛 | 其他用户可读 |
| 日志文件权限 | 644 | 宽泛 | 可能泄露敏感信息 |
| Unix Socket 权限 | 755 | 宽泛 | 其他用户可连接 |
| pidfile 位置 | /var/run/ | 需创建 | 目录可能不存在 |
| 工作目录 | ./ | 相对路径 | 依赖启动位置 |

### 5. 网络安全配置

| 配置项 | 默认值 | 评估 | 安全建议 |
|--------|--------|------|---------|
| port | 6379 | 标准端口 | 非标准端口减少扫描 |
| bind | 0.0.0.0 | 任意接口 | 仅监听必要接口 |
| tcp-backlog | 511 | 合理 | | |
| timeout | 0 | 无超时 | 设置合理超时 |
| tcp-keepalive | 300 | 较长 | | |
| protected-mode | yes/no | | 启用或绑定本地 |

### 6. 内存与资源限制

| 配置项 | 默认值 | 评估 | 风险 |
|--------|--------|------|------|
| maxclients | 10000 | 合理 | 连接耗尽 |
| maxmemory | 0 (无限制) | 危险 | OOM |
| maxmemory-policy | noeviction | 合理 | | |
| maxmemory-samples | 5 | 合理 | | |
| client-output-buffer-limit | 有默认值 | 合理 | |

### 7. 配置安全问题汇总

| ID | 配置项 | 默认值 | 风险 | 严重程度 | 建议 |
|----|--------|--------|------|---------|------|
| CFG-01 | requirepass | 空 | 无认证 | 严重 | 设置强密码 |
| CFG-02 | bind | 0.0.0.0 | 外部访问 | 严重 | 改为本地 |
| CFG-03 | protected-mode | yes | 取决于 bind | 高 | 确保 bind 本地 |
| CFG-04 | FLUSHALL | 可执行 | 数据清空 | 高 | 重命名 |
| CFG-05 | CONFIG | 可执行 | 配置注入 | 高 | 重命名 |
| ... | | | | | |

### 8. 安全加固配置示例

```conf
# ========== 安全加固配置 ==========

# 认证
requirepass <强随机密码，32字符以上>

# 网络
bind 127.0.0.1
port 6380
protected-mode yes
tcp-backlog 511
timeout 300
tcp-keepalive 300

# 危险命令重命名
rename-command FLUSHALL ""
rename-command FLUSHDB ""
rename-command CONFIG "CONFIG_a8b9c0d1e2f3"
rename-command DEBUG ""

# 资源限制
maxmemory 2gb
maxmemory-policy allkeys-lru
maxclients 5000

# 持久化
dir /var/lib/redis
dbfilename dump.rdb
rdbcompression yes
rdbchecksum yes
appendonly yes
appendfsync everysec

# 日志
loglevel notice
logfile /var/log/redis/redis.log

# 安全
slowlog-log-slower-than 10000
slowlog-max-len 128
```

---

**注意**：使用中文输出，重点识别配置层面的安全风险
