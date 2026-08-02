# 文本规则清洗

## 适用

对其他生成、QA、SFT、Reasoning 或 Code 场景的输入字段做空值、HTML、空白、语言、
长度和重复数据处理。它不是独立输出 Schema。

最小输入：JSONL 中存在待处理字符串列，例如 `text`、`instruction` 或 `response`。

## 推荐链路

```python
ContentNullFilter().run(storage=storage, input_key="text")
storage = storage.step()
HtmlUrlRemoverRefiner().run(storage=storage, input_key="text")
storage = storage.step()
RemoveEmojiRefiner().run(storage=storage, input_key="text")
storage = storage.step()
RemoveExtraSpacesRefiner().run(storage=storage, input_key="text")
storage = storage.step()
LanguageFilter(allowed_languages=["zh", "en"]).run(
    storage=storage, input_key="text"
)
storage = storage.step()
WordNumberFilter(min_words=10, max_words=50000).run(
    storage=storage, input_key="text"
)
storage = storage.step()
HashDeduplicateFilter(hash_func="md5").run(
    storage=storage, input_key="text"
)
storage = storage.step()
```

近似重复使用 `MinHashDeduplicateFilter` 替代或追加精确去重。阈值根据 Sample 调整，
不要同时堆叠大量语义相近的过滤器。

## 输出与验证

继续执行下游场景算子，再映射到下游 Schema。Sample 检查空值、短文本、HTML、混合
语言和重复样本是否被正确处理。

不使用需要本地模型或 CUDA 的语义去重、Presidio、Task2Vec 算子。
