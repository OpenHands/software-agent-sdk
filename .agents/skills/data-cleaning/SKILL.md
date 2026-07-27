---
name: data-cleaning
description: >-
  将 Pyromind Storage 中的数据集转换为平台训练格式（messages/DPO）：字段映射、
  对话结构解析、格式校验、SHA-256 去重。提供断点续跑、错误隔离和 report 统计。
  mapper 中可写简单内容过滤（regex/关键词/长度），但无预置算子、无 LLM、无跨行操作。
  适用于“格式转换”“字段映射”“简单过滤+可靠执行”；
  复杂内容清洗（去重/PII/语言/毒性/评分/生成）请用 data-preparation。
---

# 数据清洗

所有读取数据、执行清洗、格式校验和查看产物的动作都必须发生在 Pyromind 平台。
本地工作区只用于编写或修改 `clean_script.py`，不得下载数据或本地运行 cleaner、
validator。平台产物只能用 `preview_dataset` 查看。

## 工作流

1. 用 `preview_dataset` 查看用户给出的 Storage 路径。目录包含多个候选文件时，先让
   用户确认输入；确认字段语义、对话分组、过滤规则、system prompt 和有损转换。
2. 只使用 [target-formats.md](references/target-formats.md) 定义的格式。完整的
   prompt/chosen/rejected 自动按 DPO 清洗，不丢弃任一分支；其他数据输出 messages。
3. [cleaning-utils-api.md](references/cleaning-utils-api.md) 是正常清洗任务中唯一的
   Utils 契约。读取它和 [example_clean_script.py](references/example_clean_script.py)
   后编写字段映射；不要查看、搜索或反复读取 `cleaning_utils.py` 实现。只有用户明确
   要求维护 Skill/运行时，或平台行为与 API 契约矛盾时，才检查实现。
4. 用 `upload_file_to_pyromind` 上传脚本，再调用 `run_dataset_cleaning`，传
   `script_path`、`input_path` 和 `limit=3`。不要自行传 `output_dir`。
5. 收到通用 callback 后，从最近一次工具 Observation 取得 `run_id` 和
   `output_dir`，用 `preview_dataset` 查看 `report.json` 和 `output.jsonl`。
   `report.json` 包含格式校验、统计、错误样本和 checkpoint。格式失败或存在系统性
   清洗错误时修改脚本并创建新的试跑，不恢复错误 run。失败且没有报告时按启动失败
   处理，不要在本地解码 callback 的不透明 `error_log`。
6. 格式和语义检查通过后，展示最多三条结果并等待用户明确确认。确认后优先用
   `<sample_output_dir>/clean_script.py` 创建一个不传 `limit` 的新全量 run。
7. 只有平台或进程中断且脚本逻辑正确时，才传原 `input_path` 和
   `resume_run_id` 恢复；恢复时不传新脚本。Pending/Running 时等待 callback，
   不重复提交。

## 平台脚本契约

```text
python3 clean_script.py \
  --input <input_file_or_directory> \
  --output <run_dir>/output.jsonl \
  --state-dir <run_dir> \
  [--resume] \
  [--limit N]
```

`--limit` 限制解包后考虑的源记录数，不是最终保留数。平台会在创建 run 前静态
检查脚本语法和 Utils import；这不读取或执行数据。平台自动把冻结的
`clean_script.py`、`cleaning_utils.py` 和 `validate_format.py` 放在同一 run 目录，
cleaner 完成后在同一 Pod 校验并合并更新 `report.json`。

每个 run 只生成两个数据产物：纯训练数据 `output.jsonl`，以及包含统计、错误、
checkpoint 和格式校验的 `report.json`。
