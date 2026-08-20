// Conventional Commits enforcement for PR commit messages (.github/workflows/ci.yml's
// `commitlint` job, via wagoid/commitlint-github-action). type-enum below matches
// styleguide.md's documented type list exactly, which differs from
// @commitlint/config-conventional's defaults (no chore/build/ci here; adds
// setup/release/clean instead) — see styleguide.md "Type" section.
export default {
  extends: ["@commitlint/config-conventional"],
  rules: {
    "type-enum": [
      2,
      "always",
      [
        "feat",
        "fix",
        "docs",
        "style",
        "refactor",
        "test",
        "setup",
        "release",
        "perf",
        "revert",
        "clean",
      ],
    ],
    // styleguide.md requires the subject start with a capital letter, which conflicts
    // with config-conventional's default lower-case subject rule — disable that rule
    // rather than fight it with commitlint's stricter case presets.
    "subject-case": [0],
    "header-max-length": [2, "always", 72],
  },
};
