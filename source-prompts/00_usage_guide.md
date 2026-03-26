# 00 — GitHub 源码研究实操指南

> **版本**：v3.0
> **作用**：本指南说明如何使用 source-prompts v3.0 对 GitHub 项目进行深度源码研究。从仓库获取到多轮研究闭环，提供完整的输入准备、阶段执行和输出管理流程。
> **前置要求**：`00_master.md`（总控协议）+ `31_question_generator.md`（增强版）+ `32_self_research.md`（增强版）

---

## 一、GitHub 仓库输入准备

### 1.1 必选材料（首次研究必须提供）

```bash
# 克隆仓库
git clone https://github.com/[owner]/[repo].git
cd [repo]

# 1. README（必选）
cat README.md

# 2. 目录树（必选，最多 200 个文件）
find . -type f | grep -v '.git' | head -200

# 3. 依赖文件（按语言选择）
# Python
cat requirements.txt 2>/dev/null || cat pyproject.toml 2>/dev/null || cat setup.py 2>/dev/null
# Java
cat pom.xml 2>/dev/null || cat build.gradle 2>/dev/null
# Go
cat go.mod 2>/dev/null
# C/C++
cat Makefile 2>/dev/null || cat CMakeLists.txt 2>/dev/null

# 4. 入口文件（必选）
# 查找 main 函数或入口脚本
grep -r "main\s*(" --include="*.c" --include="*.go" --include="*.java" --include="*.py" | head -10
# 查找 __main__.py（Python）
find . -name "__main__.py" | head -5
```

### 1.2 可选材料（提升分析深度）

```bash
# 5. 配置文件
find . -name "*.conf" -o -name "*.yaml" -o -name "*.yml" -o -name "*.json" -o -name "*.toml" | grep -v node_modules | head -20

# 6. 网络/协议相关代码
find . -name "*.c" -o -name "*.go" -o -name "*.java" -o -name "*.py" | xargs grep -l "socket\|recv\|send\|http\|TCP\|UDP\|bind\|listen" 2>/dev/null | head -10

# 7. 认证/加密相关代码
find . -name "*.c" -o -name "*.go" -o -name "*.java" | xargs grep -l "auth\|encrypt\|decrypt\|password\|token\|session" 2>/dev/null | head -10

# 8. 内存管理相关代码（C/C++）
find . -name "*.c" -o -name "*.cpp" | xargs grep -l "malloc\|free\|alloc\|new\|delete\|buffer\|heap" 2>/dev/null | head -10

# 9. 关键源文件（按重要性选择）
# 网络 I/O
find . \( -name "*.c" -o -name "*.go" -o -name "*.java" \) | xargs grep -l "accept\|connect\|read\|write" 2>/dev/null | head -5
# 命令处理
find . \( -name "*.c" -o -name "*.go" -o -name "*.java" \) | xargs grep -l "command\|handler\|dispatch\|route" 2>/dev/null | head -5
```

### 1.3 准备状态文件（Round > 1 时需要）

```yaml
# research_state.yaml（由 00_master.md 每轮生成，下一轮研究时作为输入）
round: N
project_type: [web服务/CLI/库/中间件/分布式/其他]
security_focus: [高危模块列表]
state:
  confirmed: [...]
  hypothesis: [...]
  refuted: [...]
  unknown: [...]
open_questions: [待攻克问题列表]
depth_tracked: {问题ID: 已追踪层数}
```

---

## 二、启动研究（两种方式）

### 方式一：完整自动调度（推荐）

将以下内容作为 AI 的系统级 Prompt 或第一条指令：

```
你是一个源码研究专家。请对以下 GitHub 仓库执行深度源码研究。

请先阅读以下材料：
1. README.md（已提供）
2. 目录树（已提供）
3. 依赖文件（已提供）
4. 关键源文件（已提供）

然后按照 `00_master.md` 总控协议执行研究：
- 阶段 0：初始化（项目发现、模块划分、入口定位）
- 阶段 1：问题生成（调用 31_question_generator 增强版）
- 阶段 2：调用链追踪（对每个问题追踪 3+ 层）
- 阶段 3：安全分析与漏洞假设
- 阶段 4：漏洞验证与 PoC
- 阶段 5：研究综合（按 8 章节 schema 输出）
- 阶段 6：收敛判定

关键约束：
- 每个结论必须包含 ≥3/5 类 MEU 证据（文件路径、函数名、分支条件、数据结构字段、调用关系）
- MEU < 3/5 → 强制 hypothesis，禁止 confirmed
- 每轮结束后，更新状态文件（confirmed/hypothesis/refuted/unknown）
- 若未收敛，返回阶段 1 继续

[粘贴 README + 目录树 + 依赖文件 + 关键代码]
```

### 方式二：手动分步执行

适用于需要人工控制每个阶段的情况：

```bash
# Step 1: 初始化 → 输出项目类型和技术栈
# 使用 01_repo_scanner.md → 02_repo_map_generator.md → 04_entry_point_finder.md

# Step 2: 问题生成
# 使用 31_question_generator.md（增强版）→ 生成 3-7 个问题

# Step 3: 调用链追踪
# 使用 20_call_graph_dataflow_analyzer.md + 18_api_config_analyzer.md
# 每个问题追踪 3+ 层，输出关键函数卡片 + 数据结构卡片

# Step 4: 安全分析
# 按问题标签调用 07-16 对应模块 → 生成漏洞假设

# Step 5: 漏洞验证
# 使用 15_exploit_path_tracker.md → 输出 PoC 或反证

# Step 6: 综合报告
# 使用 32_self_research.md（增强版）→ 按 8 章节输出 → 更新状态文件

# Step 7: 判断收敛
# 若未收敛 → 返回 Step 2（传入上一轮状态文件）
```

---

## 三、每轮研究执行流程

### Round N（N = 1, 2, 3...）

```
┌─────────────────────────────────────────────────────────┐
│  Round N 开始                                             │
│  输入：仓库材料（Round 1）或 仓库材料 + 状态文件（Round > 1）  │
└─────────────────────┬───────────────────────────────────┘
                      ▼
┌─────────────────────────────────────────────────────────┐
│  阶段 0：初始化（仅 Round 1）                              │
│  → 项目类型标签 + 技术栈清单 + 安全敏感区域                  │
└─────────────────────┬───────────────────────────────────┘
                      ▼
┌─────────────────────────────────────────────────────────┐
│  阶段 1：问题生成（每轮必须）                               │
│  → 读取 open_questions（上一轮 hypothesis + unknown）       │
│  → 调用 31_question_generator（增强版）                    │
│  → 输出：3-7 个问题 + MEU 起点 + 安全映射标签               │
└─────────────────────┬───────────────────────────────────┘
                      ▼
┌─────────────────────────────────────────────────────────┐
│  阶段 2：调用链追踪                                        │
│  → 对每个问题追踪主路径（≥3 层）+ 异常/失败路径             │
│  → 输出：关键函数卡片 + 数据结构卡片                        │
│  → MEU 判定 → 标记 confirmed / hypothesis                  │
└─────────────────────┬───────────────────────────────────┘
                      ▼
┌─────────────────────────────────────────────────────────┐
│  阶段 3：安全分析与漏洞假设                                 │
│  → 按安全映射标签调用 07-16 对应模块                        │
│  → 输出：攻击面 + 漏洞假设（触发条件 + 前置要求 + MEU）      │
└─────────────────────┬───────────────────────────────────┘
                      ▼
┌─────────────────────────────────────────────────────────┐
│  阶段 4：漏洞验证与 PoC                                    │
│  → 对每个 hypothesis 执行 PoC 构造或反证                     │
│  → MEU 判定 → 标记 confirmed / refuted / hypothesis        │
└─────────────────────┬───────────────────────────────────┘
                      ▼
┌─────────────────────────────────────────────────────────┐
│  阶段 5：研究综合                                          │
│  → 调用 32_self_research（增强版）                          │
│  → 按 8 章节 schema 输出 Round N 报告                      │
│  → 更新状态文件（confirmed / hypothesis / refuted / unknown）│
└─────────────────────┬───────────────────────────────────┘
                      ▼
┌─────────────────────────────────────────────────────────┐
│  阶段 6：收敛判定                                          │
│  ✓ 所有 P0 问题 confirmed？  → 收敛 → 输出最终报告          │
│  ✓ unknown ≤ 2 且需特殊材料？→ 收敛 → 输出最终报告          │
│  ✓ research budget 用尽？   → 收敛 → 输出最终报告          │
│  ✗ 上述均不满足              → round += 1 → 返回阶段 1      │
└─────────────────────────────────────────────────────────┘
```

---

## 四、状态文件管理

### 4.1 状态文件格式

```yaml
# research_state.yaml（每轮由 00_master.md 生成和更新）
round: 1
project_type: "C/C++ 网络服务"
security_focus:
  - "网络 I/O 处理（networking.c）"
  - "命令解析（server.c）"
  - "内存分配（zmalloc.c）"

state:
  confirmed:
    - id: Q1
      text: "请求通过 readQueryFromClient 进入，在 processCommand 分发"
      mev: "networking.c:142, readQueryFromClient, if (nread < 0), client.flag, readQueryFromClient→processCommand"
      source: Q1
  hypothesis:
    - id: Q2
      text: "processCommand 中存在命令注入风险"
      mev_current: "2/5"
      missing: "分支条件分析、反例搜索"
      next_round_priority: P1
  refuted: []
  unknown:
    - id: Q5
      text: "内存碎片化问题"
      missing_material: "需要运行态内存分析"
      suggestion: "使用 GDB 附加进程观察内存分配模式"

open_questions:
  - Q2（待攻克）
  - Q5（需补充材料）

depth_tracked:
  Q1: 5
  Q2: 3
```

### 4.2 状态文件使用规则

1. **Round 1 之前**：不存在状态文件，从阶段 0 开始
2. **Round N 开始时**：读取上一轮状态文件，获取 `open_questions` 和 `state`
3. **Round N 结束时**：生成新的状态文件，替换旧文件
4. **状态文件命名**：`research_state_round{N}.yaml`
5. **备份**：每次生成新状态文件前，备份上一轮版本

---

## 五、MEU 证据采集指南

### 5.1 采集优先级

在追踪每个问题时，按以下优先级采集 MEU：

```
优先级 1（必须）：文件路径 + 函数名
    ↓ 达到此标准，问题可进入 open_questions
优先级 2（建议）：分支条件 + 数据结构字段
    ↓ 达到此标准，问题可输出 confirmed
优先级 3（可选）：调用关系
    ↓ 补充完整 MEU 清单
```

### 5.2 MEU 采集示例

```python
# 示例问题：一个 Redis SET 命令如何被处理？

# 优先级 1（必须）
文件路径: "src/networking.c:142"
函数名: "readQueryFromClient(client *c)"

# 优先级 2（建议）
分支条件: "if (nread < 0 && errno != EAGAIN)"
数据结构字段: "client.flag & CLIENT_MASTER"

# 优先级 3（可选）
调用关系: "readQueryFromClient → processCommand → call() → aeProcessEvents"

# 完整 MEU = 5/5 → confirmed
```

### 5.3 MEU 不足时的处理

```yaml
# MEU = 2/5，不满足 3/5 标准
# → 输出【证据不足】
# → 标记为 hypothesis
# → 标注缺少的证据类型
# → 进入下一轮 open_questions

example:
  text: "内存池可能存在碎片化问题"
  mev_current: 2/5
  provided:
    - "zmalloc.c:218"
    - "zmallocavyObject()"
  missing:
    - "分支条件"
    - "数据结构字段"
    - "调用关系"
  action: "进入下一轮，增加分支分析"
```

---

## 六、收敛判断与研究终止

### 6.1 收敛条件（满足任一即终止）

```yaml
convergence_conditions:
  condition_1:
    name: "P0 问题全部 confirmed"
    description: "所有高优先级问题都达到 MEU >= 3/5 且无反例"
    action: "研究收敛，输出最终报告"

  condition_2:
    name: "unknown 达到自然边界"
    description: "剩余 unknown <= 2 且每个 unknown 都标注了需要特殊材料（二进制/运行态/特殊环境）"
    action: "研究收敛（自然边界），输出最终报告 + unknown 清单"

  condition_3:
    name: "research budget 用尽"
    description: "已执行最大轮数（N 轮，由用户指定）"
    action: "强制收敛，输出当前状态 + 最终报告"
```

### 6.2 最终报告结构

```markdown
# [项目名称] 源码研究报告 — Round N（最终）

## 研究概况
- 项目类型: ...
- 技术栈: ...
- 研究轮数: N
- 研究时间: ...
- 收敛原因: [满足 condition_X]

## 核心发现

### confirmed 结论
| # | 结论 | MEU | 风险等级 |
|---|------|-----|---------|
| 1 | ... | 4/5 | 高/中/低 |

### refuted 排除
| # | 被排除假设 | 反例路径 |
|---|-----------|---------|
| 1 | ... | networking.c:218 |

## hypothesis 待进一步研究
| # | 问题 | MEU 当前 | 缺少 |
|---|------|---------|------|
| 1 | ... | 2/5 | 分支分析 |

## unknown 自然边界问题
| # | 问题 | 需要什么材料 |
|---|------|------------|
| 1 | ... | 二进制分析 / 运行态 |

## 调用链图
[关键调用链汇总]

## 风险评级矩阵
| 漏洞 | 触发条件 | 前置要求 | 风险等级 | PoC |
|------|---------|---------|---------|-----|
| ... | ... | ... | ... | ... |

## 附录：多轮研究轨迹
- Round 1: ...
- Round 2: ...
- Round N: 收敛
```

---

## 七、常见问题处理

### 7.1 项目类型识别错误

```yaml
# 如果 AI 在阶段 0 识别错误，处理方式：
issue: "项目被误识别为 CLI 工具，实际是网络服务"
solution: |
  在状态文件中修正 project_type，并重新执行阶段 1
  # 例如：
  project_type: "C/C++ 网络服务"
  # 然后在下一轮从阶段 1 开始
```

### 7.2 MEU 始终无法满足 3/5

```yaml
# 如果某个问题长期处于 hypothesis 状态
issue: "某问题追踪了 3 轮，MEU 始终为 2/5"
solution: |
  1. 检查是否缺少关键代码文件
  2. 如果缺少 → 在状态文件中添加 {missing_material} 标注
  3. 如果已有所有代码 → 强制输出 hypothesis + 下一步手动验证建议
  # 例如：
  hypothesis:
    - id: Q3
      text: "可能存在整数溢出"
      mev_current: "2/5"
      missing: "需要汇编级分析"
      suggestion: "建议使用 objdump + GDB 手动验证"
```

### 7.3 轮次过多但未收敛

```yaml
# 如果超过 N 轮仍未收敛（由用户指定上限）
issue: "Round 5 仍未收敛，但用户设置了 max_rounds=5"
solution: |
  1. 强制收敛（research budget 用尽）
  2. 输出当前状态 + 最终报告
  3. 在报告中标注"研究未完成，以下为已确认结论"
  # 保留所有 hypothesis 和 unknown 供后续研究使用
```

### 7.4 仓库规模过大

```yaml
# 如果仓库规模超出 AI 处理能力
issue: "仓库包含 10000+ 文件，单次分析不现实"
solution: |
  1. 使用 01_repo_scanner 定位核心文件
  2. 使用 06_code_scale_analyzer 定位 God File
  3. 优先聚焦高风险模块（网络 I/O、命令处理、内存管理）
  4. 在状态文件中标注 focus_areas
  # 例如：
  research_scope:
    priority_modules:
      - "src/networking.c"
      - "src/server.c"
      - "src/zmalloc.c"
    excluded: "测试文件、文档、示例代码"
```

---

## 八、快速参考卡片

```text
╔══════════════════════════════════════════════════════════════╗
║           GitHub 源码研究 — 快速执行清单                      ║
╠══════════════════════════════════════════════════════════════╣
║ 【Round 1 必做】                                              ║
║ 1. 克隆仓库 + 获取 README + 目录树                           ║
║ 2. 识别项目类型 + 技术栈                                      ║
║ 3. 定位入口函数 + 模块划分                                    ║
║ 4. 生成 3-7 个问题（31_question_generator）                  ║
║                                                              ║
║ 【每轮必做】                                                  ║
║ 1. 读取上一轮状态文件                                         ║
║ 2. 执行问题追踪（≥3 层调用深度）                              ║
║ 3. MEU 判定（< 3/5 → hypothesis）                            ║
║ 4. 安全分析 + 漏洞假设                                        ║
║ 5. PoC 构造或反证搜索                                         ║
║ 6. 更新状态文件（confirmed/hypothesis/refuted/unknown）      ║
║ 7. 收敛判定                                                   ║
║                                                              ║
║ 【收敛条件】（满足任一即终止）                                 ║
║  • 所有 P0 问题 confirmed                                     ║
║  • unknown <= 2 且需特殊材料                                  ║
║  • research budget 用尽                                        ║
║                                                              ║
║ 【MEU 标准】                                                  ║
║  文件路径 ✓  函数名 ✓  分支条件    数据结构字段  调用关系    ║
║  → 每个结论必须满足 ≥3/5 类                                  ║
║  → MEU < 3/5 → 强制 hypothesis，禁止 confirmed                ║
╚══════════════════════════════════════════════════════════════╝
```
