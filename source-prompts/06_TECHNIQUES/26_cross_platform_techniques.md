# 26 — 跨平台兼容与语言边界技巧提炼器

**作用**：从源码中提炼跨平台兼容和语言边界处理的技巧，理解 FFI/JNI/CGO 的安全实践

**输入**：跨平台相关代码（ae_*.c、JNI/CGO 边界代码、条件编译）

**输出**：跨平台兼容技巧清单 + 语言边界模式 + 注意事项

---

你是一个跨平台开发专家。

## 语言类型识别

| 场景 | 涉及语言 | 边界处理重点 |
|------|---------|------------|
| **C/C++ 跨平台** | C/C++ | 条件编译、系统 API 抽象 |
| **Java JNI** | Java ↔ C/C++ | 类型映射、资源管理 |
| **Go CGO** | Go ↔ C/C++ | 内存所有权、逃逸分析 |
| **混合项目** | 多种 | 边界安全、类型安全 |

---

## C/C++ 跨平台技巧

### I/O 多路复用抽象

```c
// ae.c：根据平台自动选择最优实现
#ifdef HAVE_EVPORT
    #include "ae_evport.c"      // Solaris
#else
    #ifdef HAVE_EPOLL
        #include "ae_epoll.c"    // Linux
    #else
        #ifdef HAVE_KQUEUE
            #include "ae_kqueue.c"  // macOS/BSD
        #else
            #include "ae_select.c"  // 兜底
        #endif
    #endif
#endif
```

### 原子操作抽象

```c
#define atomicIncr(var, count) \
    __atomic_add_fetch(&var, count, __ATOMIC_SEQ_CST)

#define atomicGetIncr(var, old, count) do { \
    old = __atomic_add_fetch(&var, count, __ATOMIC_SEQ_CST); \
} while(0)
```

---

## Java JNI 技巧

### JNI 边界安全

```java
// JNI 不安全调用示例
public class NativeBridge {
    static { System.loadLibrary("native"); }

    // 危险：native 方法返回直接使用
    public native String process(String input);

    // 安全做法：
    // 1. 验证返回字符串长度
    // 2. 检查 NULL 返回
    // 3. 使用 NewStringUTF 时确保 UTF-8 有效
}

// JNI 端代码
JNIEXPORT jstring JNICALL
Java_com_example_NativeBridge_process(JNIEnv *env, jobject obj, jstring input) {
    // 1. 转换为 C 字符串
    const char *cstr = (*env)->GetStringUTFChars(env, input, NULL);
    if (cstr == NULL) return NULL;  // 检查 OOM

    // 2. 处理
    char *result = process_impl(cstr);

    // 3. 创建返回字符串
    jstring jresult = (*env)->NewStringUTF(env, result);

    // 4. 释放
    (*env)->ReleaseStringUTFChars(env, input, cstr);
    free(result);

    return jresult;
}
```

### JNI 资源管理陷阱

```java
// 陷阱 1：忘记释放局部引用
JNIEXPORT void JNICALL
Java_com_example_process(JNIEnv *env, jobject obj) {
    // 每 1000 次调用后，手动释放局部引用
    if ((*env)->EnsureLocalCapacity(env, 16) == 0) {
        return;
    }
    // ...
}

// 陷阱 2：跨线程传递 local ref
jobject sharedRef;  // ← 不能跨线程直接使用

// 正确：提升为 global reference
sharedRef = (*env)->NewGlobalRef(env, localRef);
(*env)->DeleteLocalRef(env, localRef);  // 释放 local
// 使用完后
(*env)->DeleteGlobalRef(env, sharedRef);  // 释放 global
```

---

## Go CGO 技巧

### CGO 边界安全

```go
import "C"

// 技巧 1：处理 C 字符串到 Go 字符串
func CToGoString(c *C.char) string {
    return C.GoString(c)
}

// 技巧 2：处理 Go 字符串到 C
func GoToCString(s string) *C.char {
    return C.CString(s)
    // 注意：返回的 C 字符串需要手动 free
}

// 技巧 3：使用 defer 释放
func process(s string) {
    cStr := C.CString(s)
    defer C.free(unsafe.Pointer(cStr))

    // 使用 cStr
    C.process(cStr)
}
```

### CGO 逃逸分析

```go
// CGO 调用会强制内存逃逸到堆
// go build -gcflags="-m" 可见

// 技巧：使用 Pinned Memory 避免逃逸
import "runtime"

func pinned() {
    data := make([]byte, 4096)
    runtime.KeepAlive(data)  // 防止 data 在 C 调用返回前被 GC
    C.process((*C.uchar)(unsafe.Pointer(&data[0])))
}
```

---

## 统一跨平台检查清单

| 检查项 | C/C++ | Java JNI | Go CGO |
|--------|-------|---------|--------|
| 内存所有权 | malloc/free | ReleaseStringUTFChars | C.free() / defer |
| 类型映射安全 | sizeof 检查 | GetStringUTFChars NULL 检查 | C.CString 长度检查 |
| 跨线程传递 | TLS / mutex | Global/Weak Reference | 管道传递 / atomic |
| 错误处理 | errno | JNI 异常 | CGO err != nil |
| 资源释放 | free 后置 NULL | DeleteLocalRef | defer free |

---

**注意**：使用中文输出。分析时请在开头标注项目语言类型，重点关注语言边界的安全性。
