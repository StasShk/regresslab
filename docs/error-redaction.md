# Validation and comparison error privacy

RegressLab CLI prints **field names and error categories**, never raw Pydantic
input values. Unknown input-field names are displayed as `[unknown field]`.
YAML parse failures are reported without excerpting source lines. This matters
when the input contains internal service URLs or authentication details.

Workload compatibility diagnostics identify the differing field (for example,
`URL query differs`), without displaying either original value. Environment
mismatch warnings likewise identify the field without exposing identifiers.

These rules do not encrypt YAML, saved results, CLI arguments, file paths, or
benchmark inputs. Result JSON deliberately retains some workload path/query
values and the experiment name. Do not put credentials or secrets in them.
Also review historical result JSON before sharing it.
