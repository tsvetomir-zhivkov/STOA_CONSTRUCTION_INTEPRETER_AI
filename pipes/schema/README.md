# pipes/schema

`pipe.schema.json` is the piping schema v0.1 (`schema_version: "piping-0.1"`): pipe_segments, pipe_fittings,
pipe_equipment, pipe_connections, texts, coverage, history, statistics. Every route writes it through
`pipes.write.finalize()`:

- `convert.py` : the pipelines build a working graph doc (nodes / edges); `to_piping()` maps it onto the schema and
  `view()` / `graph()` maps a piping file back for the query tools, the demo overlay and the evaluation.
- `derive.py`  : code-written fields (length_m, centerline_m, quantities, connected ids, systems, types, statistics).
- `validate.py`: JSON Schema + reference rules (ids unique, connections and text links exist, unsnapped ends flagged,
  no length without a scale).

`old_pipe.schema.json` is the previous schema (graph doc shape), kept for reference only.
