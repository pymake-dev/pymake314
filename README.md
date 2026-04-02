# pymake314

A pure-Python Makefile interpreter. Use `make` without installing `make`.

```
pip install pymake314
```

No dependencies. Works on any OS where Python 3.8+ is available — including
environments where you cannot install system packages.

---

## CLI usage

```bash
pymake                        # run the default target from ./Makefile
pymake build                  # run a specific target
pymake build test             # run multiple targets in order
pymake -n build               # dry run — print commands, don't execute
pymake -s test                # silent — don't echo commands
pymake -k test                # keep going after errors
pymake -i test                # ignore all errors
pymake -f path/to/Makefile    # use a specific Makefile
pymake -C path/to/dir         # change to directory before doing anything
pymake CC=clang build         # override a variable
pymake -p                     # print parsed variable/rule database and exit
```

## Python API

```python
from pymake import run

rc = run()                                        # default target, ./Makefile
rc = run("build", "test")                         # multiple targets
rc = run("all", makefile="path/to/Makefile")      # custom Makefile path
rc = run("build", variables={"CC": "clang"})      # variable overrides
rc = run("all", dry_run=True)                     # dry run
rc = run("all", silent=True)                      # silent mode
rc = run("all", ignore_errors=True)               # ignore errors
rc = run("all", keep_going=True)                  # keep going after errors
```

`run()` returns `0` on success and a non-zero exit code on failure.  
It raises `FileNotFoundError` if the Makefile is missing, and `RuntimeError`
on dependency cycles or missing targets.

---

## Supported Makefile features

| Feature | Supported |
|---|---|
| `=` recursive variables | ✅ |
| `:=` / `::=` immediate variables | ✅ |
| `?=` conditional assignment | ✅ |
| `+=` append | ✅ |
| `!=` shell assignment | ✅ |
| `$(VAR)` / `${VAR}` expansion | ✅ |
| `$(VAR:.c=.o)` suffix substitution references | ✅ |
| `$(VAR:%.c=%.o)` pattern substitution references | ✅ |
| `.PHONY` targets | ✅ |
| Pattern rules (`%.o: %.c`) | ✅ |
| Automatic variables `$@` `$<` `$^` `$*` | ✅ |
| `@` silent prefix | ✅ |
| `-` ignore-errors prefix | ✅ |
| `include` / `-include` | ✅ |
| Backslash line continuation | ✅ |
| Inline `#` comments | ✅ |
| File timestamp (mtime) up-to-date checks | ✅ |
| `-C` directory flag | ✅ |
| `-n` dry run | ✅ |
| `-s` silent mode | ✅ |
| `-k` keep going | ✅ |
| `-i` ignore errors | ✅ |
| `ifeq` / `ifdef` conditionals | 🔜 planned |
| `$(foreach ...)` / `$(call ...)` | 🔜 planned |
| Parallel jobs (`-j`) | 🔜 planned |

---

## Why pymake?

Some environments — CI containers, restricted servers, embedded systems,
Windows machines — don't have `make` available and you can't install it.
`pymake` gives you the same workflow (`make build`, `make test`, `make clean`)
with nothing but Python.

---

## License

See [LICENSE](LICENSE) for details.
