# Toolkit workspace

`odw` itself and the workspace tools: plan, audit, sync, rename.

## Working here

- After changing `crates/odw-dispatch`, rebuild the shared `odw` (`setup.sh`, or `cargo build --release --locked --manifest-path crates/odw-dispatch/Cargo.toml --target-dir target` from this folder); `tools.toml` changes need no rebuild. When the crate's exports change, ship the parser first and rebuild before any data uses them.
- A tool that commits runs git in the environment a plain `git commit` has (`audit.hook_env`), never its own runtime.
- Tests run from the tools root (`uv run pytest`), under the root's `conftest.py`.
