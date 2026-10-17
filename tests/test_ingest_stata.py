"""Tests for researchos.ingest.stata and its use by researchos.ingest.dataflow
(DESIGN.md 15.1-15.3, task V11 S1). Every do-file here is written for the
test; none is taken from a replication package."""

from __future__ import annotations

from researchos import db
from researchos.ingest import dataflow, stata
from researchos.ingest import scan as scan_mod


def _calls(text):
    found = stata.scan_text(text, "t.do")
    return [(c.line, c.command, c.kind, c.literal) for c in found.calls]


def _skips(text):
    return [(s.line, s.command, s.reason) for s in stata.scan_text(text, "t.do").skipped]


# -- the lexer -------------------------------------------------------------------


def test_comments_are_removed_and_lines_keep_the_file_numbers():
    text = (
        "* a star comment save \"no1\" ///\n"  # 1: continued onto line 2
        "  still the comment: save \"no2\"\n"  # 2
        "/* block /* nested */ save \"no3\"\n"  # 3
        "   save \"no4\" */ use \"a\"\n"  # 4: the block ends, the command starts
        "save \"b\" // trailing save \"no5\"\n"  # 5
        "use \"http://example.org/x\"\n"  # 6: // inside a string is text
        "// save \"no6\"\n"  # 7
        "* old /* opens a block in a star comment\n"  # 8
        "save \"no7\" */\n"  # 9
        "save \"c\", ///\n"  # 10: continuation
        "   replace\n"  # 11
    )
    cmds, problem = stata.commands(text)
    assert problem is None
    assert [(c.line, c.text) for c in cmds] == [
        (4, 'use "a"'), (5, 'save "b"'), (6, 'use "http://example.org/x"'), (10, 'save "c",     replace'),
    ]
    assert _calls(text)[:2] == [(4, "use", "read", "a.dta"), (5, "save", "write", "b.dta")]


def test_crlf_line_endings_and_compound_quotes():
    text = '\ufeffuse `"data/with "quote" and // slashes.dta"\', clear\r\nsave "out"\r\n'
    assert _calls(text) == [
        (1, "use", "read", 'data/with "quote" and // slashes.dta'),
        (2, "save", "write", "out.dta"),
    ]


def test_delimit_semicolon_and_back_to_cr():
    text = (
        "#delimit ;\n"  # 1
        "use\n"  # 2
        '  "data/a" ;\n'  # 3
        '* a star comment ; save "b"\n'  # 4: the comment ends at ;
        "  , replace ;\n"  # 5
        "foreach x in a b {\n"  # 6: a block's brace ends its line
        '  save "c" ;\n'  # 7
        "} ;\n"  # 8
        "#d cr\n"  # 9
        'save "d"\n'  # 10
    )
    found = stata.scan_text(text, "t.do")
    assert found.parsed
    assert [(c.line, c.command, c.literal) for c in found.calls] == [
        (2, "use", "data/a.dta"), (4, "save", "b.dta"), (7, "save", "c.dta"), (10, "save", "d.dta"),
    ]


# -- commands at a command's start only ----------------------------------------------


def test_prefixes_blocks_and_abbreviations_from_the_manual():
    text = (
        "qui cap noi u data/a\n"
        'by id, sort: merge 1:1 id using "data/b", keep(3)\n'
        'bysort id (t): append using "data/c"\n'
        'version 16: joinby id using "data/d"\n'
        'capture: cross using "data/e"\n'
        "if `flag' {\n"
        '    sav "out/f"\n'
        "}\n"
        "else {\n"
        '    gr export "fig/g.png", replace\n'
        "}\n"
        'else saveold "out/h", version(13)\n'
        'mer m:1 id using "data/i"\n'
        'ap using "data/j"\n'
        'import delim "raw/k.csv", clear\n'
        'import exc using "raw/l.xlsx", firstrow\n'
        'export delim using "out/m.csv" if x > 1, replace\n'
        'export excel x y using "out/n.xlsx"\n'
        'insheet using "raw/o.csv", comma\n'
        'outs using "out/p.csv"\n'
        'inf x y using "raw/q.raw"\n'
        'infix x 1-2 using "raw/r.dat", clear\n'
    )
    assert _calls(text) == [
        (1, "use", "read", "data/a.dta"),
        (2, "merge", "read", "data/b.dta"),
        (3, "append", "read", "data/c.dta"),
        (4, "joinby", "read", "data/d.dta"),
        (5, "cross", "read", "data/e.dta"),
        (7, "save", "write", "out/f.dta"),
        (10, "graph export", "write", "fig/g.png"),
        (12, "saveold", "write", "out/h.dta"),
        (13, "merge", "read", "data/i.dta"),
        (14, "append", "read", "data/j.dta"),
        (15, "import delimited", "read", "raw/k.csv"),
        (16, "import excel", "read", "raw/l.xlsx"),
        (17, "export delimited", "write", "out/m.csv"),
        (18, "export excel", "write", "out/n.xlsx"),
        (19, "insheet", "read", "raw/o.csv"),
        (20, "outsheet", "write", "out/p.csv"),
        (21, "infile", "read", "raw/q.raw"),
        (22, "infix", "read", "raw/r.dat"),
    ]


def test_command_words_elsewhere_and_non_abbreviations_are_not_commands():
    text = (
        "gen use = 1\n"
        'local save "x.dta"\n'
        'merge 1:1 id using "data/a", keepusing(save use)\n'
        'display "use b.dta"\n'
        's "c"\n'  # s is not an abbreviation of save
        'me 1:1 id using "d"\n'  # nor me of merge
        'a using "e"\n'  # nor a of append
        'g export "f.png"\n'  # g is generate, not graph
        'saveol "g"\n'  # saveold has no abbreviation
        "foreach save in a b {\n"
        "}\n"
        "mata:\n"
        '  use = "h"\n'
        "end\n"
        "USE i\n"  # commands are lower case
    )
    assert _calls(text) == [(3, "merge", "read", "data/a.dta")]


def test_use_varlist_using_and_append_of_several_files():
    text = 'use id x if x > 0 using "data/a", clear\nappend using "b" c.dta "d d", force\n'
    assert _calls(text) == [
        (1, "use", "read", "data/a.dta"),
        (2, "append", "read", "b.dta"), (2, "append", "read", "c.dta"), (2, "append", "read", "d d.dta"),
    ]


def test_default_extension_only_for_the_dta_commands():
    text = (
        'import delimited "raw/a"\n'  # not a .dta command: kept as written
        'graph export "fig/b"\n'
        'save "out/v1.2/c"\n'  # a dot in a folder is not an extension
        'use "data/d.csv"\n'  # an extension is kept
    )
    assert [c[3] for c in _calls(text)] == ["raw/a", "fig/b", "out/v1.2/c.dta", "data/d.csv"]


def test_backslashes_are_separators_except_before_dollar_and_backtick():
    text = 'use "..\\input\\a.dta"\nsave "out\\\\b"\nuse "data\\$c.dta"\nsave "C:\\Users\\me\\d"\n'
    assert [c[3] for c in _calls(text)] == ["../input/a.dta", "out//b.dta", "data$c.dta", "C:/Users/me/d.dta"]


# -- skipped and logged ---------------------------------------------------------------


def test_every_skip_is_logged_with_its_reason(caplog):
    text = (
        "tempfile t1 t2\n"  # 1
        "save `t1'\n"  # 2
        'use "`t2\'", clear\n'  # 3
        'use "$root/a.dta"\n'  # 4
        'save "${out}/b"\n'  # 5
        "foreach v in x y {\n"  # 6
        '    save "out/`v\'"\n'  # 7
        "}\n"  # 8
        "sysuse auto, clear\n"  # 9
        "webuse nlswork\n"  # 10
        'esttab m1 using "t.tex", replace\n'  # 11
        'texsave using "u.tex"\n'  # 12
        'outreg2 using "v.doc"\n'  # 13
        'log using "w.log", text\n'  # 14
        "save, replace\n"  # 15
        "merge id using a b\n"  # 16
        'if "`g\'" == "x" save "inline"\n'  # 17
        'use "https://example.org/x.dta"\n'  # 18
        "esttab m1\n"  # 19: no file, nothing to say
        'use "caf\ufffd.dta"\n'  # 20: a character the file's encoding lost
    )
    with caplog.at_level("WARNING", logger="researchos.ingest.stata"):
        skips = _skips(text)
    assert skips == [
        (2, "save", stata.TEMPFILE), (3, "use", stata.TEMPFILE),
        (4, "use", stata.UNDEFINED), (5, "save", stata.UNDEFINED), (7, "save", stata.LOOP),
        (9, "sysuse", stata.BUILTIN_DATA), (10, "webuse", stata.BUILTIN_DATA),
        (11, "esttab", stata.OUTPUTS), (12, "texsave", stata.OUTPUTS), (13, "outreg2", stata.OUTPUTS),
        (14, "log using", stata.OUTPUTS),
        (15, "save", stata.NO_FILE), (16, "merge", stata.SHAPE),
        (17, "save", stata.INLINE_IF), (18, "use", stata.WEB), (20, "use", stata.ENCODING),
    ]
    assert len([r for r in caplog.records if "not guessing" in r.message]) == len(skips)
    assert _calls(text) == []


def test_a_project_program_is_never_read_as_a_stata_command():
    ado = "program define sa\n  syntax anything\nend\n"
    programs = stata.program_names(ado) | stata.program_names("capture program drop x\npr de y, rclass\nend\n")
    assert programs == {"sa", "y"}
    found = stata.scan_text('sa "results"\nsave "out"\n', "t.do", programs=programs)
    assert [c.literal for c in found.calls] == ["out.dta"]
    assert [s.reason for s in found.skipped] == [stata.PROGRAM]


# -- what the lexer could not follow ------------------------------------------------


def test_lost_structure_makes_the_file_unparseable_but_keeps_its_calls():
    assert stata.scan_text('use "a"\n/* never closed\nsave "b"\n', "t.do").parsed is False
    unclosed = stata.scan_text('use "a"\nsave "b\n', "t.do")
    assert unclosed.parsed is False and [c.literal for c in unclosed.calls] == ["a.dta"]
    assert stata.scan_text('use "a"\nforeach x in a {\nsave "b"\n', "t.do").parsed is False
    assert stata.scan_text('use "a"\n}\nsave "b"\n', "t.do").parsed is False
    assert stata.scan_text('#delimit ;\nuse "a";\nsave "b"\n', "t.do").parsed is False
    assert stata.scan_text('#delimit\nuse "a"\n', "t.do").parsed is False
    assert stata.scan_text('if 1 {\nuse "a"\n}\nelse {\nsave "b"\n}\n', "t.do").parsed is True


# -- into the index -----------------------------------------------------------------------


def _project(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "raw.dta").write_bytes(b"<stata_dta>")
    (tmp_path / "code").mkdir()
    return tmp_path


def _scan(conn, root):
    from researchos.ingest import files as files_ingest

    inventory = files_ingest.list_source_files(root)
    with scan_mod.scan(conn, "test") as sc:
        sc.inventory(inventory)
        counts = dataflow.ingest_dataflow_repo(
            conn, root, inventory["py"], inventory["r"], inventory["rmd"], scan=sc,
            do_paths=inventory["do"], ado_paths=inventory["ado"],
        )
    return counts


def test_do_file_links_carry_evidence_and_the_canonical_command_as_basis(tmp_path, conn, caplog):
    root = _project(tmp_path)
    (root / "code" / "clean.do").write_text(
        'u "data/raw", clear\n'
        'gr export "figs/hist.png", replace\n'
        'save "C:\\Users\\me\\project\\out"\n'  # a Windows path: never a node
        'save "../../outside"\n'
    )
    with caplog.at_level("WARNING", logger="researchos.ingest"):
        for _ in range(2):  # idempotent
            assert _scan(conn, root) == {"reads": 1, "writes": 1}
    read = {"src": "script:code/clean.do", "dst": "dataset:data/raw.dta", "type": "reads", "extractor": "dataflow"}
    (edge,) = db.query_edges(conn, type="reads")
    assert edge["evidence"] == {"occurrences": [{"file": "code/clean.do", "line": 1, "callee": "use"}]}
    (write,) = db.query_edges(conn, type="writes")
    assert write["dst"] == "figure:figs/hist.png" and write["evidence"]["occurrences"][0]["missing"] is True
    assert db.get_node(conn, "script:code/clean.do")["type"] == "script"
    assert scan_mod.source_status(conn, read) == scan_mod.READ_AND_PARSED
    assert scan_mod.current_basis(conn, read) == {"calls": ["use"]}
    assert not conn.execute("SELECT 1 FROM nodes WHERE id LIKE '%C:%'").fetchall()
    assert sum("cannot be safely mapped into the repo" in r.message for r in caplog.records) == 4
    assert dataflow.reaches_outside(root, ["code/clean.do"]) == ["code/clean.do"]

    # a re-spelling is not a new fact (9.6): the basis stays {"calls": ["use"]}
    (root / "code" / "clean.do").write_text('use "data/raw.dta"\n')
    _scan(conn, root)
    assert scan_mod.current_basis(conn, read) == {"calls": ["use"]}


def test_unparseable_and_vendored_do_files_in_the_scan_report(tmp_path, conn, caplog):
    root = _project(tmp_path)
    (root / "code" / "broken.do").write_text('use "data/raw"\n/* never closed\n')
    lib = root / "code" / "libraries" / "stata"
    lib.mkdir(parents=True)
    (lib / "tool.ado").write_text('program define tool\nuse "data/raw"\nend\n')
    (lib / "helper.do").write_text('use "data/raw"\n')
    plus = root / "ado" / "plus" / "t"
    plus.mkdir(parents=True)
    (plus / "t.do").write_text('use "data/raw"\n')
    tracked = root / "vendor"
    tracked.mkdir()
    (tracked / "stata.trk").write_text("")
    (tracked / "x.do").write_text('use "data/raw"\n')
    (root / "code" / "own.ado").write_text('program define own\nuse "data/raw"\nend\n')
    with caplog.at_level("WARNING", logger="researchos.ingest"):
        _scan(conn, root)
    edges = db.query_edges(conn, type="reads")
    assert [e["src"] for e in edges] == ["script:code/broken.do"]  # written, as for R
    read = {"src": "script:code/broken.do", "dst": "dataset:data/raw.dta", "type": "reads", "extractor": "dataflow"}
    assert scan_mod.source_status(conn, read) == scan_mod.NOT_SCANNED  # never stamped
    # the project .ado and the vendored files are not dataflow sources
    rows = conn.execute("SELECT source, status FROM scan_sources WHERE extractor='dataflow'")
    assert {r["source"]: r["status"] for r in rows} == {"code/broken.do": scan_mod.UNPARSEABLE}
    vendored = sorted(r.message.split(":")[0] for r in caplog.records if "vendored Stata library" in r.message)
    assert vendored == [
        "ado/plus/t/t.do", "code/libraries/stata/helper.do", "code/libraries/stata/tool.ado", "vendor/x.do",
    ]
    rows = conn.execute("SELECT source FROM scan_sources WHERE extractor='inventory' AND source LIKE '%.%do'")
    assert sorted(r["source"] for r in rows) == [  # listed all the same
        "ado/plus/t/t.do", "code/broken.do", "code/libraries/stata/helper.do",
        "code/libraries/stata/tool.ado", "code/own.ado", "vendor/x.do",
    ]


# -- macros (DESIGN.md 15.3, task V11 S2) ----------------------------------------------------


def _read(texts, root=None):
    found = stata.scan_project(root, texts)
    calls = {rel: [(c.line, c.command, c.literal, c.folded_from) for c in s.calls] for rel, s in found.items()}
    skips = {rel: [(k.line, k.command, k.reason, k.macro, k.rest) for k in s.skipped] for rel, s in found.items()}
    return calls, skips


def test_a_global_folds_across_files_only_when_every_assignment_agrees(caplog):
    texts = {
        "config.do": (
            'gl data "data"\n'
            'global raw "${data}/raw"\n'
            'global out "out"\n'
            'global out out\n'  # the same literal again
            'global twice "a"\n'
            'global twice "b"\n'
            'global calc = "x"\n'
            'global win "C:\\Users\\me\\proj"\n'
        ),
        "clean.do": (
            'use "$raw/a"\n'  # 1
            'save "${out}/b", replace\n'  # 2
            'use "$twice/c"\n'  # 3
            'use "$calc/d"\n'  # 4
            'use "$nobody/e"\n'  # 5: never the empty string
            'use "${win}\\f"\n'  # 6: a root on someone's computer
            'use "data\\$raw"\n'  # 7: an escaped $ is text
        ),
    }
    with caplog.at_level("WARNING", logger="researchos.ingest.stata"):
        calls, skips = _read(texts)
    assert calls["clean.do"] == [
        (1, "use", "data/raw/a.dta",
         '$raw/a; global raw "${data}/raw" (config.do:2); global data "data" (config.do:1)'),
        (2, "save", "out/b.dta", '${out}/b; global out "out" (config.do:3)'),
        (7, "use", "data$raw.dta", None),
    ]
    assert skips["clean.do"] == [
        (3, "use", stata.REASSIGNED, "$twice", "c.dta"),
        (4, "use", stata.MACRO, "$calc", "d.dta"),
        (5, "use", stata.UNDEFINED, "$nobody", "e.dta"),
        (6, "use", stata.OUTSIDE, "$win", "f.dta"),
    ]
    assert "clean.do:6: use '${win}\\\\f': skipped (outside the project: $win), not guessing" in caplog.text


def test_a_local_is_its_files_and_folds_only_when_assigned_once_before_use():
    text = (
        'global base "data"\n'  # 1
        'local dir "$base/raw"\n'  # 2
        "use \"`dir'/a\"\n"  # 3
        "use \"`later'/b\"\n"  # 4: used before it is assigned
        'local later "x"\n'  # 5
        'local two "a"\n'  # 6
        'loc two "b"\n'  # 7
        "use \"`two'/c\"\n"  # 8
        "levelsof v, local(lv)\n"  # 9: an option assigns it too
        'local lv "y"\n'  # 10
        "use \"`lv'/d\"\n"  # 11
        "use \"`=c(pwd)'/e\"\n"  # 12
        "use \"`1'/f\"\n"  # 13
        "program define p\n"  # 14
        "  use \"`dir'/g\"\n"  # 15: a program has its own locals
        "end\n"  # 16
        "forvalues j = 1/3 {\n"  # 17
        "  append using \"`dir'/h`j'\"\n"  # 18
        "}\n"  # 19
        "tempname n\n"  # 20
        "use \"`n'\"\n"  # 21
    )
    calls, skips = _read({"t.do": text})
    assert calls["t.do"] == [
        (3, "use", "data/raw/a.dta",
         "`dir'/a; local dir \"$base/raw\" (t.do:2); global base \"data\" (t.do:1)"),
    ]
    assert [(line, reason, macro) for line, _c, reason, macro, _r in skips["t.do"]] == [
        (4, stata.UNDEFINED, "`later'"), (8, stata.REASSIGNED, "`two'"), (11, stata.REASSIGNED, "`lv'"),
        (12, stata.MACRO, "`=c(pwd)'"), (13, stata.MACRO, "`1'"), (15, stata.MACRO, "`dir'"),
        (18, stata.LOOP, "`j'"), (21, stata.MACRO, "`n'"),
    ]


def test_what_else_may_set_a_macro_keeps_it_from_folding():
    texts = {
        "lib.ado": 'program define setter\n  c_local made "q"\nend\n',
        "a.do": (
            "macro drop _all\n"  # 1: followed by its own assignment of keep
            'global keep "k"\n'  # 2
            'local made "m"\n'  # 3
            "use \"`made'/a\"\n"  # 4: c_local in a project program may set it
            'use "$keep/b"\n'  # 5
            'use "$other/c"\n'  # 6: dropped here, assigned in b.do
            "foreach v in x y {\n"  # 7
            "  global dyn_`v' \"d\"\n"  # 8: a name built from a macro
            "}\n"  # 9
            'use "$dyn_x/d"\n'  # 10
            'if "`c(username)\'" == "me" global who "w"\n'  # 11: under an inline if
            'global who "w"\n'  # 12
            'use "$who/e"\n'  # 13
        ),
        "b.do": 'global other "o"\ninclude "c.do"\nlocal set "f"\nuse "`set\'/g"\n',  # include may set it
    }
    calls, skips = _read(texts)
    assert calls["a.do"] == [(5, "use", "k/b.dta", '$keep/b; global keep "k" (a.do:2)')]
    assert [(line, reason, macro) for line, _c, reason, macro, _r in skips["a.do"]] == [
        (4, stata.MACRO, "`made'"), (6, stata.REASSIGNED, "$other"), (10, stata.MACRO, "$dyn_x"),
        (13, stata.REASSIGNED, "$who"),
    ]
    assert [(line, reason) for line, _c, reason, _m, _r in skips["b.do"]] == [(4, stata.MACRO)]
    assert "lib.ado" not in calls  # an .ado is read for its macros and programs only


def test_globals_into_the_index_with_their_fold_chain(tmp_path, conn, caplog):
    root = _project(tmp_path)
    (root / "code" / "master.do").write_text(
        f'global data "{root}/data"\n'  # inside the project: remapped
        'global out "results"\n'
        'global ext "C:\\Users\\someone\\proj"\n'
    )
    (root / "code" / "clean.do").write_text('use "$data/raw"\nsave "$out/clean"\nsave "${ext}/x"\n')
    with caplog.at_level("WARNING", logger="researchos.ingest"):
        assert _scan(conn, root) == {"reads": 1, "writes": 1}
    read = {"src": "script:code/clean.do", "dst": "dataset:data/raw.dta", "type": "reads", "extractor": "dataflow"}
    (edge,) = db.query_edges(conn, type="reads")
    assert edge["evidence"]["occurrences"] == [{
        "file": "code/clean.do", "line": 1, "callee": "use", "remapped_from_absolute": True,
        "folded_from": f'$data/raw; global data "{root}/data" (code/master.do:1)',
    }]
    assert scan_mod.current_basis(conn, read) == {"calls": ["use"]}  # the expansion is not a fact (9.6)
    (write,) = db.query_edges(conn, type="writes")
    assert write["dst"] == "dataset:results/clean.dta"
    assert not conn.execute("SELECT 1 FROM nodes WHERE id LIKE '%C:%'").fetchall()
    assert "skipped (outside the project: $ext), not guessing" in caplog.text
    assert dataflow.reaches_outside(root, ["code/clean.do", "code/master.do"]) == ["code/clean.do"]


def test_an_approval_reads_a_placed_do_file_with_the_projects_globals(tmp_path, conn):
    from researchos import proposals

    root = _project(tmp_path)
    (root / "code" / "master.do").write_text('global data "data"\n')
    (root / "code" / "new.do").write_text('use "$data/raw"\n')
    proposals._scan_placed(conn, root, [{"path": "code/new.do", "action": "added"}])
    assert [(e["src"], e["dst"]) for e in db.query_edges(conn, type="reads")] == [
        ("script:code/new.do", "dataset:data/raw.dta"),
    ]
