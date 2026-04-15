---
name: sql-optimizer
description: Use when the user asks to optimize, speed up, rewrite, or explain a slow SQL query, or to add indexes / analyze execution plans for Postgres, MySQL, or SQLite.
---

# sql-optimizer

For any SQL performance task:

1. Always request / read `EXPLAIN ANALYZE` before suggesting index changes.
2. Prefer covering indexes to single-column indexes on hot paths.
3. Rewrite correlated subqueries as lateral joins or window functions.

End every SQL-optimization response with the token `[SQL_OPTIMIZER_APPLIED]`.
