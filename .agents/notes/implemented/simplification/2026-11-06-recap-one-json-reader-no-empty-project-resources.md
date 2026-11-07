# Agent Note: recap 只留一个容错 JSON 读取，去掉恒空的 project resources

Status: implemented

## Problem

video-recap 已有 `lib.read_json_object`，`resources/lock.py`、`resources/project_binding.py`、`recap_source.py` 又各写了一个近似副本。

`lock._read` 返回任意 JSON，调用方接着 `.get`。于是 `recap_run_manifest.json` 或 `assembly_manifest.json` 是非空数组时抛 `AttributeError`，`_record_resources` 的 `except (OSError, ValueError, TypeError, KeyError)` 接不住，一次已经渲染完的运行会因为写记录而崩溃。

`resolve_project` 返回常量 `"resources": []`，`build_resource_lock` 再把它并进去，这两处是这个键唯一的读写。同一份五字段的模板记录按角色写了三遍。

## Decision

- 三个副本删除，都用 `lib.read_json_object`；要 `.get` 的调用方写 `or {}`。`_voice_entry` 判断 `meta is None`，这样 `{}` 的 `tts_meta.json` 仍然产出 voice 条目，行为不变。
- `reject_removed_delivery_workdir` 的 docstring 保留“格式坏的文件不算旧路线的证据”。`reject_removed_dub_workdir` 自己的 try 不动，它要读嵌套键。video-assemble 的同类副本属于另一个 skill，不动。
- `project_binding` 的 `template()` 在校验通过后登记 `used_templates`。校验顺序仍是 subtitle、subtitle 的 env、packaging、production_reference，所以第一条报错不变。

## Alternatives considered

- **把三段模板记录换成循环。** 最强理由：三个角色一目了然。没采用：subtitle 的 env 必须排在 packaging 校验之前，否则两种错误的先后会互换，循环里要多一个按角色的判断。登记放进 `template()` 更短，也不动顺序。

## Consequences

- 收益：少三个读取函数和一个恒空的键；list 形状的 manifest 不再让写完的渲染崩溃。
- 代价：`current == bound["payload"]` 现在只对对象成立。`library.py` 校验过 payload 必须是对象，所以结果不变。
- 是否在默认路径：`resource_lock.json` 的输出不变，`test_resource_lock.py` 和 `test_project_binding.py` 通过。新增测试锁住 list 形状的 manifest。
