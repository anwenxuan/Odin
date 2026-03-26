# 24 — 多态与抽象技巧提炼器

**作用**：从源码中提炼多态和抽象的技巧，理解各语言的面向对象和多态实现

**输入**：函数指针相关代码（dictType、command、interface 等）

**输出**：多态设计技巧清单 + 模式分析 + 代码模板

---

你是一个设计模式专家。

## 语言类型识别

| 语言类型 | 多态实现方式 |
|---------|------------|
| **C/C++** | 函数指针、虚函数表（vtable）|
| **Java/Kotlin** | 接口、抽象类、多态 |
| **Go** | 接口、函数类型、struct 组合 |

---

## C/C++ 多态技巧

### 策略模式（函数指针表）

```c
typedef struct dictType {
    uint64_t (*hashFunction)(const void *key);
    void *(*keyDup)(dict *d, const void *key);
    int (*keyCompare)(dict *d, const void *key1, const void *key2);
    void (*keyDestructor)(dict *d, void *key);
} dictType;

// 策略切换
dict *d = dictCreate(&dictTypeHeapStringCopyKey, NULL);
```

### 命令模式

```c
typedef void (*CommandHandler)(client *c);

typedef struct {
    const char *name;
    CommandHandler handler;
    int arity;
} Command;

Command commands[] = {
    {"get", cmd_get, 2},
    {"set", cmd_set, 3},
    {NULL, NULL, 0}
};
```

### C++ 虚函数表（vtable）

```cpp
class Command {
public:
    virtual void execute() = 0;  // vptr 指向 vtable
    virtual ~Command() {}
};

class ConcreteCommand : public Command {
    void execute() override;  // vtable[0] 指向此函数
};
```

---

## Java 多态技巧

### 接口与策略模式

```java
// 策略接口
public interface SortStrategy<T> {
    void sort(List<T> list);
}

// 策略实现
public class QuickSort<T> implements SortStrategy<T> { ... }
public class MergeSort<T> implements SortStrategy<T> { ... }

// 使用
public class Sorter<T> {
    private SortStrategy<T> strategy;
    public void sort(List<T> list) {
        strategy.sort(list);  // 多态调用
    }
}
```

### 模板方法模式

```java
public abstract class DataProcessor {
    public final void process() {  // final 防止子类修改骨架
        read();
        validate();
        transform();
        write();
    }

    protected abstract void read();
    protected abstract void transform();
    protected abstract void write();

    private void validate() {  // 公共验证逻辑
        // ...
    }
}
```

### 适配器模式

```java
public class FileAdapter implements DataSource {
    private FileInputStream fis;

    public FileAdapter(String path) throws FileNotFoundException {
        this.fis = new FileInputStream(path);
    }

    @Override
    public byte[] readAll() throws IOException {
        return fis.readAllBytes();
    }
}
```

---

## Go 多态技巧

### 接口（隐式实现）

```go
// 接口定义（无需显式声明实现）
type Writer interface {
    Write(p []byte) (n int, err error)
}

// 任何实现了 Write 方法的类型都实现了 Writer
type FileWriter struct{}

func (FileWriter) Write(p []byte) (n int, err error) {
    // ...
}

// 空接口：类似 Java Object
var any interface{} = "string"
var any2 interface{} = 123
```

### 函数类型（Go 特有）

```go
// 函数作为一等公民
type Handler func(http.ResponseWriter, *http.Request)

func logHandler(h Handler) Handler {
    return func(w http.ResponseWriter, r *http.Request) {
        log.Println(r.URL.Path)
        h(w, r)
    }
}

// 使用
http.HandleFunc("/api", logHandler(apiHandler))
```

### 组合优于继承

```go
// Go 没有继承，但通过嵌入实现组合
type Reader struct{}

func (Reader) Read(p []byte) (n int, err error) {
    return 0, nil
}

type BufferedReader struct {
    Reader  // 嵌入，类似继承
    buf     []byte
}

// 自动获得 Read 方法
```

---

## 统一模式总结

| 模式 | C/C++ | Java | Go |
|------|-------|------|-----|
| 策略 | 函数指针表 | 接口 + 实现类 | 接口 + 实现类型 |
| 命令 | 函数指针 | Command 接口 | 函数类型 / interface{} |
| 适配器 | 包装函数 | Adapter 类 | 包装 struct |
| 模板方法 | 基类 + virtual | 抽象类 | 骨架函数 + hook 方法 |

---

**注意**：使用中文输出。分析时请在开头标注项目语言类型。
