# 22 — 内存管理与运行时资源技巧提炼器

**作用**：从源码中提炼内存管理和运行时资源的核心技巧，理解各语言的内存模型和资源管理模式

**输入**：内存相关代码（zmalloc、sds、GC 配置、pool 等）

**输出**：内存/资源管理技巧清单 + 源码示例 + 应用场景

---

你是一个多语言性能优化专家。

## 语言类型识别

**请先判断项目使用的语言类型**：

| 语言类型 | 内存管理模型 | 关键技巧 |
|---------|------------|---------|
| **C/C++** | 手动管理（malloc/free） | 统一分配器、预分配、引用计数 |
| **Java/Kotlin** | GC 自动管理 | 对象池、弱引用、TLAB 配置 |
| **Go** | GC 自动管理（并发三色标记） | sync.Pool、对象复用、内存剖析 |

---

## 输入材料

**内存分配代码**：
```
{memory_code}
```

**内存池/对象池代码**：
```
{pool_code}
```

**GC 配置代码**：
```
{gc_config}
```

---

## C/C++ 内存管理技巧

### 1. 分配器抽象

```c
// 统一内存分配入口
#define zmalloc(size) zmalloc_internal(size, ZMALLOC_HDR_SIZE)

// 编译时切换分配器
#if defined(USE_JEMALLOC)
    #define zmalloc_size(p) je_malloc_usable_size(p)
#elif defined(__APPLE__)
    #define zmalloc_size(p) malloc_size(p)
#endif
```

### 2. 预分配策略

```c
#define SDS_MAX_PREALLOC (1024*1024)  // 1MB

sds sdsMakeRoomFor(sds s, size_t addlen) {
    size_t newlen = sdslen(s) + addlen;
    if (newlen > sdsalloc(s)) {
        // 小于 1MB 时翻倍，大于 1MB 时线性增长
        size_t newalloc = (newlen < SDS_MAX_PREALLOC) ?
                          newlen * 2 : newlen + SDS_MAX_PREALLOC;
        s = s_realloc(s, newalloc);
    }
    return s;
}
```

### 3. 引用计数

```c
robj *obj = createObject(...);
incrRefCount(obj);   // 增加引用
decrRefCount(obj);  // 减少引用，自动释放
```

---

## Java 内存管理技巧

### 1. 对象池模式

```java
// 复用昂贵对象
public class StringBuilderPool {
    private static final ThreadLocal<StringBuilder> pool =
        ThreadLocal.withInitial(StringBuilder::new);

    public static StringBuilder get() {
        StringBuilder sb = pool.get();
        sb.setLength(0);  // 清空复用
        return sb;
    }
}

// 使用 ByteBuffer 池减少 GC 压力
public class BufferPool {
    private static final Queue<ByteBuffer> POOL =
        new ConcurrentLinkedQueue<>();

    public static ByteBuffer acquire(int size) {
        ByteBuffer bb = POOL.poll();
        if (bb == null || bb.capacity() < size) {
            return ByteBuffer.allocateDirect(size);
        }
        return bb;
    }
}
```

### 2. 弱引用/软引用

```java
// 缓存使用软引用（内存不足时被回收）
private SoftReference<CacheEntry> cacheRef = new SoftReference<>(entry);

// 监听器注册使用弱引用（避免内存泄漏）
private WeakHashMap<Listener, Object> listeners = new WeakHashMap<>();

// 典型应用：ConcurrentReferenceHashMap
ConcurrentReferenceHashMap<Key, Value> cache =
    new ConcurrentReferenceHashMap<>(16, ReferenceType.WEAK);
```

### 3. TLAB 配置（JVM 调优）

```java
// -XX:+UseTLAB 启用线程本地分配块
// -XX:TLABSize=512k 设置 TLAB 大小
// -XX:ResizeTLAB 自动调整 TLAB
```

---

## Go 内存管理技巧

### 1. sync.Pool 对象池

```go
// sync.Pool：并发安全对象池
var bufPool = sync.Pool{
    New: func() interface{} {
        b := make([]byte, 1024)
        return &b
    },
}

func getBuffer() []byte {
    buf := bufPool.Get().(*[]byte)
    return (*buf)[:0]  // 重置长度
}

func putBuffer(buf []byte) {
    bufPool.Put(&buf)
}
```

### 2. 内存剖析工具使用

```go
// 运行时内存剖析
import _ "net/http/pprof"
import "runtime"

// 手动触发 GC + 堆剖析
runtime.GC()
debug.FreeOSMemory()

// 内存统计
var m runtime.MemStats
runtime.ReadMemStats(&m)
fmt.Printf("Alloc: %d MB\n", m.Alloc/1024/1024)
fmt.Printf("Sys: %d MB\n", m.Sys/1024/1024)
fmt.Printf("NumGC: %d\n", m.NumGC)
```

### 3. 逃逸分析

```go
// 使用 go build -gcflags="-m" 分析逃逸
// 原则：
// - 小对象栈分配，大对象堆分配
// - 返回指针 = 逃逸到堆
// - 接口类型 = 逃逸到堆
// - 闭包引用外部变量 = 逃逸

// 优化：避免不必要的指针
func process(data [1024]byte) int {  // 值传递，栈上分配
    sum := 0
    for _, v := range data {
        sum += int(v)
    }
    return sum
}
```

---

## 统一技巧总结

| 语言 | 技巧 | 效果 |
|------|------|------|
| C | 分配器抽象（zmalloc） | 统一接口，多平台切换 |
| C | 预分配（SDS 策略） | 减少重分配 |
| C | 引用计数 | 自动释放，防止泄漏 |
| Java | 对象池（ThreadLocal） | 减少 GC 压力 |
| Java | 弱引用缓存 | 防止内存泄漏 |
| Go | sync.Pool | 减少 GC 压力 |
| Go | 逃逸分析 | 减少堆分配 |

---

**注意**：使用中文输出。分析时请在开头标注项目语言类型。
