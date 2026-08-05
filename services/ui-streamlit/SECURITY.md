# Security

## Deployment Hardening Checklist

Before deploying ui-streamlit to production, verify every item below:

### Environment & Secrets

- [ ] `.env` file is NOT committed (verify `.gitignore` excludes `.env*`)
- [ ] All secrets use `SecretStr` (never logged in plaintext)
- [ ] `gitleaks` pre-commit hook is active (detects accidental secret commits)
- [ ] No hardcoded credentials in source code

### Configuration

- [ ] `settings.yaml` ↔ `data_model.py` are in sync (run `make generate-data-model` / the `*-config-drift-check` script)
- [ ] All mandatory fields have regex validation in `settings.yaml`
- [ ] `fail_on_missing: true` in global settings (app fails fast on missing vars)

### API & Network

- [ ] HTTP request timeouts configured for all external API calls
- [ ] Rate limiting / retry logic with exponential backoff enabled where applicable
- [ ] No debug logging of user input/PII in production
- [ ] Server host is NOT `0.0.0.0` unless behind a reverse proxy

### Subprocess Safety

- [ ] All subprocess calls use argument lists (never `shell=True`)
- [ ] Executable paths validated with `shutil.which()` before execution

### Dependencies

- [ ] `uv.lock` reviewed for unexpected changes
- [ ] No known CVEs in dependency tree (`uv pip audit` or equivalent)
- [ ] Pre-commit hooks enforced in CI

---

## Known Patterns & Mitigations

### Secret Logging Prevention

All secrets use Pydantic `SecretStr`. When displaying:
```python
val = "****" + secret.get_secret_value()[-4:] if secret else "None"
```

### Env Drift Detection

Generated `data_model.py` contains a SHA-256 hash of the source YAML.
Run the `*-config-drift-check` console script (wired into `make qa`) to verify sync. CI should include this check.

---

## Reporting Vulnerabilities

If you discover a security issue, please report it responsibly via a private issue
or email (dev@ai-circus-framework.local) rather than public disclosure. See [CONTRIBUTING.md](CONTRIBUTING.md).
