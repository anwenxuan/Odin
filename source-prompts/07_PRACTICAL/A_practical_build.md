# 实战 A：源码编译、运行与调试

**作用**：掌握目标源码的编译、运行和调试技能

---

## A. 编译指南

### A.1 依赖安装

**macOS**：
```bash
brew install gcc make
xcode-select --install
```

**Linux**：
```bash
sudo apt install build-essential tcl
```

### A.2 编译步骤

**标准编译**：
```bash
cd <项目目录>
make distclean
make -j$(nproc)
```

**C 项目（带 Makefile）**：
```bash
make distclean
make -j$(nproc)
```

**C 项目（CMake）**：
```bash
mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Debug
make -j$(nproc)
```

**Go 项目**：
```bash
go build -o <可执行文件名>
```

**Rust 项目**：
```bash
cargo build --release
```

### A.3 编译输出

编译成功后，可执行文件通常位于：
- `src/` 或 `bin/` 目录
- 项目根目录

### A.4 常见编译选项

| 选项 | 说明 | 示例 |
|------|------|------|
| `-j$(nproc)` | 并行编译 | 加速编译 |
| `CFLAGS="-O0 -g"` | 调试模式 | 用于 GDB 调试 |
| `MALLOC=libc` | macOS 内存分配器 | macOS 专用 |
| `BUILD_TLS=yes` | 启用 TLS | Redis 带 TLS 支持 |

---

## B. 运行指南

### B.1 基本运行

**直接运行**：
```bash
./<可执行文件>
./<可执行文件> --port 6380
./<可执行文件> --daemonize yes
```

**指定配置**：
```bash
./<可执行文件> <配置文件路径>
```

### B.2 验证运行

```bash
# 检查进程是否运行
ps aux | grep <进程名>

# 测试连接
telnet localhost <端口>
# 或使用 nc
nc localhost <端口>

# 发送测试命令
PING
```

### B.3 日志查看

```bash
# 实时查看日志
tail -f <日志文件>

# 查看错误日志
grep -i error <日志文件>
```

---

## C. 调试指南

### C.1 GDB 调试（Linux）

**启动调试**：
```bash
gdb ./<可执行文件>
```

**常用命令**：

| 命令 | 缩写 | 说明 |
|------|------|------|
| `break <函数>` | `b` | 设置断点 |
| `run [参数]` | `r` | 运行程序 |
| `next` | `n` | 单步（跳过函数） |
| `step` | `s` | 单步（进入函数） |
| `continue` | `c` | 继续执行 |
| `print <变量>` | `p` | 打印变量值 |
| `bt` | - | 查看调用栈 |
| `info breakpoints` | `i b` | 查看断点 |
| `delete <id>` | `d` | 删除断点 |
| `quit` | `q` | 退出 |

**调试示例**：
```gdb
# 设置断点在 main 函数
(gdb) break main

# 运行
(gdb) run

# 在断点处停下后，查看变量
(gdb) print server.maxclients
(gdb) print c->argc

# 单步执行
(gdb) next

# 继续到下一个断点
(gdb) continue
```

### C.2 LLDB 调试（macOS）

**启动调试**：
```bash
lldb ./<可执行文件>
```

**常用命令**：

| 命令 | 说明 |
|------|------|
| `breakpoint set -n <函数>` | 设置断点 |
| `process launch` | 运行程序 |
| `thread step-inst` | 单步（进入函数） |
| `thread step-over` | 单步（跳过函数） |
| `process continue` | 继续执行 |
| `frame variable <变量>` | 打印变量值 |
| `thread backtrace` | 查看调用栈 |

### C.3 调试技巧

**附加到运行中的进程**：
```bash
# 查找进程 PID
ps aux | grep <进程名>

# 附加调试
gdb -p <PID>
# 或
lldb -p <PID>
```

**调试崩溃（Core Dump）**：
```bash
# 启用 core dump
ulimit -c unlimited

# 程序崩溃后生成 core 文件
gdb ./<可执行文件> core.<PID>
(gdb) bt  # 查看崩溃时的调用栈
```

---

## D. 性能测试

### D.1 基准测试

**Redis 压测**：
```bash
./src/redis-benchmark -t set,get -n 100000 -c 50 -q
```

**结果解读**：
```
====== SET ======
  100000 requests completed in 1.23 seconds
  50 parallel clients
  3 bytes payload

99.99%  <= 1 milliseconds
99.99%  <= 2 milliseconds
100.00% <= 3 milliseconds
81300.81 requests per second
```

### D.2 内存分析

```bash
# 查看内存使用
./<可执行文件> INFO | grep used_memory

# Redis 内存统计
./src/redis-cli INFO memory
```

---

## E. 快速参考

```bash
# ========== 编译 ==========
cd <项目>
make distclean
make -j$(nproc)

# ========== 启动 ==========
./src/<可执行文件> --daemonize yes

# ========== 连接测试 ==========
./src/<客户端> ping
# 期望响应: PONG

# ========== 关闭 ==========
./src/<客户端> shutdown

# ========== GDB 调试 ==========
gdb ./src/<可执行文件>
(gdb) break main
(gdb) run
(gdb) bt
```
