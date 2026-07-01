# D3 v7 custom bundle

`d3.v7.custom.min.js` is a self-hosted cherry-pick of D3 v7 covering only
the modules used by the interactive docs layer. It exposes one global
`d3` symbol.

## Modules

The bundle uses **explicit named re-exports** rather than `export *`, so
Rollup's tree-shaker can drop unused d3-scale entry points (`scaleTime`,
`scaleUtc`) and their `d3-time` / `d3-time-format` transitive dependencies.
The d3-scale dependency is on **v4**; tree-shaking is what trims the bundle,
not the major-version pin (v3 has the same `d3-time` transitives).

Symbols re-exported, by source module:

- `d3-array`: `extent`, `max`, `bisector`
- `d3-axis`: `axisBottom`, `axisLeft`
- `d3-scale` (v4): `scaleLinear`, `scaleLog`, `scaleOrdinal`
- `d3-selection`: `select`, `selectAll`, `pointer`
- `d3-shape`: `line`, `area`, `curveCatmullRom`
- `d3-transition`: `transition`
- `d3-format`: `format`

`d3-color` and `d3-interpolate` are not directly re-exported; Rollup
still bundles the parts of them that d3-scale depends on internally. If
a future widget calls those symbols directly, add them to `entry.js`.

## Why self-hosted

Self-hosted because CDN-hosted JS would be the only outgoing network
call the rendered docs make, and the rest of the interactive layer was
designed to run from `_static/` alone. Keeping D3 alongside the rest of
the bundled assets means the docs render identically offline, on a
review preview, and in production.

## Rebuilding

Requires Node 18+ and npm 9+ (tested with Node 20 / npm 10).

```bash
mkdir -p /tmp/d3-custom && cd /tmp/d3-custom
npm init -y
npm install --save-dev rollup @rollup/plugin-node-resolve @rollup/plugin-terser
npm install d3-array@3 d3-axis@3 d3-color@3 d3-format@3 \
            d3-interpolate@3 d3-scale@4 d3-selection@3 d3-shape@3 \
            d3-transition@3
```

Create `entry.js` (explicit re-exports — required for the tree-shaker
to drop `scaleTime` / `scaleUtc` and the `d3-time` chain):

```javascript
export { extent, max, bisector } from "d3-array";
export { axisBottom, axisLeft } from "d3-axis";
export { scaleLinear, scaleLog, scaleOrdinal } from "d3-scale";
export { select, selectAll, pointer } from "d3-selection";
export { line, area, curveCatmullRom } from "d3-shape";
export { transition } from "d3-transition";
export { format } from "d3-format";
```

Create `rollup.config.mjs`:

```javascript
import resolve from "@rollup/plugin-node-resolve";
import terser from "@rollup/plugin-terser";

export default {
  input: "entry.js",
  output: { file: "d3.v7.custom.min.js", format: "iife", name: "d3" },
  plugins: [resolve(), terser()],
};
```

Build and copy:

```bash
npx rollup -c
cp d3.v7.custom.min.js \
   <repo>/docs/source/_static/js/lib/d3.v7.custom.min.js
```

After copying, verify the bundle still loads and exposes every symbol
listed under **Modules** above. A headless sanity check:

```bash
node -e '
  const fs = require("fs"), vm = require("vm");
  const code = fs.readFileSync(
    "docs/source/_static/js/lib/d3.v7.custom.min.js", "utf-8");
  const s = { console };
  vm.createContext(s); vm.runInContext(code, s);
  const ok = ["scaleLinear","scaleLog","scaleOrdinal","axisBottom",
              "axisLeft","line","area","curveCatmullRom","select",
              "selectAll","pointer","extent","max","bisector",
              "format","transition"].every(k => typeof s.d3[k] === "function");
  if (!ok) { console.error("symbol missing"); process.exit(1); }
  console.log("OK", code.length);
'
```

## Version pin policy

D3 v7 is pinned intentionally. Before upgrading to v8 (or any other
major), audit symbol compatibility against every consumer under
`docs/source/_static/js/plots/` and `widgets/`, rebuild from scratch
with the recipe above, and re-run the headless sanity check. There is
no `package-lock.json` for this bundle — the pinned `@3` / `@4` ranges
in the recipe are the lockfile.
