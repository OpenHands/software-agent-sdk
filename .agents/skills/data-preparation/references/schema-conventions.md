# 正式 JSONL 输出契约

UTF-8 JSONL，一行一个对象；根字段精确匹配，`id` 在文件内唯一。运行审计信息写入
Report，不进入数据行。

## `text`

```json
{"id":"t-1","system_prompt":"You are helpful.","user_prompt":"问题","gt":"答案"}
```

四个字段均为非空字符串。规则清洗本身是前处理，必须在下游生成、QA、代码等场景中
映射成此格式。

## `vision`

```json
{"id":"v-1","image_path":"images/a.png","images":["images/a.png"],"system_prompt":"You are helpful.","user_prompt":"问题","gt":"<think>推理</think>\n<answer>A</answer>"}
```

图片路径是相对 POSIX 路径，`image_path == images[0]`；`gt` 含一组非空
`think/answer`。

## `multiturn`

```json
{"id":"m-1","messages":[{"role":"system","content":"You are helpful."},{"role":"user","content":"问题"},{"role":"assistant","content":"回答"}]}
```

`system` 可选且只能位于开头；后续严格 `user → assistant` 交替并以 assistant 结束。

## `function_call`

```json
{"id":"fc-1","tools":[{"type":"function","function":{"name":"lookup","description":"查询","parameters":{"type":"object","properties":{"q":{"type":"string"}},"required":["q"],"additionalProperties":false}}}],"messages":[{"role":"user","content":"查询 A"},{"role":"assistant","content":"","tool_calls":[{"id":"call_1","type":"function","function":{"name":"lookup","arguments":"{\"q\":\"A\"}"}}]},{"role":"tool","tool_call_id":"call_1","content":"{\"value\":1}"},{"role":"assistant","content":"结果是 1。"}]}
```

工具名和调用 ID 唯一；arguments 是 JSON object 字符串；每个调用恰好对应一个 tool
返回；完整轨迹以最终 assistant 回复结束。

## `quality_evaluation`

```json
{"id":"q-1","subject":{"type":"instruction_response","input":"问题","output":"回答"},"evaluation":{"overall_score":4,"decision":"keep","dimensions":[{"name":"correctness","score":4,"reason":"结论正确"}],"issues":[]}}
```

`subject.type` 为 `text/instruction_response/conversation/function_call/text2sql`；
评分 1～5，decision 为 `keep/rewrite/drop`。维度名称可扩展，但同一条内唯一。

## `text2sql`

```json
{"id":"sql-1","question":"查询用户数","database":{"id":"db1","dialect":"sqlite","schema":{"tables":[{"name":"users","columns":[{"name":"id","type":"INTEGER","nullable":false}],"primary_key":["id"]}],"foreign_keys":[]}},"evidence":"","sql":"SELECT COUNT(*) FROM users;"}
```

数据库 Schema 至少包含表、列、类型、主键和外键字段。执行结果、错误和难度进入
`scenario_metrics.json`，不进入训练行。
