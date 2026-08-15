# Golden QA Evaluation Analysis

## Summary

- Samples: 100
- Passed: 97
- Failed: 3
- Pass rate: 0.97
- Average faithfulness: 0.9033
- Average context recall: 0.9352
- Average answer relevance proxy: 0.6285
- Average citation support: 0.9033
- Citation presence rate: 1.0
- Source hit at 5: 100 (1.0)
- Retrieval latency average/p50/p95 ms: 167.02 / 145.0 / 259.0
- End-to-end latency average/p50/p95 ms: 357.99 / 329.0 / 548.0
- Model latency average/p50/p95 ms: 3.88 / 3.0 / 9.0

## Failed Samples

| Sample | Faithfulness | Context recall | Answer relevance | Citation support | Reason |
| --- | ---: | ---: | ---: | ---: | --- |
| eduground-doc-049 | 0.5517 | 0.1429 | 0.25 | 0.5517 | low context recall, low answer relevance proxy |
| eduground-doc-050 | 0.7297 | 0.0909 | 0.1136 | 0.7297 | low context recall, low answer relevance proxy |
| eduground-doc-079 | 0.7193 | 0.3793 | 0.2602 | 0.7193 | low context recall, low answer relevance proxy |
