# 10 — 内存安全与运行时安全分析器

**作用**：深度分析内存安全（针对 C/C++）和运行时安全（针对 Java/Go）问题

**输入**：内存/运行时相关源代码 + 指针/引用操作代码 + JNI/CGO 边界代码

**输出**：内存/运行时安全问题清单 + 漏洞类型 + 利用条件 + PoC

---

你是一个安全研究员。

## 语言类型识别

**请先判断项目使用的语言类型，后续分析将根据语言类型调整重点**：

| 语言 | 安全分析重点 |
|------|------------|
| **C/C++** | 缓冲区溢出、UAF、double-free、整数溢出、格式化字符串 |
| **Java/Kotlin** | 反序列化漏洞、本地代码漏洞（JNI）、内存耗尽、反射滥用 |
| **Go** | nil pointer 解引用、goroutine 泄漏、CGO 边界、runtime panic |
| **混合（FFI/JNI/CGO）** | 语言边界安全、类型安全边界、内存所有权转移 |

---

## 输入材料

**内存/运行时相关代码**：
```
{memory_runtime_code}
```

**指针/引用操作代码**：
```
{pointer_reference_code}
```

**JNI/CGO 边界代码**：
```
{ffi_boundary_code}
```

**数据结构定义**：
```
{data_structures}
```

---

## C/C++ 分析模块

### 1. 内存操作审计

| 函数 | 使用位置 | 安全检查 | 风险 | 说明 |
|------|---------|---------|------|------|
| malloc() | server.c:100 | sizeof 乘法溢出？ | | |
| calloc() | - | 乘法溢出？ | | |
| realloc() | - | 原指针仍使用？ | | |
| strdup() | - | 无 | 高 | 无边界检查 |
| memcpy() | - | 无 | 高 | 需检查长度参数 |
| sprintf() | - | 无 | 极高 | 缓冲区溢出 |

### 2. 缓冲区溢出分析

```c
// 栈缓冲区溢出
void handle_request(char *input) {
    char buf[64];
    strcpy(buf, input);  // ← 无长度检查
}

// 堆缓冲区溢出
void parse_packet(char *data, size_t len) {
    char *buf = malloc(len + 1);
    memcpy(buf, data, len);  // ← len 可能被攻击者控制
}

// 格式化字符串
printf(user_input);  // ← 漏洞
```

### 3. UAF / Double-Free 分析

```c
// UAF 模式
free(ptr);
process(ptr);  // ← 使用已释放的指针

// Double-free
free(ptr);
free(ptr);  // ← 双重释放
```

---

## Java 分析模块

### 1. 本地代码漏洞（JNI）

```java
// JNI 不安全调用
public class NativeBridge {
    static {
        System.loadLibrary("native");  // 本地库
    }
    public native String process(String input);  // ← JNI 边界，信任返回

    // 潜在问题：本地库漏洞直接危害 JVM
}

// 分析重点：
// 1. 是否校验 JNI 返回的字符串长度？
// 2. JNI 回调 Java 代码时是否有类型检查？
// 3. 本地代码分配的内存是否正确释放？
```

### 2. 反射与 ClassLoader

```java
// 危险反射
Class<?> clazz = Class.forName(userInput);  // ← 用户输入控制类名
Method method = clazz.getMethod("exec");
method.invoke(null);

// 字节码加载
ClassLoader loader = new URLClassLoader(urls);
Class<?> clazz = loader.loadClass(userInput);  // ← 动态加载任意类
```

### 3. 反序列化漏洞

```java
// 危险反序列化
ObjectInputStream ois = new ObjectInputStream(input);
Object obj = ois.readObject();  // ← 信任任意序列化数据

// 不安全的 JMX
MBeanServer mbs = MBeanServerFactory.createMBeanServer();
mbs.createMBean("javax.management.modelmbean.RequiredModelMBean", objectName);
// 攻击：构造恶意 ObjectName
```

---

## Go 分析模块

### 1. Nil Pointer 解引用

```go
// 典型 nil 问题
var ptr *MyStruct
if ptr != nil {
    ptr.Field = value  // ← 分支覆盖了 nil，但逻辑复杂时可能遗漏
}

// 接口 nil
var i interface{}
i = (*MyStruct)(nil)
i == nil  // ← false！接口有类型信息
```

### 2. Goroutine 泄漏与并发问题

```go
// goroutine 泄漏
func handler() {
    go func() {
        ch := make(chan int)
        for v := range ch {  // 如果没人写，永久阻塞
            process(v)
        }
    }()
    // ← goroutine 泄漏
}

// 竞态条件
var counter int
go func() { counter++ }()
go func() { counter++ }()  // ← data race

// 关闭已关闭的 channel
ch := make(chan int)
close(ch)
ch <- 1  // ← panic: send on closed channel
```

### 3. CGO 边界安全

```go
// CGO 不安全调用
import "C"
func Process(data *C.char) *C.char {
    // ← C 代码可返回任意指针
    return C.get_data(data)  // 可能返回越界指针
}

// C 字符串到 Go 字符串转换
goStr := C.GoString(ptr)  // ← 依赖 C 端保证 \0 终止
```

### 4. Recover 与 Panic 滥用

```go
// 滥用 recover 隐藏问题
func safe() {
    defer func() {
        if r := recover(); r != nil {
            // ← recover 可能隐藏严重错误
        }
    }()
    // 不安全的操作
}
```

---

## 统一缺陷汇总

| ID | 缺陷类型 | 语言 | 代码位置 | 触发条件 | 影响 | CVSS |
|----|---------|------|---------|---------|------|------|
| MEM-01 | 栈溢出 | C | handler.c:50 | strcpy(user_input) | RCE | 9.8 |
| MEM-02 | UAF | C | client.c:100 | free 后仍使用 | RCE | 9.1 |
| MEM-03 | 反序列化 | Java | deserialize.java:30 | readObject() | RCE | 9.8 |
| MEM-04 | nil 解引用 | Go | handler.go:40 | nil 指针访问 | DoS | 6.5 |
| MEM-05 | goroutine 泄漏 | Go | worker.go:30 | channel 无发送 | 资源耗尽 | 5.3 |
| MEM-06 | CGO 边界 | Go | cgo.go:20 | C 返回越界指针 | 内存损坏 | 8.1 |
| MEM-07 | 反射滥用 | Java | reflection.java:50 | Class.forName() | RCE | 9.1 |
| MEM-08 | double-free | C | memory.c:100 | 两次 free | RCE | 9.1 |
| MEM-09 | 接口 nil | Go | interface.go:20 | 接口类型!=nil | 逻辑错误 | 5.9 |
| ... | | | | | | |

---

## PoC 示例

### C 缓冲区溢出

```python
#!/usr/bin/env python3
"""MEM-01 PoC: C 缓冲区溢出"""
payload = b'A' * 200  # 超过栈缓冲区大小
send(sock, payload)
```

### Java 反序列化

```python
#!/usr/bin/env python3
"""MEM-03 PoC: Java 反序列化漏洞"""
import pickle
import base64

class RCE:
    def __reduce__(self):
        return (__import__('os').system, ('id',))
payload = base64.b64encode(pickle.dumps(RCE()))
# 发送 payload 到反序列化端点
```

### Go nil 解引用

```go
// PoC: nil pointer 解引用
func handler(w http.ResponseWriter, r *http.Request) {
    var user *User
    // 错误：假设 user 非 nil
    fmt.Fprintf(w, user.Name)  // ← panic: invalid memory address
}
```

---

## 语言特定的缓解措施

| 语言 | 缓解措施 | 有效性 |
|------|---------|--------|
| C/C++ | -fsanitize=address, -fstack-protector | 高 |
| Java | 反序列化白名单（葫芦娃）| 高 |
| Go | nil 检查 + race detector | 高 |
| Go | go vet -atomic -race | 检测并发问题 |
| 跨语言 | 边界验证（FFI/JNI/CGO） | 极高 |

---

**注意**：使用中文输出。分析时请在开头标注项目语言类型，系统自动切换分析模块。提供可验证的 PoC。
