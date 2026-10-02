# PCC 未完成目标与当前交接 — 2026-10-02

这是当前工作状态和复现导航，不是新的项目要求或任务权威。最终要求见
[Project Intent](../project-intent.md) 和 [compiler-contract.md](../compiler-contract.md)；
工作继续由原始维护者指令和相关 GitHub issue 驱动。旧运行的 provenance 保留在
[10 月 1 日交接](2026-10-01-pcc-native-gates-handoff.md) 和各自的运行目录。

用户要求：write Python, run native；C 仍是一等能力；保留五种 GC；完成原始未完成项和
五项 EDG 借鉴；最后统一提交，不拆 commit。状态提问和本交接不取消主线工作。

## 源码与证据身份

- HEAD：`ab6f29d08a9ca53034c85c35eda636b135185262`。没有创建本轮提交。
- 最近一次已执行自举的冻结源码是 v31，1090 个 compiler/runtime 输入文件，SHA256
  `c07150ade286dd2d854fa0a22fa9121cf8d3065ddcecd3a1d5d5501b00869427`。
  清单：`build/remediation-20261001/frozen-current-v31-source.json`。
- v31 的普通 native 回归是 host pcc0/self/no-libpython 发射并实际运行，320 次全部通过，
  stdout 匹配、stderr 为空；它不是新 pcc1 或固定点证明。
  证据：`build/remediation-20261001/native-controls-v31/{result.json,behavior-focused.json}`。
- v31 Stage1 在 92.311 秒的 bootstrap 计时处失败；外层 watchdog 为 92.535 秒。
  没有生成 pcc1，后续阶段未启动。
  `stage1-v31/{stdout.log,stderr.log,result.json,profiles/stage1.result.json}` 均在上述 build 前缀下。
- 最近一次通过实际 native smoke 的 Stage1 仍是 v20：编译/链接 244.030 秒，smoke
  8.331 秒，总计 252.361 秒。其 Stage2 在 68.079 秒失败，没有 pcc2/pcc3。
  不能把这些旧时长用于描述 v31 完整编译性能。
- live 比 v31 多了已稳定的 `py_list.py` / `py_class.py` getter root 修复，58 项
  host-memory 检查通过；还没有包含这些修复的新 runtime/native/bootstrap 资格。
  精确哈希和边界：`runtime-getter-roots-handback/source-receipt.json`。
- 当前异常类下标表达式仍是 native 编译红例：
  `exception-class-cell-red.log`；测试位于
  `tests/python/test_native_exception_class_expressions.py`。

## 为什么连续出现新错误

本轮不仅调整文件位置，还改变共用的 lexical binding、函数对象身份、默认值、异常边和
移动 GC 指针交接。一个共用 lowering 可以影响用户程序、运行时和自举编译器。

旧的部分检查只看 AST、IR 或目标文件生成。生成成功可能含有不可用函数桩；桩替换又会
清掉原来 `py_cpy_*` 调用，使“无 CPython 调用”的文本检查呈现假绿。仅验证有对象文件
也不会执行函数身份、异常类型、字段值或 GC 清理。因此原生执行和整编译器首次经过新形状
时，才暴露遗漏。这是已确认的验证覆盖不足，不能用项目复杂性代替修复。

当前控制已增加不可用桩检测，保留原始失败源和断言，覆盖所有五种 collector 的实际执行；
自举仍逐阶段运行，在首次失败处停止。仍未执行的形状必须继续标明。

## 眼下两个阻塞与下一步

### 自举异常类表达式

v29 Stage1 的 managed root join 错误已在 v31 越过。最小问题是 fresh-instance append 的
接收者 relocation reload 重复触发 Name bound check，给活跃临时 root 增加不完整错误边。
修复和 `with open` 的遗漏 bound store 已用四目标对象和五 GC 原生控制验证。

v31 新失败：`codegen[pcc.driver.cli_core]: Layer 1 except-clause class expression
Subscript not supported`。闭包 cell 把异常类名字转成下标读取；当前
`ExceptionLoweringMixin._emit_exception_class_ref` 仅接受 Name/Attr。下一步：

1. 支持真实异常类表达式，保留类选择、异常传播和临时引用所有权。
2. 执行 cell-captured class 和 indexed class 的最小原生控制，再检查敏感 exception 形状。
3. 冻结包含稳定 runtime getter 修复的新源码，构建匹配 runtime，再跑 Stage1。
4. 新 pcc1 必须编译并执行 C ABI/Python 原程序，重放旧 SSA builder worker9，然后才跑
   Stage2→Stage3 和原始字节固定点。

### GC4 并发容量

v29 线程组 21 次执行中前 20 次通过，最后 `append_gate` / GC4 失败：先报告 unmanaged
refcount pointer，再在 Record 索引比较失败。列表长度和 payload 内容检查已经通过；
不能把它直接归因于排序。

原源 SHA256：`7653bb8f38c25f8841b5b15903da8d3d2a703a3f64f4edcb1467ebb7f4077b0e`。
行为和二进制：`native-threaded-v29/`。同二进制在 probe3 下首次独立运行通过，下一次的
trial0 中止 -6：`gc4-capacity-v29-probe3-repeat/`，因此不能把失败关闭成偶发。

已独立证明并修复的两个风险是 list 元素在 root cleanup 期间移动后返回旧地址，以及
instance field getter 验证后仍用旧 receiver 算字段地址。58 项 host-memory 检查不证明
它们解释了本次 native 失败。下一步用新 runtime 重跑原始程序、全部线程控制和五 GC；
如仍失败，继续区分 append/字段提取/排序阶段。`py_iter` 的额外交接尚未认证。

## 原始未完成范围

下表中的旧失败数字是维护者最初报告或历史记录，不是本日重跑结果。未完成项不会因为局部
对象生成、旧时长或历史绿色标签被关闭。

| 范围 | 当前未完成内容 | 关闭条件 |
|---|---|---|
| 整理并提交 | 大量改动尚无本轮提交；不能只提交旧暂存部分或拆 commit | 全部授权改动完成复核，按用户要求统一提交 |
| LLVM/外部 owner 清除 | self 默认、IR 保留/改名和删除工作已有实现；全工作流依赖拒绝资格未完成 | host stdlib-only；pcc1 不借 LLVM、cc、host Python/libpython 完成 C、runtime、优化、发射、链接、缓存和安装 |
| macOS job | 完整 job 未跑绿；Python 版本/wheel/owned Meson 来源和 3 核 M1 时限仍需闭环 | 新源整个 job 成功，45 分钟内完成 |
| Linux x86_64 job | 旧第 79 模块 stdio/mov 形状和后续 runtime/native 链需要全量资格 | 完整 runtime、安装、native C/Python 和 Stage1→3 job 成功 |
| Windows job | arc4random integer boxing、full-path pointer ternary、普通 int 比较的旧失败家族需全量验证 | Windows 完整 runtime、安装和 native/compiler job 成功 |
| Linux aarch64 job | Linux 平台的新源 Stage1→3 链没有资格 | 在 Linux aarch64 实机/CI 执行完整原始固定点 |
| 五 GC 自举时间 | runtime/Stage1 复用及 adaptive jobs 已有脚本实现，尚无整链时间证明 | 所有五链成功，按目标核心/内存预算并发，总时长满足 6 小时限制 |
| external TLS/archive | x86 TLS 类型信息与 BSD archive symbol table 的完整平台回归仍待资格 | 原测试集、目标文件读回及相应平台原生执行通过 |
| pcc0/pcc1 能力一致 | 未完成函数桩审计、native runtime 自构建和 CLI/API 差异关闭 | 新 pcc1 实际执行相同能力；普通路径不靠不可用桩；关闭 bindgen/uv-lock/runtime builder/diagnostic 等具体边界 |
| GC4 运行时/性能 | 并发 pointer 失败未关闭；自动搬迁、FRESH_ALLOC 选择和性能未完成新源证明 | 保留 finalizer/weakref/resurrection，共同 root 契约；执行并测量实际搬迁、长期 RSS/pause/吞吐 |
| 过时/超时测试 | freestanding 旧 IR expectations 与 partial async 编译超时尚未全量复核 | 更新符合当前契约的断言，执行原程序，定位超时机制；不删测试掩盖问题 |
| structured runtime emission | 已有模块/四格式对象验证，默认生产路径和整 runtime/自举资格仍待证明 | 实际 runtime owner→IR→emitter→archive 路径明确，全部模块和完整自举执行通过 |
| Stage1 并发/性能 | auto memory budget 配置已有实现，新源完整时长和 Stage2 不慢于 Stage1 未重新证明 | 冻结源码/选项/runtime/缓存，记录每阶段时间和 tree RSS；性能比较须阶段成功 |
| 仓库产物清理 | 清理工具已有实现和 dry-run，整体目录/引用清理仍需最终审阅 | 明确保留源码、测试、证据，处理授权无引用产物；不能删除此次故障证据 |
| gateway 主线 | 最新 native scheduler/gateway 对 asyncio 的端到端比较尚未完成 | 同输入/配置的真实 native 基准与长期行为；README 数字绑定产物 |
| 推迟的后端/工具工作 | direct tail calls、madd、peephole、按身份识别 scaffold、PCC_LOG=thread 尚未全部闭环 | 通用实现、原生语义/诊断证据和与生产路径一致的收益验证 |

`pcc/ir` 是 pcc 自己的 IR 基础设施。其 builder 和 direct indexed kernel 必须保留，
不能把目录名曾含 llvm 当成删整个包的依据。Python/C 支持没有被撤销。

## EDG 五项借鉴

| 项目 | 已有工作与未完成边界 |
|---|---|
| 完整前端语义表示 | binding/default/factory/name facts 已有实际控制；完整语义表示未完成，annotation-only 与显式 None 初始化等存在表示缺口 |
| 布局单一来源 | 具体字段/布局缺陷已修；ClassGen、context exports、type inference、closed-world inheritance 仍需合并为同一权威 |
| 分层验证 | 已区分 host/native、生成/链接/运行、pcc1/固定点；当前 320 次是 host pcc0 发射的实际运行，不能称新编译器资格 |
| 按函数管理内存 | 只有代码/阶段 profile 导航，尚无函数生命周期的真实持有量和释放方案验证 |
| 集中目标 ABI | 统一 C ABI 配置已有实现、组件/C 控制和四格式验证；完整 native bootstrap/跨平台 job 未完成 |

## 继续工作的操作导航

使用 `build/remediation-20260930/venv`，其中实际检查 `llvmlite` 不存在，默认 `CEvaluator()`
能初始化。共享 `.venv` 仍不能用于证明依赖清除，不要为证明它而破坏共享环境。

```bash
env -u LC_ALL \
  UV_CACHE_DIR="$PWD/build/remediation-20261001/uv-cache" \
  UV_PROJECT_ENVIRONMENT="$PWD/build/remediation-20260930/venv" \
  uv run --no-sync pytest -x -n0 -vv --tb=short <selected-node>
```

长测试/build 使用现有 process-group/RSS watchdog，isolated outputs，performance lock，
16 GiB tree cap。当前诊断 adapter 为 `stage1-v8-retained/owned_watchdog.py`；只观察自己的
Darwin 子进程。每个新 result 用独立文件名，不覆写早期结果。不能同时运行争用的自举/runtime
测量。执行新 shape 并读回结果；在第一次失败处停止后续依赖阶段。

相关源码：`exception_lowering.py` / `local_bound_lowering.py` / `native_files.py` /
`list_method_lowering.py`；runtime getter 位于 `py_list.py` / `py_class.py`。
新测试：`test_native_exception_class_expressions.py`、`test_runtime_getter_roots.py`、
`test_fresh_native_instance_append.py`。完整源哈希见对应 source receipts。

本环境 `.git` 写入和 external MCP 写入受 managed policy 限制；此前 issue 更新没有发布。
issue 草稿位于 `build/remediation-20261001/github-issue-171-comment.md`。不要将草稿当作
已更新的 issue。未观察到可用 weekly quota 数据，不声称监控了它。

## Suggested skills

- `code-converge`：继续按计划做独立代码审查、原生验证和修复，不以对象生成替代执行。
- `github`：可访问 CI/issue 时核对实际 job 和 relevant issue；发布动作必须已有授权且工具允许。
- `handoff`：若需要再次交接，遵从仓库要求放在 dated knowledge 路径，保留 source/receipt/repro。

这是 compiler correctness/runtime ownership 工作；没有开展 security scan。
