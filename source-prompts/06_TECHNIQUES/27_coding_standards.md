# 27 — 编码规范与工程化技巧提炼器

**作用**：从源码中提炼代码规范和工程化实践，理解各语言高质量代码的组织方式

**输入**：代码组织相关文件（Makefile、头文件、构建配置、注释风格等）

**输出**：编码规范清单 + 工程化实践 + 代码模板

---

你是一个软件工程专家。

## 语言类型识别

| 语言类型 | 规范重点 |
|---------|---------|
| **C/C++** | 头文件保护、命名规范、Makefile、编译器选项 |
| **Java/Kotlin** | 包结构、Javadoc、Checkstyle、Maven/Gradle |
| **Go** | gofmt、go mod、错误处理、文档注释 |

---

## C/C++ 编码规范

### 头文件组织

```c
#ifndef __MODULE_NAME_H
#define __MODULE_NAME_H

// 1. 标准库（按字母顺序）
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// 2. 第三方库
#include <lua.h>

// 3. 内部头文件（按依赖顺序）
#include "ae.h"
#include "sds.h"

// 前向声明
struct redisObject;

// 公共类型
typedef long long mstime_t;

// 函数声明
struct redisCommand *lookupCommand(robj **argv, int argc);

#endif
```

### 命名规范

| 类型 | 规范 | 示例 |
|------|------|------|
| 结构体 | 小写下划线 | `struct redisServer` |
| 函数 | 小写下划线 | `dictFind()` |
| 宏常量 | 全大写下划线 | `C_OK`, `MAX_CLIENTS` |
| 类型定义 | `_t` 后缀 | `size_t` |
| 全局变量 | `server.` 前缀 | `server.clients` |
| 文件名 | 小写下划线 | `server.c` |

---

## Java 编码规范

### 包结构与命名

```java
// 包名：com.company.module.submodule
package com.example.auth.service;

// 类名：PascalCase
public class AuthenticationService { }

// 方法名：camelCase
public boolean authenticate(String username, String password) { }

// 常量：UPPER_SNAKE_CASE
public static final int MAX_RETRY_COUNT = 5;

// Javadoc 注释
/**
 * Authenticates a user with the given credentials.
 *
 * @param username the user's username
 * @param password the user's password (will be hashed)
 * @return true if authentication succeeds, false otherwise
 * @throws IllegalArgumentException if username is null or empty
 */
public boolean authenticate(String username, String password) {
    // ...
}
```

### Maven/Gradle 配置

```xml
<!-- Maven: 依赖版本管理 -->
<dependencyManagement>
    <dependencies>
        <dependency>
            <groupId>com.fasterxml.jackson</groupId>
            <artifactId>jackson-bom</artifactId>
            <version>2.15.0</version>
            <type>pom</type>
            <scope>import</scope>
        </dependency>
    </dependencies>
</dependencyManagement>

<!-- Gradle: 依赖版本集中管理 -->
// build.gradle
ext {
    springVersion = '6.0.0'
    jacksonVersion = '2.15.0'
}
dependencies {
    implementation "com.fasterxml.jackson.core:jackson-databind:$jacksonVersion"
}
```

---

## Go 编码规范

### gofmt 强制格式化

```go
// Go 使用 gofmt 强制统一格式
// 缩进：TAB
// 行宽：无限（但超过 120 字符应拆分）
// 命名：PascalCase 导出，camelCase 私有

// 导入分组（标准库、第三方、本地）
import (
    "fmt"
    "io"
    "time"

    "github.com/pkg/errors"
    "go.uber.org/zap"

    "myproject/auth"
    "myproject/config"
)
```

### 错误处理模式

```go
// 技巧 1：wrap 错误
if err != nil {
    return fmt.Errorf("process failed: %w", err)
}

// 技巧 2： sentinel errors
var ErrNotFound = errors.New("resource not found")

if err == ErrNotFound {
    // 处理
}

// 技巧 3：忽略已知错误
if _, err := os.Stat(path); os.IsNotExist(err) {
    // 文件不存在，这是正常的
} else if err != nil {
    return err
}

// 技巧 4：defer 清理
func process() {
    defer func() {
        mu.Unlock()  // 即使 panic 也执行
    }()
    mu.Lock()
    // ...
}
```

### go.mod 模块管理

```go
// go.mod
module github.com/myproject/service

go 1.21

require (
    github.com/pkg/errors v0.9.1
    go.uber.org/zap v1.24.0
)

require (
    github.com/davecgh/go-spew v1.1.1 // indirect
)
```

---

## 统一工程化实践

| 实践 | C/C++ | Java | Go |
|------|-------|------|-----|
| 格式化工具 | clang-format | google-java-format | gofmt |
| Linter | clang-tidy, cppcheck | SpotBugs, PMD, Checkstyle | golangci-lint, staticcheck |
| 包管理 | CMake FetchContent / vcpkg | Maven Central / Gradle Plugin | go mod |
| 测试框架 | CTest / GoogleTest | JUnit5 / TestNG | testing / testify |
| CI/CD | Makefile / GitHub Actions | GitHub Actions / Jenkins | GitHub Actions |
| 文档生成 | Doxygen | Javadoc | godoc |

---

**注意**：使用中文输出。分析时请在开头标注项目语言类型。
