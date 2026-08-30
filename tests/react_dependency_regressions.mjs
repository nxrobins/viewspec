// Behavioral dependency regressions using synthetic files only. No user source,
// credentials, or external network is involved in these probes.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";

const seeds = process.argv.slice(2);
assert.ok(seeds.length > 0, "Pass one or more installed dependency seed directories.");

for (const seed of seeds) {
  const directory = path.resolve(seed);
  const require = createRequire(path.join(directory, "package.json"));
  const postcss = require("postcss");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "viewspec-js-regression-"));
  try {
    const project = path.join(root, "project");
    fs.mkdirSync(project);
    const privateMap = path.join(root, "outside.map");
    const marker = "viewspec-synthetic-outside-map-marker";
    const map = content => JSON.stringify({ version: 3, sources: ["source.css"], sourcesContent: [content], names: [], mappings: "AAAA" });
    fs.writeFileSync(privateMap, map(marker));
    const input = path.join(project, "input.css");
    fs.writeFileSync(input, "a{color:red}");
    fs.symlinkSync(privateMap, path.join(project, "alias.map"));
    const blocked = [
      [privateMap, undefined],
      [privateMap, input],
      ["../outside.map", input],
      ["alias.map", input],
    ];
    for (const [annotation, from] of blocked) {
      const result = await postcss([]).process(`a{color:red}\n/*# sourceMappingURL=${annotation} */`, {
        from, to: path.join(project, "output.css"), map: { inline: false, annotation: false },
      });
      assert.ok(!result.map?.toString().includes(marker), "PostCSS disclosed a synthetic out-of-tree source map.");
    }
    const validMarker = "viewspec-synthetic-valid-map-marker";
    fs.writeFileSync(path.join(project, "local.map"), map(validMarker));
    const valid = await postcss([]).process("a{color:red}\n/*# sourceMappingURL=local.map */", {
      from: input, to: path.join(project, "output.css"), map: { inline: false, annotation: false },
    });
    assert.ok(valid.map?.toString().includes(validMarker), "A valid same-directory source map stopped working.");

    // A vulnerable generator can loop forever. Keep this regression in a small,
    // time-bounded child so a future dependency regression cannot hang CI.
    execFileSync(process.execPath, ["--max-old-space-size=64", "-e", `
      const assert = require('node:assert/strict');
      const {createRequire} = require('node:module');
      const {resolve} = require('node:path');
      const load = createRequire(resolve(process.argv[1], 'package.json'));
      const secure = load('nanoid');
      const insecure = load('nanoid/non-secure');
      assert.equal(insecure.nanoid(-1), '');
      assert.equal(insecure.customAlphabet('abc', -1)(), '');
      assert.equal(secure.customAlphabet('abc', 0)(), '');
      assert.equal(secure.customRandom('abc', 0, size => Buffer.alloc(size))(), '');
      assert.throws(() => secure.nanoid(-1), RangeError);
      assert.equal(secure.nanoid(8).length, 8);
      assert.equal(secure.customAlphabet('abc', 6)().length, 6);
    `, directory], { timeout: 3000, maxBuffer: 8192, killSignal: "SIGKILL", stdio: "pipe" });
    console.log(JSON.stringify({ seed, status: "passed", source_map_boundaries: blocked.length,
      same_directory_map: "passed", bounded_id_generation: "passed" }));
  } finally {
    // This exact directory was created above and contains only our synthetic fixtures.
    fs.rmSync(root, { recursive: true, force: true });
  }
}
