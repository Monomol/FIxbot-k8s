# Contributing

Contributions are welcome, but this repository is primarily a source snapshot
of a production Helm chart. Please keep changes focused and explain the
operational impact.

## Before opening a change

- Run `helm lint .` and `helm template fixbot . -f values.example.yaml`.
- Run `python -m py_compile scripts/*.py` and `shellcheck scripts/*.sh` when
  touching scripts.
- Do not add real credentials, personal data, internal hostnames, or private
  infrastructure identifiers.
- Keep the generic defaults in `values.yaml` free of environment-specific
  values.

## Commit style

Use a short imperative summary, optionally scoped, for example:

```
fix(pipe-control): handle missing target Secret
docs: clarify backup prerequisites
```

## Security

See [`SECURITY.md`](SECURITY.md). Never report or commit secrets.
