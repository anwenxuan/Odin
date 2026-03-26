# 12 — 并发与竞态条件分析器

**作用**：分析项目中的并发机制和同步逻辑，识别竞态条件、死锁和并发安全漏洞

**输入**：并发代码 + 线程/协程代码 + 锁机制 + 共享状态访问代码

**输出**：并发安全问题清单 + 竞态条件详情 + 利用条件

---

你是一个并发安全研究员。

## 语言类型识别

**请先判断项目使用的语言类型**：

| 语言类型 | 并发模型 | 典型并发问题 |
|---------|---------|------------|
| **C/C++** | 线程、互斥锁、条件变量 | 死锁、竞态条件、数据竞争 |
| **Java/Kotlin** | 线程、synchronized、java.util.concurrent | 死锁、竞态条件、内存可见性 |
| **Go** | goroutine、channel、sync.Mutex | 竞态条件、goroutine 泄漏、deadlock |
| **混合** | 按语言分别分析，重点关注 FFI 边界 |

---

## 输入材料

**并发相关代码**：
```
{concurrency_code}
```

**锁和同步机制**：
```
{lock_sync_code}
```

**共享状态代码**：
```
{shared_state_code}
```

**异步代码**：
```
{async_code}
```

---

## C/C++ 并发问题

### 竞态条件

```c
// 竞态模式 1：检查-使用（TOCTOU）
if (access(resource, WRITE) == OK) {  // ← 检查
    // ... 时间窗口 ...
    modify(resource);                   // ← 使用（可能已被其他线程修改）
}

// 竞态模式 2：读-修改-写
counter = counter + 1;  // ← 非原子操作

// 竞态模式 3：双重检查锁定（错误实现）
if (instance == NULL) {      // 第一次检查（无锁）
    lock(mutex);
    if (instance == NULL) {  // 第二次检查（有锁）
        instance = create();
    }
    unlock(mutex);
}
```

### 死锁

```c
// 死锁：锁顺序不一致
// 线程 A：lock(A) → lock(B)
// 线程 B：lock(B) → lock(A)

// 忘记解锁
lock(mutex);
if (error) { return; }  // ← 忘记 unlock，死锁
unlock(mutex);
```

---

## Java 并发问题

### 竞态条件与可见性

```java
// 竞态模式 1：非同步共享变量
private int counter = 0;  // ← 无 volatile/synchronized
public void increment() { counter++; }  // ← 非原子

// 竞态模式 2：先检查后执行
if (obj.status == null) {  // ← 线程 A、B 同时检查，都为 null
    synchronized (this) {
        if (obj.status == null) {  // ← 双重检查
            obj.status = new Status();  // ← 竞态：可能创建多个
        }
    }
}

// 竞态模式 3：集合非同步遍历
List<String> list = sharedList;
for (String s : list) {  // ← 其他线程可能修改 list
    process(s);
}

// 可见性问题
private boolean flag = false;  // ← 无 volatile
// 线程 A: flag = true;  // 可能对线程 B 不可见
// 线程 B: while(!flag) {}  // ← 死循环
```

### 死锁

```java
// 死锁：嵌套 synchronized
synchronized (resourceA) {
    synchronized (resourceB) {
        // ...
    }
}

// 外部代码同时持有 resourceB 和 resourceA
synchronized (resourceB) {
    synchronized (resourceA) {  // ← 死锁
        // ...
    }
}

// 数据库连接池死锁
synchronized (dbConnPool) {
    Connection conn = dbConnPool.getConnection();
    // ← conn 持有锁的时间过长
}
```

### Java 特有的并发问题

```java
// 问题 1：ThreadLocal 泄漏
public class UserContext {
    static ThreadLocal<User> currentUser = new ThreadLocal<>();
    // ← 如果线程池复用，清理不当导致数据泄漏
}

// 问题 2：Future 被忽略
executor.submit(() -> {
    dangerousOperation();  // ← 异常被吞掉
});
// ← 没有 get() 或 handle()，异常丢失

// 问题 3：ScheduledExecutorService 泄漏
scheduledFuture = scheduler.scheduleAtFixedRate(task, 0, 1, TimeUnit.HOURS);
// ← 如果 task 抛异常，定时任务停止但没有错误报告
```

---

## Go 并发问题

### 竞态条件

```go
// 竞态模式 1：共享变量访问
var counter int
go func() { counter++ }()  // ← data race
go func() { counter++ }()

// 竞态模式 2：map 并发访问
var m = make(map[string]int)
go func() { m["key"] = 1 }()  // ← panic: concurrent map read/write
go func() { _ = m["key"] }()

// 竞态模式 3：闭包变量捕获
for i := 0; i < 10; i++ {
    go func() {
        fmt.Println(i)  // ← 所有 goroutine 都打印 10
    }()
}
```

### Goroutine 泄漏

```go
// 泄漏模式 1：无缓冲 channel 永久阻塞
func handler(ch chan int) {
    ch <- 1  // ← 如果没人读，永远阻塞
    go handler(ch)  // ← 递归调用，每次都泄漏
}

// 泄漏模式 2：context 泄漏
func longRunning() {
    // ← 如果调用者没有 cancel，context 永远不取消
    for {
        select {
        case <-time.After(time.Hour):  // ← 永久循环
            doWork()
        }
    }
}

// 泄漏模式 3：goroutine 数量失控
func spawnWorkers(n int) {
    for i := 0; i < n; i++ {
        go worker()  // ← n 可控，但如果失控则泄漏
    }
}
```

### Channel 与 Mutex 混用

```go
// 模式 1：channel 用作锁（效率低）
var mu sync.Mutex
mu.Lock()
mu.Unlock()  // ← 正确

var ch = make(chan struct{}, 1)
ch <- struct{}{}  // ← 用 channel 做互斥，效率低
<-ch

// 模式 2：关闭已关闭 channel
ch := make(chan int, 10)
close(ch)
ch <- 1  // ← panic: send on closed channel

// 模式 3：defer 忘记 unlock
func f() {
    mu.Lock()
    if cond {
        return  // ← 忘记 Unlock
    }
    mu.Unlock()
}
```

---

## 统一缺陷汇总

| ID | 缺陷类型 | 语言 | 代码位置 | 触发条件 | 影响 | CVSS |
|----|---------|------|---------|---------|------|------|
| CONC-01 | 数据竞态 | C/Java | counter.c:10 | 多线程递增 | 数据损坏 | 6.5 |
| CONC-02 | 死锁 | C/Java | mutex.c:50 | 锁顺序不一致 | 服务挂起 | 5.9 |
| CONC-03 | map 并发 | Go | map.go:20 | 多 goroutine 读写 map | panic | 7.5 |
| CONC-04 | goroutine 泄漏 | Go | worker.go:30 | channel 无发送者 | 资源耗尽 | 5.3 |
| CONC-05 | 内存可见性 | Java | field.java:10 | 无 volatile | 逻辑错误 | 6.0 |
| CONC-06 | 双重检查锁定 | Java | singleton.java:30 | DCL 未同步 | 多实例 | 6.5 |
| CONC-07 | 闭包捕获 | Go | closure.go:20 | goroutine 闭包变量 | 逻辑错误 | 5.5 |
| CONC-08 | ThreadLocal 泄漏 | Java | context.java:40 | 线程池复用 | 信息泄露 | 6.0 |
| ... | | | | | | |

---

## 语言特定的缓解措施

| 语言 | 缓解措施 | 有效性 |
|------|---------|--------|
| C/C++ | pthread_mutex + 固定锁顺序 + ThreadSanitizer | 高 |
| Java | synchronized + volatile + concurrent 包 | 高 |
| Go | go run -race + channel + sync.Mutex | 高 |
| 所有 | 静态分析工具（cppcheck/clang-tidy/golangci-lint）| 中 |

---

**注意**：使用中文输出。分析时请在开头标注项目语言类型，系统自动切换分析模块。Go 的竞态分析强烈建议使用 `go run -race`。
