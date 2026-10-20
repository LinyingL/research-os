# Changelog

## 0.2.0 — 2026-10-20

### Stata support

- Read `.do` files to link scripts to their data inputs, data outputs and
  exported figures, including resolvable macros and working-folder changes.
- Suggest project folders for globals and create the dependent links after
  a person confirms the folder. Unresolved paths stay visible with a reason.
- Inspect `.dta` row counts, variables, labels and storage types from the
  header, without reading data rows. Track structural changes between versions.
- Connect `.dta` readers and writers across Stata, Python and R pipelines.

Scanning does not execute Stata. `.ado` files are listed as scripts; parsing
their file relationships, interpreting Stata result tables and logs, and
building variable cards from `gen`/`egen` are outside this release's Stata support.

### Local-model assistance

- Generate an attempt's description and result from up to five chosen text
  files using LM Studio or another OpenAI-compatible server.
- Fill empty fields only, identify their source files and leave unsupported
  content empty. Review and edit the draft before saving or submitting it.
- Configure the server address and model in the form. The destination is
  shown before file contents are sent; the default is this Mac.
- Keep verdicts, reasons and approval decisions under the researcher's control.

The model is optional. Reading code does not establish execution or an
observed result; include a text log or results file to draft findings.

### Fixes included

- Accept a model server's root address and add `/v1` automatically.
- Handle LM Studio responses that put complete, schema-valid JSON in
  `reasoning_content` while leaving the final answer empty.
- Restrict draft citations to selected file paths, including Chinese names,
  and reject extra fields and unsupported numbers.

### Installation and updates

The macOS app is built from source on your Mac. Install this version with:

```bash
uv tool install git+https://github.com/LinyingL/research-os@v0.2.0
researchos app
```

For an existing uv installation, close the app first and use:

```bash
uv tool install --force git+https://github.com/LinyingL/research-os@v0.2.0
researchos app
```

The update uses the existing project and machine-local records. It does not
require reinitializing projects or joining teams again.
