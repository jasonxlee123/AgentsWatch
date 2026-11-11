## Summary

Describe the user-visible change and the problem it solves.

## Verification

List the checks you ran:

```bash
python3 -m unittest discover -s tests -v
npm --prefix frontend test
npm --prefix frontend run build
docker compose config --quiet
python3 scripts/check_public_release.py --history
```

## Checklist

- [ ] No credentials, real inventories, private DSNs, or production data are included.
- [ ] Documentation was updated when configuration, deployment, or API behavior changed.
- [ ] New or changed behavior has test coverage, or the gap is explained.
- [ ] The change stays within the documented module boundaries.
