# 实战 D：框架 CLI 系统调试参考

**作用**：掌握框架内置 CLI 系统的调试与分析技能，适用于 Python/Go/Java/Node.js 等生态

---

## A. 框架 CLI 快速参考

### A.1 通用命令入口

| 语言/框架 | CLI 入口命令 | 查看帮助 |
|---------|------------|---------|
| Python/Flask | `flask [command]` | `flask --help` |
| Python/Django | `python manage.py [command]` | `python manage.py help` |
| Go/Cobra | `[binary] [command]` | `[binary] --help` |
| Java/Spring | `./mvnw spring:run` / `java -jar app.jar` | `--help` |
| Node.js/Express | `node bin/www` / `npm start` | `-h` / `--help` |

### A.2 验证上下文可用

**Python 框架（Flask）**：

```python
# 在 flask shell 中验证
>>> from flask import current_app, g, request
>>> current_app.name       # 验证 AppContext
>>> g.user = 'test'        # 验证 g 对象
```

**Go 框架（Gin）**：

```go
// 在 gin.Default() 中，默认已注入 Logger 和 Recovery 中间件
r := gin.Default()
// 验证中间件链：Logger → Recovery → Handler
```

**Java/Spring Boot**：

```bash
# 验证 Spring Boot 应用上下文
./mvnw spring-boot:run
# 或
java -jar target/app.jar
```

---

## B. CLI 源码调试技巧

### B.1 追踪命令注册流程

**Python/Click（Flask CLI）**：

```python
# 在 Click Group 中注入注册日志
import click

orig_command = click.Group.command

def traced_command(self, name=None, **kwargs):
    print(f"[DEBUG] Registering command: {name or kwargs.get('cmd_name')}", file=sys.stderr)
    return orig_command(self, name, **kwargs)

click.Group.command = traced_command
```

**Go/Cobra**：

```go
// 在 cobra 初始化时打印命令树
rootCmd.AddCommand(subCmd)
fmt.Printf("[DEBUG] Registered command: %s\n", subCmd.Name())

// 或遍历现有命令
rootCmd.Commands() // 返回所有子命令
```

### B.2 追踪上下文推送/弹出

**Python（Flask AppContext）**：

```python
# 在 AppContext.push() 中注入日志
import sys
from flask import Flask

orig_push = Flask.app_context.__enter__

def traced_push(self):
    print(f"[DEBUG] AppContext.push(), app={self.app}", file=sys.stderr)
    return orig_push(self)

Flask.app_context.__enter__ = traced_push
```

**Go（Gin Context）**：

```go
// 中间件中追踪请求上下文
func LoggerMiddleware() gin.HandlerFunc {
    return func(c *gin.Context) {
        fmt.Printf("[DEBUG] Request: %s %s\n", c.Request.Method, c.Request.URL.Path)
        c.Next()
        fmt.Printf("[DEBUG] Response: %d\n", c.Writer.Status())
    }
}
```

### B.3 验证上下文注入行为

```python
# 测试命令是否正确注入上下文
from flask import Flask, current_app

app = Flask(__name__)

@app.cli.command('test-context')
def test_context():
    print(f"App name: {current_app.name}")  # 验证 current_app 可用
    print(f"App object: {current_app._get_current_object()}")  # 验证非代理对象

# 在 CLI 外手动验证
with app.app_context():
    print(f"Inside context: {current_app.name}")  # OK
# 离开 with 块后，current_app 不可用
```

---

## C. 命令注册调试

### C.1 查看所有注册命令

**Python/Click**：

```python
from flask import Flask
app = Flask(__name__)

# 列出所有顶级命令
print("Top-level commands:", list(app.cli.commands.keys()))

# 列出命令组中的子命令
for name, cmd in app.cli.group_commands.items():
    print(f"  {name}: {cmd}")
```

**Go/Cobra**：

```go
// 打印命令树
func printCommandTree(cmd *cobra.Command, indent int) {
    prefix := strings.Repeat("  ", indent)
    fmt.Printf("%s- %s: %s\n", prefix, cmd.Name(), cmd.Short)
    for _, sub := range cmd.Commands() {
        printCommandTree(sub, indent+1)
    }
}
```

**Java/Spring Shell**：

```bash
# Spring Shell 默认命令
help
stacktrace
quit/exit
# 自定义命令需要注册 @ShellComponent bean
```

### C.2 追踪命令执行路径

**Python**：

```python
# 在命令函数入口加断点
@app.cli.command('init-db')
def init_db():
    import pdb; pdb.set_trace()  # 断点
    # ... 命令逻辑
```

**Go**：

```go
// 在 cobra RunE 中加日志
var initDbCmd = &cobra.Command{
    Use:   "init-db",
    RunE: func(cmd *cobra.Command, args []string) error {
        fmt.Println("[DEBUG] init-db called")
        return initDatabase()
    },
}
```

### C.3 分析扩展点触发

**Python/Flask 信号**：

```python
from flask import signals

def my_handler(sender, **kwargs):
    print(f"[SIGNAL] {sender}, kwargs={kwargs}")

signals.request_started.connect(my_handler)
signals.request_finished.connect(my_handler)
signals.got_request_exception.connect(my_handler)
```

---

## D. 常见 CLI 问题排查

### D.1 "找不到应用实例" 排查

```bash
# 问题：CLI 找不到应用程序实例
# 原因：
#   1. 环境变量未设置（FLASK_APP / SPRING_APPLICATION_NAME）
#   2. --app / --factory 参数格式错误
#   3. 工厂函数需要参数但未提供
#   4. 自动搜索失败（无标准文件名）

# 排查步骤
flask --app myapp run --debug         # 指定 app
flask --app "myapp:create_app()" run  # 指定工厂函数
```

### D.2 "Working outside of application context"

```python
# 问题：current_app / current_request 不可用
# 原因：在 CLI 命令中使用了需要上下文的代码，但未推送上下文

# 错误示例：直接访问 current_app
@app.cli.command('broken')
def broken():
    print(current_app.name)  # 报错：Working outside of application context.

# 正确示例：使用 @with_appcontext 或手动推送
@app.cli.command('works')
def works():
    with app.app_context():  # 手动推送
        print(current_app.name)  # 正常
```

### D.3 命令未出现

```bash
# 问题：注册的命令不显示在帮助中
# 原因：
#   1. 命令在蓝图注册之前注册（Flask）
#   2. 插件的 entry_points 未正确配置
#   3. 命令注册函数未被调用

# 排查
flask --app myapp --help  # 查看所有可用命令
flask routes              # 列出所有路由

# 在 Python 中检查
from myapp import app
print(list(app.cli.commands.keys()))
```

### D.4 上下文泄漏

```python
# 问题：CLI 命令中的上下文状态污染了后续命令
# 原因：ctx.push() 后未正确 pop()

# 排查：追踪 push/pop 配对
def traced_push(self):
    print(f"[DEBUG] AppContext.push()", file=sys.stderr)
    return super().push()

def traced_pop(self, exc=None):
    print(f"[DEBUG] AppContext.pop(), exc={exc}", file=sys.stderr)
    return super().pop(exc=exc)
```

---

## E. 快速参考

```bash
# ========== 框架诊断命令 ==========
flask --app myapp --help          # 列出所有命令
flask --app myapp routes           # 列出所有路由
python manage.py help              # Django 命令帮助

# ========== Python 调试 ==========
python -c "from myapp import app; print(list(app.cli.commands.keys()))"

# ========== 追踪上下文 ==========
python -c "
from myapp import app
with app.app_context():
    print('Context pushed OK')
"

# ========== Go Cobra ==========
go run main.go --help              # 查看命令树
go run main.go [command] --help   # 查看子命令帮助

# ========== Java Spring ==========
./mvnw spring-boot:run --help     # Spring Boot CLI
java -jar app.jar --help           # JAR 帮助

# ========== 验证信号 ==========
python -c "
from myapp import app
from flask import signals

def handler(sender, **kwargs):
    print(f'Signal received from {sender}')
signals.request_started.connect(handler)

with app.test_request_context():
    pass  # 触发信号
"
```

---

**注意**：
- 本模块聚焦框架 CLI 系统的调试，不绑定特定框架
- 核心调试思想：追踪"命令注册 → 上下文注入 → 命令执行 → 清理"的完整生命周期
- 对于 Go/Java 项目，关注 DI 容器和中间件链；对于 Python 项目，关注 AppContext 和信号系统
