# Vendored: ProseMirror (writing editor, X.31.4)

Built outside the repo with esbuild; the repo holds only the output and the entry file. No npm in the repo.

| Package | Version | Licence |
|---|---|---|
| prosemirror-model | 1.25.12 | MIT |
| prosemirror-state | 1.4.4 | MIT |
| prosemirror-view | 1.42.6 | MIT |
| prosemirror-transform | 1.12.2 | MIT |
| prosemirror-history | 1.5.1 | MIT |
| prosemirror-keymap | 1.2.3 | MIT |
| prosemirror-commands | 1.7.2 | MIT |
| prosemirror-schema-list | 1.5.1 | MIT |
| prosemirror-inputrules | 1.5.1 | MIT |
| prosemirror-dropcursor | 1.8.4 | MIT |
| prosemirror-gapcursor | 1.4.1 | MIT |
| esbuild (build tool only, not shipped) | 0.28.2 | MIT |

Files:
- `prosemirror.bundle.js` (sha256 `33beb5868504fda251825bb29ad46ab0566bc4586aa191550a503e1e0273c0d1`): one ESM file. Each package is a namespace: `model`, `state`, `view`, `transform`, `history`, `keymap`, `commands`, `schemaList`, `inputrules`, `dropcursor`, `gapcursor`.
- `prosemirror.css` (sha256 `35fe9adcbd3ac6aaeb95d4f8c2b81ee4107c888e4ee329c8e3071e6a3f997175`): `prosemirror-view/style/prosemirror.css` followed by `prosemirror-gapcursor/style/gapcursor.css`.
- `entry.js`: the entry that esbuild bundled (the namespace re-exports above).

Rebuild (in an empty folder outside the repo):

```bash
npm install --save-exact prosemirror-model@1.25.12 prosemirror-state@1.4.4 prosemirror-view@1.42.6 prosemirror-transform@1.12.2 prosemirror-history@1.5.1 prosemirror-keymap@1.2.3 prosemirror-commands@1.7.2 prosemirror-schema-list@1.5.1 prosemirror-inputrules@1.5.1 prosemirror-dropcursor@1.8.4 prosemirror-gapcursor@1.4.1 esbuild@0.28.2
cp <repo>/backend/app/web/vendor/prosemirror/entry.js .
./node_modules/.bin/esbuild entry.js --bundle --format=esm --target=es2020 --minify --legal-comments=none --outfile=prosemirror.bundle.js
cat node_modules/prosemirror-view/style/prosemirror.css node_modules/prosemirror-gapcursor/style/gapcursor.css > prosemirror.css
```

Checks: no `eval` and no `new Function` in the bundle (CSP `script-src 'self'`). ProseMirror's own `innerHTML` calls are internal to the library and are not restricted by the CSP here. Our code never uses `innerHTML`.

Versions were chosen at least 14 days after publication (2026-10-13). Upgrading means a new entry in this table, new hashes, and a re-run of `node --test` on the editor tests.
