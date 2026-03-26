# 实战 C：关键编码技巧提炼与应用

**作用**：从源码中提炼可复用的编码技巧，提升编程能力

---

## C.1 内存管理技巧

### C.1.1 统一内存分配入口

```c
// 统一入口，便于统计和替换
#define zmalloc(size) zmalloc_internal(size, ZMALLOC_HDR_SIZE)
#define zfree(ptr) zmalloc_free(ptr)

// 应用：任何内存分配都用统一入口
char *buf = zmalloc(256);
strcpy(buf, "data");
zfree(buf);
```

### C.1.2 预分配策略

```c
// 根据大小选择增长策略
#define MAX_PREALLOC (1024 * 1024)

size_t calculate_new_size(size_t current, size_t needed) {
    size_t new_size = current + needed;
    if (new_size < MAX_PREALLOC) {
        return new_size * 2;  // 小数据翻倍
    }
    return new_size + MAX_PREALLOC;  // 大数据线性增长
}
```

### C.1.3 引用计数

```c
typedef struct {
    int refcount;
    void *data;
} RefObject;

void retain(RefObject *obj) {
    obj->refcount++;
}

void release(RefObject *obj) {
    if (--obj->refcount == 0) {
        free(obj->data);
        free(obj);
    }
}
```

---

## C.2 数据结构技巧

### C.2.1 O(1) 长度获取

```c
// 头部存储长度元数据
typedef struct {
    size_t len;     // 已用长度
    size_t alloc;   // 已分配大小
    char buf[];     // 灵活数组成员
} StringHeader;

size_t string_len(const char *s) {
    StringHeader *hdr = (StringHeader *)(s - sizeof(StringHeader));
    return hdr->len;  // O(1) 获取
}
```

### C.2.2 位域压缩

```c
// 用位域压缩元数据
typedef struct {
    unsigned type:4;      // 4 位：类型（0-15）
    unsigned encoding:4;  // 4 位：编码（0-15）
    unsigned flags:8;    // 8 位：标志
    void *data;          // 数据指针
} CompactObject;
```

### C.2.3 渐进式扩容

```c
typedef struct {
    Entry **table[2];  // 双哈希表
    long rehashidx;      // -1 = 未在 rehash
    int rehash_bucket;   // 每步迁移的桶数
} HashTable;

void *dictFind(dict *d, void *key) {
    if (dictIsRehashing(d)) {
        dictRehashStep(d);  // 查找时附带迁移
    }
    // ... 标准查找 ...
}
```

---

## C.3 多态与抽象

### C.3.1 策略模式

```c
typedef struct {
    int (*compare)(void *, void *);
    void (*destroy)(void *);
    void *(*copy)(void *);
} Strategy;

typedef struct {
    Strategy *strategy;
    void *data;
} Context;

void context_operate(Context *ctx) {
    if (ctx->strategy->operate) {
        ctx->strategy->operate(ctx->data);
    }
}
```

### C.3.2 命令模式

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

CommandHandler find_command(const char *name) {
    for (int i = 0; commands[i].name; i++) {
        if (strcmp(commands[i].name, name) == 0) {
            return commands[i].handler;
        }
    }
    return NULL;
}
```

### C.3.3 适配器模式

```c
typedef struct {
    int (*open)(void *ctx, const char *path);
    int (*read)(void *ctx, void *buf, size_t n);
    int (*close)(void *ctx);
} IoOps;

typedef struct {
    const IoOps *ops;
    void *impl;
} IoAdapter;

int io_read(IoAdapter *io, void *buf, size_t n) {
    return io->ops->read(io->impl, buf, n);
}
```

---

## C.4 性能优化技巧

### C.4.1 内联优化

```c
// 高频函数使用 static inline
static inline size_t get_len(const char *s) {
    return ((size_t *)s)[-1];
}

// 应用：避免函数调用开销
for (int i = 0; i < n; i++) {
    size_t len = get_len(strings[i]);  // 内联，无调用开销
}
```

### C.4.2 批量 I/O

```c
// 批量写入，减少系统调用
ssize_t bulk_write(int fd, const char *buf, size_t len) {
    ssize_t total = 0;
    while (total < len) {
        ssize_t n = write(fd, buf + total, len - total);
        if (n < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        total += n;
    }
    return total;
}
```

### C.4.3 缓存友好

```c
// 连续内存访问，提高缓存命中率
typedef struct {
    int id;
    char name[64];
    char email[128];
} User;

// 按 ID 顺序遍历，连续内存访问
for (int i = 0; i < count; i++) {
    process_user(&users[i]);  // 连续内存，缓存友好
}
```

---

## C.5 工程化技巧

### C.5.1 统一错误码

```c
#define ERR_OK 0
#define ERR_INVALID_ARG -1
#define ERR_NOT_FOUND -2
#define ERR_NO_MEMORY -3
#define ERR_IO -4

// 应用
int do_something() {
    if (invalid_condition) return ERR_INVALID_ARG;
    if (not_found) return ERR_NOT_FOUND;
    return ERR_OK;
}
```

### C.5.2 断言宏

```c
#ifdef DEBUG
    #define ASSERT(cond) \
        do { if (!(cond)) { \
            fprintf(stderr, "ASSERTION FAILED: %s\n", #cond); \
            abort(); \
        }} while(0)
#else
    #define ASSERT(cond) ((void)0)
#endif
```

### C.5.3 跨平台抽象

```c
// 跨平台 sleep
#ifdef _WIN32
    #include <windows.h>
    #define sleep_ms(ms) Sleep(ms)
#else
    #include <unistd.h>
    #define sleep_ms(ms) usleep((ms) * 1000)
#endif
```

---

## C.6 技巧应用速查

| 技巧 | 源码位置 | 应用场景 |
|------|---------|---------|
| 统一分配器 | zmalloc.h | 内存统计和替换 |
| 预分配 | sds.c | 减少重分配 |
| O(1) 长度 | sds.h | 高频字符串操作 |
| 位域压缩 | server.h | 内存敏感场景 |
| 渐进扩容 | dict.c | 大数据容器 |
| 策略模式 | dictType | 算法可替换 |
| 命令模式 | serverCommand | 插件化 |
| 内联优化 | sds.h | 高频函数 |
| 批量 I/O | networking.c | 减少系统调用 |
| 跨平台抽象 | ae_*.c | 多平台支持 |
