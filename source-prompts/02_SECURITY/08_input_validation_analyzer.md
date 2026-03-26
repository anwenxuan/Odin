# 08 — 输入验证分析器

**作用**：深入分析项目对输入数据的校验机制，识别各类语言的输入验证缺陷

**输入**：协议解析代码 + 命令处理代码 + 输入校验函数

**输出**：输入验证覆盖图 + 验证缺陷清单 + 漏洞触发条件

---

你是一个安全研究员和漏洞猎人。

## 语言类型识别

**请先判断项目使用的语言类型**：

| 语言类型 | 典型输入安全缺陷 |
|---------|----------------|
| **C/C++** | 缓冲区溢出、格式化字符串、整数溢出 |
| **Java/Kotlin** | 反序列化、路径穿越、正则 DoS、XML 注入 |
| **Go** | 路径穿越、正则 DoS、模板注入、SQL 注入 |
| **混合** | 按语言分别分析 |

---

## 输入材料

**协议解析代码**：
```
{protocol_parser}
```

**命令参数处理代码**：
```
{command_handlers}
```

**输入校验函数**：
```
{validation_functions}
```

**数据结构定义**：
```
{data_structures}
```

---

## 分析任务

### 1. 输入验证总览

追踪所有外部输入的来源和处理路径：

```
[外部输入]
     │
     ├─→ 网络数据 (TCP/Unix Socket)
     │       └─→ recv() → 输入缓冲区 → 协议解析器
     │
     ├─→ 命令行参数 (argc/argv)
     │       └─→ 参数解析 → 命令分发
     │
     ├─→ 配置文件
     │       └─→ 配置解析 → 全局设置
     │
     ├─→ HTTP 请求参数
     │       └─→ 路由 → Controller → 参数校验
     │
     ├─→ 文件上传
     │       └─→ 文件名/内容校验
     │
     └─→ Lua/JS 脚本参数
             └─→ 脚本解析 → 函数参数
```

---

### 2. 协议解析安全性分析

**对每个协议/数据格式解析器，分析其安全性**：

| 解析器 | 输入点 | 语言 | 验证类型 | 是否验证 | 验证位置 | 缺陷风险 |
|--------|--------|------|---------|---------|---------|---------|
| RESP 解析 | readQueryFromClient() | C | 长度边界 | 部分 | processMultibulkBuffer() | 超长参数 |
| JSON 解析 | JSON.parse() | Java/Go | 类型/边界 | 部分 | 字段访问 | 反序列化 |
| XML 解析 | DocumentBuilder | Java | XXE | 无 | parse() | XXE |
| URL 解析 | url.parse() | JS/Go | 特殊字符 | 无 | - | 协议跳转 |
| 路径解析 | os.Open() | Go | .. 穿越 | 无 | - | 目录穿越 |

---

### 3. 语言相关的输入缺陷模式

#### C/C++ 常见缺陷

```c
// 缺陷 1：缓冲区溢出
char buf[256];
strcpy(buf, input);  // 无长度检查 ← 漏洞

// 缺陷 2：格式化字符串
printf(user_input);  // 用户输入作为格式串 ← 漏洞

// 缺陷 3：整数溢出
size_t len = strlen(input);
char *buf = malloc(len + 1);  // len 可能为负或超大 ← 漏洞

// 缺陷 4：use-after-free
free(ptr);
process(ptr);  // ← 漏洞

// 缺陷 5：双 free
free(ptr);
free(ptr);  // ← 漏洞
```

#### Java 常见缺陷

```java
// 缺陷 1：反序列化漏洞
ObjectInputStream ois = new ObjectInputStream(input);
Object obj = ois.readObject();  // ← 漏洞：信任任意序列化数据

// 缺陷 2：路径穿越
String path = request.getParameter("file");
File file = new File(BASE_DIR, path);
file.getCanonicalPath();  // ← 需检查是否在 BASE_DIR 内

// 缺陷 3：XML 外部实体 (XXE)
DocumentBuilderFactory dbf = DocumentBuilderFactory.newInstance();
dbf.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);  // ← 需显式设置

// 缺陷 4：正则 DoS (ReDoS)
Pattern.compile(userRegex);  // ← 嵌套量词正则可导致 ReDoS

// 缺陷 5：SpEL 注入
SpelExpressionParser parser = new SpelExpressionParser();
parser.parseExpression(userInput);  // ← 漏洞：用户输入进 SpEL
```

#### Go 常见缺陷

```go
// 缺陷 1：路径穿越
path := r.FormValue("file")
data, err := os.ReadFile(path)  // ← 需检查 filepath.Clean(path) 是否在允许目录内

// 缺陷 2：正则 DoS
re := regexp.MustCompile(userRegex)  // ← 嵌套量词正则可导致 ReDoS

// 缺陷 3：模板注入
tmpl.Execute(w, userInput)  // ← 需使用 template.HTMLEscaper

// 缺陷 4：SQL 注入
query := "SELECT * FROM users WHERE id=" + userID  // ← 漏洞：字符串拼接

// 缺陷 5：URL 重定向
redirectUrl := r.URL.Query().Get("redirect")
http.Redirect(w, r, redirectUrl)  // ← 需验证 redirectUrl 为内部地址
```

---

### 4. 特殊字符处理分析

| 输入类型 | 语言 | 特殊字符 | 处理方式 | 安全风险 |
|---------|------|---------|---------|---------|
| 命令名 | C | 换行符 \r\n | splitlines() | 命令注入 |
| 字符串值 | C | \x00 (空字节) | 保留处理 | 空字节截断 |
| 路径参数 | Java/Go | .. / | 路径规范化 | 目录穿越 |
| 序列化数据 | Java | 任意对象 | readObject() | 反序列化漏洞 |
| 正则表达式 | Go/Java | 嵌套量词 | 直接编译 | ReDoS |
| SQL 参数 | Go/Java | ' " ; -- | 字符串拼接 | SQL 注入 |
| 模板数据 | Go | {{.}} | 直接输出 | 模板注入 |

---

### 5. 验证缺陷清单

| ID | 缺陷类型 | 语言 | 代码位置 | 触发条件 | 影响 | 严重程度 |
|----|---------|------|---------|---------|------|---------|
| IV-01 | 格式化字符串 | C | util.c:50 | `%s` 替换为用户输入 | 信息泄露/RCE | 严重 |
| IV-02 | 反序列化 | Java | deserialize.java:30 | readObject() 接收外部数据 | RCE | 严重 |
| IV-03 | 路径穿越 | Go | handler.go:40 | 路径参数含 `..` | 读任意文件 | 高 |
| IV-04 | SQL 注入 | Go/Java | query.go:20 | 字符串拼接 SQL | 数据泄露 | 严重 |
| IV-05 | XXE | Java | xmlparser.java:25 | XML 含外部实体 | 文件读取/RCE | 严重 |
| IV-06 | ReDoS | Java/Go | regex.go:30 | 嵌套量词正则 | DoS | 中 |
| IV-07 | 模板注入 | Go | template.go:20 | 用户输入进模板 | XSS/RCE | 高 |
| IV-08 | 整数溢出 | C | packet.c:30 | count * sizeof | 溢出 | 高 |
| IV-09 | URL 重定向 | Go | redirect.go:20 | 外部 URL 重定向 | 网络钓鱼 | 中 |
| ... | | | | | | |

---

### 6. 长度/数量限制分析

| 限制项 | 语言 | 限制值 | 位置 | 验证方式 | 绕过风险 |
|--------|------|--------|------|---------|---------|
| 单个 key 大小 | C | 512MB | server.h | checkMemory() | 低 |
| 请求参数长度 | Java | 可配置 | Filter | checkSize() | 中 |
| 文件上传大小 | Java/Go | 可配置 | Filter | checkSize() | 中 |
| 正则执行时间 | Go | 无限制 | regexp | - | 高（ReDoS）|
| 反序列化深度 | Java | 无限制 | ObjectInputStream | - | 高 |

---

### 7. 输入验证强化建议

**C/C++**：
```c
// 使用安全字符串函数
strncpy(buf, input, sizeof(buf) - 1);
buf[sizeof(buf) - 1] = '\0';

// 添加长度检查
if (strlen(input) >= sizeof(buf)) return ERROR;
```

**Java**：
```java
// 反序列化安全配置
ObjectInputStream ois = new CustomObjectInputStream(input);
ois.setExternalizerFilter(new ClassFilter() {
    public boolean validate(Class<?> clazz) {
        return ALLOWED_CLASSES.contains(clazz.getName());
    }
});

// 路径穿越防护
String path = req.getParameter("file");
File file = new File(BASE_DIR, path);
if (!file.getCanonicalPath().startsWith(BASE_DIR)) {
    throw new SecurityException("Path traversal detected");
}
```

**Go**：
```go
// 路径穿越防护
func safeOpen(path string) ([]byte, error) {
    fpath := filepath.Clean(path)
    if !strings.HasPrefix(fpath, allowedDir) {
        return nil, errors.New("forbidden")
    }
    return os.ReadFile(fpath)
}

// SQL 参数化查询
row := db.QueryRowContext(ctx,
    "SELECT * FROM users WHERE id = $1", userID)
```

---

**注意**：使用中文输出。分析时请在开头标注项目语言类型，系统会自动选择对应的缺陷模式。
