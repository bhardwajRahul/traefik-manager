# Screenshot rig

Recaptures every desktop screenshot for the docs and README from a seeded
demo environment, in both themes, at 1920x1080 in the Modern layout, which uses the full frame.

```bash
scripts/screenshots/run.sh                 # shoot ghcr.io/chr0nzz/traefik-manager:beta
scripts/screenshots/run.sh ghcr.io/chr0nzz/traefik-manager:latest
scripts/screenshots/run.sh --setup         # also retake the setup wizard pages
scripts/screenshots/run.sh --login         # also retake the login page
scripts/screenshots/run.sh --auth          # both of the above
```

The setup wizard and login page are skipped by default - their screenshots
rarely change, and each needs its own app restart with different settings.
Pass `--setup`, `--login` or `--auth` to retake them.

What it does:

1. Seeds demo data: 20 routes named after real apps (so dashboard groups and
   icons resolve), middlewares, multi-backend/TCP/UDP/shared-service examples,
   a weighted and a mirroring service (one of them managed by Traefik Manager,
   so the ownership and edit paths are shot), six self-signed certificates with
   staggered expiries, and a generated JSON access log (`gen_data.py`).
2. Boots Traefik v3.6 + Traefik Manager on a private docker network so live
   stats, entry points and router status are real. Traefik gets a copy of the
   dynamic config without `certResolver` so no router shows an ACME error.
3. Logs in through the real login page (password `screenshots`) and drives
   headless Chrome through every tab, view mode, modal and settings panel in
   dark and light (`capture.mjs` - 40 views per theme). Certificate removal is
   shot as far as the confirmation, which is then cancelled, so nothing is
   deleted and Traefik is never restarted.
4. Resizes to 1920x1080, installs into `docs/public/images/` under the
   existing names, and rebuilds both README carousel GIFs
   (`install_images.py`).

Not covered: all mobile screenshots. Everything runs in throwaway containers
and a temp dir; review the git diff before committing.
