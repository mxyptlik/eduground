# Dataset Rules

Create `eduground_100q.v1.json` from the schema in this directory. It must contain exactly 100 approved samples before execution.

The existing `apps/evaluator/app/datasets/eduground_100q.seed.json` is historical development material, not the final dataset. Its records explicitly say that live EduGround output must replace the seed answer and context; therefore neither its answers nor its lexical scores can be reused as RAGAS results.

For every final item, keep the reviewed reference answer concise, identify the supporting source and page/slide range, and record the original authoring method. Do not put generated model answers or retrieved contexts in this frozen dataset; those belong in the run-specific input file because they change with the system configuration.
