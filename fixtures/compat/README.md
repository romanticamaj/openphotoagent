# Compatibility fixtures (09b §9.1 FRZ-005, FRZ-006)

Files produced by **earlier releases**, so v1.0+ proves it still reads them:

- `registry-v0.1/models/*.yaml`: `registry/models/` as of `feat/v0.1-d` (registry `schema_version` 1).
- `presets-v0.1/*.yaml`: the built-in presets as of `feat/v0.1-d` (the bare v1 preset format).
- `presets-v0.3/*.yaml`: the built-in presets as of `feat/v0.3`, exported by **v0.3's own** `preset_io.export_text`
  (schema_version 2), run from a worktree of that branch.

Never regenerate these from current code.
