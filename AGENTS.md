# Development contracts

- Product: one original virtual adult character, multiple outfits, a shared dance timeline,
  automatic code-driven rendering. No manual video-editing requirement.
- User environment: Windows PowerShell, Python 3.12, no Docker/WSL/admin dependency.
- Use dataclasses and stdlib configuration. Keep the dependency surface small.
- Run `python -m pytest -q` and `python -m localmodel demo` before claiming the renderer works.
- Production providers must call documented endpoints. A test fixture is never AI generation.
- No cloud calls without the explicit `--allow-paid` gate. Never run paid generation in tests/CI.
- Never request API secrets in chat, commit them, print them or store them in project config.
- Preserve a common source timeline across all looks. Segment two must use its absolute
  source interval, not restart its clip from t=0.
- Never claim that equal seeds, equal fps or passing API tests prove identity/pose consistency.
- Keep user media, weights, generated outputs and request state outside Git.
- Do not silently loop short clips, freeze frames, reset request IDs or retry paid POSTs.
- Preserve existing files. Changed generation inputs require a new experiment workdir.
- Keep README and verification reports honest: Linux tests are not Windows verification,
  HTTP test doubles are not a successful live model run, and demo is not an AI character.
