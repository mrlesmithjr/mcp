# ynab-tools dashboard frontend

React + TypeScript + Vite frontend for the ynab-tools dashboard. The dashboard backend (FastAPI) is part of the `ynab-tools` package and serves the built assets from `dist/`.

## Build

```bash
npm install
npm run build      # outputs to dist/, which ships inside the Python wheel
```

After building, reinstall the package so the backend picks up the new assets:

```bash
# from packages/ynab-tools
uv tool install --editable ".[dashboard]"
ynab dashboard restart
```

## Develop

```bash
npm run dev        # Vite dev server with HMR
```

See [../../../docs/dashboard.md](../../../docs/dashboard.md) for install, service management, and the view reference.
