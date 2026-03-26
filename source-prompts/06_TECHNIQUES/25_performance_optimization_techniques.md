# 25 — 性能优化技巧提炼器

**作用**：从源码中提炼性能优化的核心技巧，理解各语言高性能系统设计的原则

**输入**：性能相关代码（批量 I/O、缓存、内联、零拷贝等）

**输出**：性能优化技巧清单 + 原理分析 + 适用场景

---

你是一个性能优化专家。

## 语言类型识别

| 语言类型 | 性能优化重点 |
|---------|------------|
| **C/C++** | 内联、系统调用优化、SIMD、缓存友好 |
| **Java** | GC 优化、JIT 编译优化、数据结构选型 |
| **Go** | 逃逸分析、goroutine 调度、内存池 |

---

## C/C++ 性能优化

### 批量操作减少系统调用

```c
// 批量写入到客户端
static int _writeToClient(client *c, ssize_t *nwritten) {
    while (clientHasPendingReplies(c)) {
        ssize_t n = write(c->fd,
                          c->buf + c->sentlen,
                          c->bufpos - c->sentlen);
        // 一次性写入尽可能多的数据
        nwritten_total += n;
        c->sentlen += n;
    }
}
```

### 内联优化

```c
// 高频函数使用 static inline
static inline size_t sdslen(const sds s) {
    return SDS_HDR(8, s)->len;  // 编译时内联，零开销
}
```

---

## Java 性能优化

### JIT 编译优化

```java
// 热点代码会被 JIT 编译成本地机器码

// 技巧 1：避免不必要的自动装箱
// 反例：频繁的 Integer ↔ int 转换
List<Integer> list = new ArrayList<>();
for (int i = 0; i < 100000; i++) {
    list.add(i);  // 自动装箱 i → Integer
}

// 正例：使用原始类型
IntArrayList list = new IntArrayList();  // Trove4J
for (int i = 0; i < 100000; i++) {
    list.add(i);  // 无装箱
}

// 技巧 2：循环优化
for (int i = 0; i < arr.length; i++) {   // 每次检查 arr.length
    process(arr[i]);
}
for (int i = 0, n = arr.length; i < n; i++) {  // 缓存长度
    process(arr[i]);
}

// 技巧 3：方法内联
// JIT 会内联小方法和虚方法（如果只有一个实现）
private final void process() { ... }  // final 有助于 JIT 内联
```

### 数据结构选型

```java
// ArrayList vs LinkedList
// ArrayList: O(1) 随机访问，O(n) 插入
// LinkedList: O(n) 随机访问，O(1) 头部/尾部插入

// 选择原则：频繁随机访问 → ArrayList
//           频繁中间插入/删除 → LinkedList（或 ArrayDeque）

// 内存友好
String s1 = "hello";        // 字符串常量池
String s2 = new String("hello");  // 堆上新分配

// StringBuilder vs StringBuffer
// StringBuilder: 非同步，性能好
// StringBuffer: synchronized，同步安全
```

---

## Go 性能优化

### 减少内存分配

```go
// 技巧 1：预分配 slice 容量
// 反例：频繁 append 导致多次扩容
var data []int
for i := 0; i < n; i++ {
    data = append(data, i)  // 多次扩容
}

// 正例：预分配
data := make([]int, 0, n)  // 预分配 n 容量
for i := 0; i < n; i++ {
    data = append(data, i)
}

// 技巧 2：使用 sync.Pool 复用对象
var pool = sync.Pool{
    New: func() interface{} { return new(bytes.Buffer) },
}

// 技巧 3：bytes.Buffer 替代字符串拼接
var buf bytes.Buffer
for i := 0; i < 1000; i++ {
    buf.WriteString(fmt.Sprintf("%d", i))  // 避免频繁字符串分配
}
```

### 逃逸分析与优化

```go
// go build -gcflags="-m" 分析逃逸
// 返回指针 = 逃逸到堆

// 反例：返回切片头指针
func getData() []int {
    s := make([]int, 100)
    return s  // 逃逸
}

// 正例：传入切片
func processData(s []int) {  // 栈上分配
    // 处理 s
}
```

---

## 统一技巧总结

| 技巧 | C/C++ | Java | Go |
|------|-------|------|-----|
| 批量 I/O | 输出缓冲区 | BufferedOutputStream | bufio.Writer |
| 内联 | `static inline` | JIT 自动内联 | `//go:inline` 注解 |
| 预分配 | SDS 预分配 | `ArrayList(size)` | `make([]int, 0, n)` |
| 减少分配 | 零拷贝引用 | 对象池/ThreadLocal | sync.Pool |
| 缓存友好 | 连续内存访问 | ArrayList vs LinkedList | Slice 连续内存 |

---

**注意**：使用中文输出。分析时请在开头标注项目语言类型。
