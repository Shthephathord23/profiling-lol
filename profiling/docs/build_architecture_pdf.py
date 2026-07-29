#!/usr/bin/env python3
"""Generate the profiling-harness architecture document."""

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, KeepTogether, NextPageTemplate, PageBreak,
    PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

OUT = str(__import__("pathlib").Path(__file__).resolve().parent / "architecture.pdf")

INK      = colors.HexColor("#1a1d21")
MUTED    = colors.HexColor("#5b6470")
RULE     = colors.HexColor("#d6dae0")
ACCENT   = colors.HexColor("#1f6feb")
CODEBG   = colors.HexColor("#f5f6f8")
CODEEDGE = colors.HexColor("#e2e5ea")
WARNBG   = colors.HexColor("#fff8e6")
WARNEDGE = colors.HexColor("#e8d59a")
OKBG     = colors.HexColor("#eef7ef")
OKEDGE   = colors.HexColor("#bcd9c0")

ss = getSampleStyleSheet()

def S(name, **kw):
    base = kw.pop("parent", ss["BodyText"])
    return ParagraphStyle(name, parent=base, **kw)

Body = S("Body", fontName="Helvetica", fontSize=9.6, leading=14.2,
         textColor=INK, spaceAfter=7, alignment=TA_LEFT)
Lead = S("Lead", parent=Body, fontSize=11, leading=16, textColor=MUTED, spaceAfter=11)
H1   = S("H1", fontName="Helvetica-Bold", fontSize=19, leading=23, textColor=INK,
         spaceBefore=0, spaceAfter=3)
H2   = S("H2", fontName="Helvetica-Bold", fontSize=13.5, leading=17, textColor=INK,
         spaceBefore=16, spaceAfter=6)
H3   = S("H3", fontName="Helvetica-Bold", fontSize=10.6, leading=14, textColor=INK,
         spaceBefore=11, spaceAfter=4)
Kick = S("Kick", fontName="Helvetica-Bold", fontSize=8, leading=11,
         textColor=ACCENT, spaceAfter=2)
Code = S("Code", fontName="Courier", fontSize=7.9, leading=10.4, textColor=INK,
         spaceBefore=0, spaceAfter=0)
CodeSm = S("CodeSm", parent=Code, fontSize=7.0, leading=9.1)
Cell = S("Cell", fontName="Helvetica", fontSize=8.4, leading=11.6, textColor=INK)
CellB = S("CellB", parent=Cell, fontName="Helvetica-Bold")
CellC = S("CellC", parent=Cell, fontName="Courier", fontSize=7.6, leading=10.6)
CellH = S("CellH", parent=Cell, fontName="Helvetica-Bold", fontSize=7.6,
          textColor=colors.white)
Note = S("Note", parent=Body, fontSize=9.0, leading=13, spaceAfter=0)
Cap  = S("Cap", parent=Body, fontSize=8.1, leading=11, textColor=MUTED, spaceAfter=10)

def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def p(t, style=Body):
    return Paragraph(t, style)

def code(text, small=False):
    # Paragraph collapses runs of whitespace, which destroys ASCII diagrams and
    # column alignment.  Non-breaking spaces preserve both (and stop wrapping,
    # which is what you want for code); the longest line here is 88 chars
    # against ~98 available, so nothing overflows the box.
    st = CodeSm if small else Code
    rows = [
        [Paragraph(esc(l).replace(" ", "&nbsp;") or "&nbsp;", st)]
        for l in text.strip("\n").split("\n")
    ]
    t = Table(rows, colWidths=[168 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CODEBG),
        ("BOX", (0, 0), (-1, -1), 0.5, CODEEDGE),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 0.6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0.6),
        ("TOPPADDING", (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 5),
    ]))
    return t

def callout(title, text, kind="warn"):
    bg, edge = (WARNBG, WARNEDGE) if kind == "warn" else (OKBG, OKEDGE)
    inner = [[Paragraph(f"<b>{title}</b>", Note)], [Paragraph(text, Note)]]
    it = Table(inner, colWidths=[160 * mm])
    it.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, 0), 0), ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
        ("TOPPADDING", (0, 1), (-1, 1), 0), ("BOTTOMPADDING", (0, 1), (-1, -1), 0),
    ]))
    t = Table([[it]], colWidths=[168 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.6, edge),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t

def table(header, rows, widths, mono_cols=(), align=None):
    data = [[Paragraph(esc(h), CellH) for h in header]]
    for r in rows:
        data.append([
            Paragraph(esc(c) if i in mono_cols else c,
                      CellC if i in mono_cols else Cell)
            for i, c in enumerate(r)
        ])
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafbfc")]),
    ]
    if align:
        style.extend(align)
    t.setStyle(TableStyle(style))
    return t

def spacer(h=5):
    return Spacer(1, h)

# ---------------------------------------------------------------- page frame

def decorate(canvas, doc, title="Profiling Harness — Architecture"):
    canvas.saveState()
    w, h = A4
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(21 * mm, h - 16 * mm, w - 21 * mm, h - 16 * mm)
    canvas.setFont("Helvetica", 7.2)
    canvas.setFillColor(MUTED)
    canvas.drawString(21 * mm, h - 14 * mm, title)
    canvas.drawRightString(w - 21 * mm, h - 14 * mm, "profiling/")
    canvas.line(21 * mm, 15 * mm, w - 21 * mm, 15 * mm)
    canvas.drawString(21 * mm, 11 * mm, "claude/profiling-harness-impl-h3hrjt")
    canvas.drawRightString(w - 21 * mm, 11 * mm, str(doc.page))
    canvas.restoreState()

def cover(canvas, doc):
    canvas.saveState()
    w, h = A4
    canvas.setFillColor(INK)
    canvas.rect(0, h - 88 * mm, w, 88 * mm, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 30)
    canvas.drawString(21 * mm, h - 46 * mm, "Profiling Harness")
    canvas.setFont("Helvetica", 17)
    canvas.setFillColor(colors.HexColor("#9fb3cc"))
    canvas.drawString(21 * mm, h - 57 * mm, "Code architecture, explained end to end")
    canvas.setFont("Courier", 8.6)
    canvas.setFillColor(colors.HexColor("#7f8ea3"))
    canvas.drawString(21 * mm, h - 72 * mm,
                      "2336 lines  /  7 Python modules  /  4 profilers  /  111 checks")
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(21 * mm, 15 * mm, w - 21 * mm, 15 * mm)
    canvas.setFont("Helvetica", 7.2)
    canvas.setFillColor(MUTED)
    canvas.drawString(21 * mm, 11 * mm, "claude/profiling-harness-impl-h3hrjt")
    canvas.restoreState()

doc = BaseDocTemplate(
    OUT, pagesize=A4,
    leftMargin=21 * mm, rightMargin=21 * mm,
    topMargin=22 * mm, bottomMargin=20 * mm,
    title="Profiling Harness - Architecture",
    author="Claude Code",
)
frame_cover = Frame(21 * mm, 20 * mm, A4[0] - 42 * mm, A4[1] - 108 * mm, id="cov",
                    leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
frame_body = Frame(21 * mm, 20 * mm, A4[0] - 42 * mm, A4[1] - 42 * mm, id="body",
                   leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
doc.addPageTemplates([
    PageTemplate(id="Cover", frames=[frame_cover], onPage=cover),
    PageTemplate(id="Body", frames=[frame_body], onPage=decorate),
])

F = []      # flowables
A = F.append

# ============================================================ COVER
A(Spacer(1, 4 * mm))
A(p("This document walks the harness from the command line down to the process "
    "that actually runs your workload. It follows execution order rather than "
    "file order, so each piece arrives when you need it.", Lead))
A(spacer(2))
A(table(
    ["Section", "What it covers"],
    [["1 - 2", "The shape of the system, and the file tree"],
     ["3 - 4", "Entry point, dispatch, and discovery"],
     ["5 - 6", "The two ideas everything rests on: environment layering and the target contract"],
     ["7 - 9", "The main loop, package init, and the bash harness"],
     ["10 - 12", "Process control, output artifacts, retention"],
     ["13 - 14", "Exit codes and the four profilers"],
     ["15 - 17", "Code-size analysis, testing, and judgement calls"]],
    [26 * mm, 142 * mm], mono_cols=(0,)))
A(spacer(9))
A(callout("How to read this",
          "Every claim about behaviour in this document was verified by running the "
          "code, not by reading it. Where a number appears (a race that hits 1 run in "
          "30, a 106 MB trace, a 10-second kill), it was measured. Section 17 lists "
          "the places where the implementation deliberately departs from the "
          "specification, and why.", "ok"))
A(NextPageTemplate("Body"))
A(PageBreak())

# ============================================================ 1. SHAPE
A(p("SECTION 1", Kick))
A(p("The shape of the system", H1))
A(p("A pipeline, not a framework", Cap))

A(p("The harness runs a project's workloads (<b>packages</b>) under measurement tools "
    "(<b>profilers</b>), writes timestamped artifacts, and reports a single exit code. "
    "Both packages and profilers are discovered from the filesystem, so adding either "
    "means creating a directory with two small files - never editing the core.", Body))

A(p("There is no plugin registry, no dependency injection, no class hierarchy. Each "
    "module owns one noun and hands its result to the next:", Body))

A(code("""
  run_profiling.sh          4-line shim: exec python3 lib/cli.py "$@"
         |
         v
  ....................  cli.py  ....................
  :  parse args -> dispatch -> loop over pairs     :
  :.................................................:
         |             |              |
         |             |              +--> retention.py    (--remove-output only)
         |             |
         |             +--> discovery.py   what packages and profilers exist?
         |                        |
         |                        +--> envfile.py   what do their .env files mean?
         |
         +--> initstate.py   does this package need building first?
         |
         +--> runner.py      build the argv, write target.sh, spawn ONE bash
         |          |
         |          +--> bash: common.sh + target.sh + package.sh + profiler.sh
         |                        |
         |                        +--> package_pre_run
         |                        +--> profiler_command   <-- builds the command
  |                        +--> "${cmd[@]}"       <-- the workload runs here
         |                        +--> profiler_post
         |                        +--> package_post_run   (EXIT trap)
         |
         +--> report.py      meta.json, summary.json, the 'latest' symlink
"""))

A(p("Data flows one way. <font face='Courier' size='8.6'>discovery</font> produces "
    "immutable value objects; <font face='Courier' size='8.6'>runner</font> consumes "
    "them and produces a result record; <font face='Courier' size='8.6'>report</font> "
    "serialises it. Nothing writes back up the chain. That is why the modules can be "
    "read in isolation.", Body))

A(p("The Python/bash split", H2))
A(p("Orchestration lives in Python; command composition lives in bash. The dividing "
    "line is deliberate and it is the single most important design decision in the "
    "tool.", Body))
A(table(
    ["Python does", "Bash does"],
    [["Argument parsing, discovery, environment layering, retention, exit-code "
      "aggregation, JSON output, process supervision",
      "Building the actual command line, per-package setup, per-profiler invocation"],
     ["Painful in bash: nested data, JSON, timeouts, signal handling",
      "Natural in bash: quoting, arrays, sourcing config, calling tools"]],
    [84 * mm, 84 * mm]))
A(p("The consequence for a user: <b>anyone adding a package writes only shell.</b> "
    "There is no Python plugin API to learn. Python 3 was already a hard requirement "
    "because three of the four shipped profilers are Python tools, so it costs "
    "nothing.", Body))
A(p("The core imports only the standard library - verified, not assumed: argparse, "
    "dataclasses, hashlib, json, os, pathlib, platform, re, secrets, shlex, shutil, "
    "signal, subprocess, sys, tempfile, threading, time, typing. Zero third-party "
    "packages.", Body))

A(PageBreak())

# ============================================================ 2. TREE
A(p("SECTION 2", Kick))
A(p("The file tree", H1))
A(p("What each file owns", Cap))

A(code("""
profiling/
  run_profiling.sh        the entry point (a shim)
  install.sh              discovery-driven dependency installer
  config.env              all paths and global defaults
  README.md               user-facing documentation
  .gitignore              output/ and .state/

  lib/                    the core - the only Python in the tree
    cli.py         701   argument parsing, dispatch, the main loop
    runner.py      613   the target contract, bash harnesses, process control
    discovery.py   244   enumerate packages/profilers, typed access to .env
    report.py      228   meta.json, summary.json, tables and JSON listings
    initstate.py   207   init fingerprinting and stamps
    retention.py   175   --remove-output planning and execution
    envfile.py     172   source .env layers, apply precedence
    common.sh            helpers sourced into every leaf hook

  packages/<name>/        a workload to measure
    .env                  declares it
    package.sh            optional hooks

  profilers/<name>/       a way to measure
    .env                  declares it
    profiler.sh           required profiler_command, optional profiler_post

  output/                 run artifacts        (gitignored)
  .state/                 package init stamps  (gitignored)
"""))

A(p("The discovery rule", H2))
A(p("A directory under <font face='Courier' size='8.6'>packages/</font> or "
    "<font face='Courier' size='8.6'>profilers/</font> is a valid entry <b>if and only "
    "if</b> it contains a <font face='Courier' size='8.6'>.env</font> file and its name "
    "does not start with an underscore. Names must match "
    "<font face='Courier' size='8.6'>[a-z0-9][a-z0-9._-]*</font>; anything else is "
    "rejected with a clear error rather than silently ignored.", Body))
A(p("The underscore prefix is what keeps <font face='Courier' size='8.6'>_template/</font> "
    "out of the listings while still letting you "
    "<font face='Courier' size='8.6'>cp -r</font> it. A directory without a "
    "<font face='Courier' size='8.6'>.env</font> is ignored entirely, so editor "
    "droppings and stray folders cause no trouble.", Body))

A(p("Why state and output are separate trees", H2))
A(p("<font face='Courier' size='8.6'>.state/</font> holds the answer to \"has this "
    "package been built?\". <font face='Courier' size='8.6'>output/</font> holds run "
    "artifacts. They are separate directories, and the init log is written to "
    "<font face='Courier' size='8.6'>.state/</font> rather than into a run directory, "
    "for one specific reason: <b>pruning artifacts must never trigger a rebuild.</b> "
    "If the build stamp lived under <font face='Courier' size='8.6'>output/</font>, "
    "then <font face='Courier' size='8.6'>--remove-output</font> would silently cost "
    "you a full rebuild on the next run.", Body))
A(p("It also means you can mount <font face='Courier' size='8.6'>.state/</font> as a "
    "Docker volume to carry \"already built\" across containers, while letting "
    "<font face='Courier' size='8.6'>output/</font> stay ephemeral.", Body))

A(PageBreak())

# ============================================================ 3. ENTRY
A(p("SECTION 3", Kick))
A(p("Entry point and dispatch", H1))
A(p("run_profiling.sh -> cli.py main()", Cap))

A(p("The shim", H2))
A(code("""
#!/usr/bin/env bash
set -euo pipefail
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/cli.py" "$@"
"""))
A(p("Four lines, and the <font face='Courier' size='8.6'>exec</font> matters. It "
    "<i>replaces</i> the shell process with Python rather than forking, so the "
    "process the user sees is the process doing the work. A SIGTERM sent to "
    "<font face='Courier' size='8.6'>run_profiling.sh</font> lands directly on the "
    "Python interpreter - which, as Section 10 explains, is what makes clean "
    "cancellation possible at all.", Body))

A(p("main()", H2))
A(p("<font face='Courier' size='8.6'>cli.py:657</font>. Four steps, in order:", Body))
A(table(
    ["#", "Step", "Why here"],
    [["1", "<font face='Courier' size='8'>parser.parse_args()</font>",
      "argparse exits 2 on a bad flag, which is already the code the spec wants"],
     ["2", "<font face='Courier' size='8'>_install_signal_handlers()</font>",
      "Before anything can spawn a child, so no window exists where a signal orphans work"],
     ["3", "<font face='Courier' size='8'>load_global_env()</font>",
      "Sources config.env alone. Even --remove-output needs PROFILING_OUTPUT_DIR"],
     ["4", "dispatch",
      "A flat if-chain, because three of four commands are terminal"]],
    [8 * mm, 54 * mm, 106 * mm]))

A(p("Terminal actions", H2))
A(p("<font face='Courier' size='8.6'>--list-packages</font>, "
    "<font face='Courier' size='8.6'>--list-profilers</font> and "
    "<font face='Courier' size='8.6'>--remove-output</font> each print and return. "
    "They never combine with a run, and combining two of them is a usage error. "
    "This is why dispatch is a plain if-chain rather than a command table: there are "
    "four commands, three of which are one-liners, and the fourth is the entire rest "
    "of the program.", Body))

A(code("""
if args.list_packages:              return cmd_list_packages(env, args.json)
if args.list_profilers:             return cmd_list_profilers(env, args.json)
if args.remove_output is not None:  return cmd_remove_output(args, env)

if args.keep is not None:   raise UsageError("--keep only with --remove-output")
if args.json:               raise UsageError("--json only with --list-*")
return cmd_run(args, env)
"""))
A(p("The two guards before <font face='Courier' size='8.6'>cmd_run</font> catch flags "
    "that parse fine but mean nothing in context. Silently ignoring "
    "<font face='Courier' size='8.6'>--keep 5</font> on a run would be worse than "
    "refusing it - the user clearly intended something.", Body))

A(p("The selector grammar", H2))
A(p("<font face='Courier' size='8.6'>--package</font> and "
    "<font face='Courier' size='8.6'>--profiler</font> both accept four spellings, "
    "handled once in <font face='Courier' size='8.6'>discovery.resolve_selection</font>:", Body))
A(code("""
--profiler time                     one name
--profiler time,py-spy              comma-separated
--profiler time --profiler py-spy   repeated flag  (argparse action="append")
--profiler all                      the literal 'all'
"""))
A(p("Mixing <font face='Courier' size='8.6'>all</font> with explicit names is an "
    "error, not a silent union - <font face='Courier' size='8.6'>--profiler all,time</font> "
    "almost certainly means the user misunderstands something.", Body))

A(spacer(3))
A(callout("--profiler is mandatory",
          "Omitting it while <font face='Courier' size='8.4'>--package</font> is present "
          "exits 2 with a message pointing at "
          "<font face='Courier' size='8.4'>--profiler all</font>. There is no implicit "
          "default sweep. Profiling every workload with every tool is expensive, and a "
          "harness that does it because you forgot a flag is a harness that burns CI "
          "minutes for no reason."))

A(PageBreak())

# ============================================================ 4. DISCOVERY
A(p("SECTION 4", Kick))
A(p("Discovery", H1))
A(p("discovery.py - turning directories into typed objects", Cap))

A(p("<font face='Courier' size='8.6'>discovery.py</font> does three things: scan the "
    "filesystem, load a <font face='Courier' size='8.6'>.env</font> stack into an "
    "environment dict, and wrap that dict in an object with typed accessors.", Body))

A(p("Scanning", H3))
A(code("""
def _scan(root, label, script_name) -> Dict[str, Entry]:
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith(("_", ".")):
            continue                      # _template and hidden dirs
        if not (child / ".env").is_file():
            continue                      # not an entry at all
        if not NAME_RE.match(child.name):
            raise DiscoveryError(...)     # a real mistake - say so
        found[child.name] = Entry(...)
"""))
A(p("<font face='Courier' size='8.6'>sorted()</font> gives deterministic order for "
    "free. The distinction between the two <font face='Courier' size='8.6'>continue</font> "
    "branches and the <font face='Courier' size='8.6'>raise</font> is deliberate: a "
    "directory with no <font face='Courier' size='8.6'>.env</font> is not an entry and "
    "is none of our business, but a directory that clearly <i>is</i> an entry and has "
    "an unusable name is a mistake worth stopping for.", Body))

A(p("Typed access", H3))
A(p("<font face='Courier' size='8.6'>Package</font> and "
    "<font face='Courier' size='8.6'>Profiler</font> are dataclasses wrapping the "
    "merged environment. Their properties are where defaults and parsing live, so no "
    "other module has to remember them:", Body))
A(code("""
@property
def args(self) -> List[str]:
    return shlex.split(self.env.get("PACKAGE_ARGS", ""))      # not .split()

@property
def timeout(self) -> Optional[float]:
    raw = (self.env.get("PACKAGE_TIMEOUT") or "").strip()
    if not raw: return None
    value = float(raw)                    # raises DiscoveryError on garbage
    return None if value <= 0 else value  # 0 means "no timeout"

def supports(self, kind: str) -> bool:
    return not self.kinds or kind in self.kinds   # empty = no restriction
"""))
A(p("<font face='Courier' size='8.6'>shlex.split</font> rather than "
    "<font face='Courier' size='8.6'>.split()</font> is what makes "
    "<font face='Courier' size='8.6'>PACKAGE_ARGS=\"--input 'my file.json'\"</font> "
    "work. This was tested directly against arguments containing spaces, single and "
    "double quotes, globs and dollar signs.", Body))

A(p("Selection and ordering", H2))
A(p("<font face='Courier' size='8.6'>resolve_selection</font> expands selectors into "
    "an ordered, de-duplicated list. Ordering follows one rule, applied independently "
    "to packages and profilers:", Body))
A(table(
    ["Given", "Order"],
    [["Explicit names", "As typed on the command line"],
     ["<font face='Courier' size='8'>all</font>", "Alphabetical"]],
    [50 * mm, 118 * mm]))
A(p("So <font face='Courier' size='8.6'>--profiler viztracer,time</font> runs viztracer "
    "first, while <font face='Courier' size='8.6'>--profiler all</font> runs a package's "
    "list alphabetically no matter how it is written in "
    "<font face='Courier' size='8.6'>PACKAGE_PROFILERS</font>. The listing output is "
    "sorted the same way, so a CI matrix built from "
    "<font face='Courier' size='8.6'>--list-packages --json</font> matches what the "
    "harness will actually do.", Body))

A(PageBreak())

# ============================================================ 5. ENV
A(p("SECTION 5", Kick))
A(p("Environment layering", H1))
A(p("envfile.py - the first of two central ideas", Cap))

A(p("Every run builds its environment by sourcing four layers in order. Later layers "
    "win:", Body))
A(code("""
   1.  config.env                     global paths and defaults
   2.  profilers/<profiler>/.env      that profiler's knobs
   3.  packages/<package>/.env        the package, ON TOP of the profiler
   4.  the real process environment   whatever the caller exported
"""))

A(spacer(2))
A(callout("Layer 3 above layer 2 is the whole point",
          "It is what lets a package tune a profiler <i>for itself</i>. "
          "<font face='Courier' size='8.4'>packages/my-tool/.env</font> can set "
          "<font face='Courier' size='8.4'>LINE_PROFILER_TARGETS</font>, "
          "<font face='Courier' size='8.4'>PYSPY_NATIVE=1</font> because it has C "
          "extensions, or a shallower "
          "<font face='Courier' size='8.4'>VIZTRACER_MAX_DEPTH</font> because its call "
          "graph is deep - all without touching the profiler. This is also why the "
          "package is loaded twice per pair, as Section 7 explains.", "ok"))

A(p("Why bash does the sourcing", H2))
A(p("The <font face='Courier' size='8.6'>.env</font> files are <b>sourced by bash, not "
    "parsed by Python</b>. The implementation runs one bash process and reads back its "
    "environment:", Body))
A(code("""
bash -c 'set -a; . "$1"; . "$2"; ...; env -0' _ <files...>
"""))
A(p("<font face='Courier' size='8.6'>set -a</font> auto-exports every assignment; "
    "<font face='Courier' size='8.6'>env -0</font> emits NUL-separated "
    "<font face='Courier' size='8.6'>NAME=VALUE</font> pairs so values containing "
    "newlines survive intact. The payoff is that variable interpolation, command "
    "substitution and <font face='Courier' size='8.6'>PATH=\"...:$PATH\"</font> all "
    "behave exactly as an author would expect, with no parser to write and no subset "
    "of shell syntax to document.", Body))
A(p("The cost is that a <font face='Courier' size='8.6'>.env</font> file is code. A "
    "stray <font face='Courier' size='8.6'>$</font> gets expanded. That is a real "
    "trade, and it is documented rather than papered over.", Body))

A(p("Naming the file that failed", H3))
A(p("A non-zero exit from the sourcing subshell is a hard error. Getting the filename "
    "into that message is harder than it looks, because a <i>syntax</i> error kills the "
    "shell outright - an <font face='Courier' size='8.6'>if ! source</font> guard never "
    "runs. An EXIT trap catches both cases:", Body))
A(code("""
trap '
  if [ "$?" -ne 0 ]; then
    printf "__PROFILING_ENV_FAIL__%s\\n" "$__profiling_current" >&2
  fi
' EXIT
"""))
A(p("Python looks for that marker on stderr and reports the offending path. In "
    "practice the user sees the filename twice - once from the harness and once from "
    "bash's own diagnostic with a line number - which is more useful than either "
    "alone.", Body))

A(p("The one exception to \"the real environment wins\"", H2))
A(p("Strict layer-4 precedence has a flaw. If a package writes "
    "<font face='Courier' size='8.6'>PYTHONPATH=\"$MY_SRC:$PYTHONPATH\"</font>, the "
    "overlay would immediately clobber it back to the inherited value, making the "
    "line a no-op. So PATH-like variables are treated as <i>extended</i> rather than "
    "assigned:", Body))
A(code("""
PATHLIKE = {PATH, PYTHONPATH, LD_LIBRARY_PATH, LD_PRELOAD,
            MANPATH, PKG_CONFIG_PATH, CPATH}

for key, value in real_env.items():
    if key in PATHLIKE and merged.get(key) != base.get(key):
        continue                  # a layer changed it deliberately - keep that
    merged[key] = value           # everything else: the real environment wins
"""))
A(p("Every other variable follows the documented rule exactly. This exception is "
    "called out in the README because it is the one place where the four-layer model "
    "is not literally true.", Body))

A(PageBreak())

# ============================================================ 6. TARGET
A(p("SECTION 6", Kick))
A(p("The target contract", H1))
A(p("runner.py - the second central idea, and the one that makes profilers pluggable", Cap))

A(p("Profilers come in two incompatible shapes, and getting this right is what the "
    "whole design turns on:", Body))
A(table(
    ["Family", "Example", "How it takes the workload"],
    [["Prefix wrapper", "<font face='Courier' size='8'>time</font>, "
      "<font face='Courier' size='8'>py-spy</font>",
      "Runs the complete command: <font face='Courier' size='8'>/usr/bin/time -v -- python -m tool</font>"],
     ["Interpreter replacement", "<font face='Courier' size='8'>viztracer</font>, "
      "<font face='Courier' size='8'>kernprof</font>",
      "<b>Is</b> the interpreter: <font face='Courier' size='8'>viztracer -m tool</font>, never "
      "<font face='Courier' size='8'>viztracer python -m tool</font>"]],
    [34 * mm, 30 * mm, 104 * mm]))

A(p("A single argv cannot serve both. So the runner exposes the workload in two "
    "forms, written to a generated file that the harness sources:", Body))
A(code("""
# <RUN_DIR>/target.sh  -- generated, one shlex.quote per element
TARGET_KIND=python-module
TARGET_PYTHON=/repo/.venv/bin/python
TARGET_ARGV=(/repo/.venv/bin/python -m my_tool.cli --input data/sample.json)
TARGET_PYTHON_ARGV=(-m my_tool.cli --input data/sample.json)
"""))

A(table(
    ["Variable", "Meaning"],
    [["TARGET_ARGV", "The complete plain command - exactly what you would run with no "
      "profiling. For prefix wrappers."],
     ["TARGET_PYTHON_ARGV", "The same minus the interpreter. This is Python's own "
      "trailing CLI shape, so it drops straight in after an interpreter-replacing "
      "tool's flags. <b>Unset when PACKAGE_KIND=exec.</b>"],
     ["TARGET_PYTHON", "The interpreter path. Unset for exec."],
     ["TARGET_KIND", "The package's PACKAGE_KIND."]],
    [36 * mm, 132 * mm], mono_cols=(0,)))

A(p("Why a sourced file rather than environment variables", H2))
A(p("Bash arrays cannot be exported. Passing an argv through the environment means "
    "encoding it - and every encoding scheme eventually meets an argument containing "
    "the delimiter. Writing a file with one "
    "<font face='Courier' size='8.6'>shlex.quote</font> per element and sourcing it "
    "sidesteps the problem completely: bash re-parses its own quoting, so the array "
    "on the other side is exactly the list Python built.", Body))
A(p("This was tested with arguments containing semicolons, "
    "<font face='Courier' size='8.6'>$(...)</font>, backticks, spaces and globs. They "
    "arrive as literal argv elements, and nothing is substituted at exec time.", Body))
A(p("A second benefit: <font face='Courier' size='8.6'>target.sh</font> is left in the "
    "run directory as an artifact. To reproduce a profiled run by hand, source it and "
    "run <font face='Courier' size='8.6'>\"${TARGET_ARGV[@]}\"</font>.", Body))

A(p("The exec kind, and failing readably", H2))
A(p("For <font face='Courier' size='8.6'>PACKAGE_KIND=exec</font> there is no "
    "interpreter to strip, so the generated file emits "
    "<font face='Courier' size='8.6'>unset TARGET_PYTHON_ARGV</font> rather than an "
    "empty array. An empty array would expand to nothing and the profiler would "
    "complain about its own arguments, which is a baffling error to debug. Instead "
    "<font face='Courier' size='8.6'>common.sh</font> provides a guard:", Body))
A(code("""
require_python_target() {
    if ! profiling_has_python_target; then
        profiling_die \\
            "profiler '$PROFILER_NAME' needs a Python target, but package" \\
            "'$PACKAGE_NAME' has PACKAGE_KIND=$TARGET_KIND. Use a prefix-style" \\
            "profiler (e.g. 'time'), or drop it from PACKAGE_PROFILERS."
    fi
}
"""))
A(p("Nothing shipped uses <font face='Courier' size='8.6'>exec</font> yet - only "
    "<font face='Courier' size='8.6'>time</font> declares support for it - but the "
    "path is plumbed and tested end to end, so adding "
    "<font face='Courier' size='8.6'>perf</font> or "
    "<font face='Courier' size='8.6'>callgrind</font> later needs no core change.", Body))

A(p("The escape hatch", H2))
A(p("When a command cannot be expressed as kind + entry + args, a package defines "
    "<font face='Courier' size='8.6'>package_command</font> in "
    "<font face='Courier' size='8.6'>package.sh</font> and populates the arrays "
    "itself. It overrides the declared entry point completely. The harness reads the "
    "final argv back from bash after the hook runs, so "
    "<font face='Courier' size='8.6'>meta.json</font> and "
    "<font face='Courier' size='8.6'>--dry-run</font> both reflect the override rather "
    "than the declaration.", Body))

A(PageBreak())

# ============================================================ 7. CMD_RUN
A(p("SECTION 7", Kick))
A(p("The main loop", H1))
A(p("cli.py:345 - cmd_run, the spine of the program", Cap))

A(p("Two nested loops, packages outer and profilers inner, with an eight-step "
    "checklist inside. This is the only function in the codebase worth reading "
    "carefully.", Body))

A(code("""
for package_name in package_names:
    base_package = load_package(CONFIG_ENV, entry)          # config + package
    selected, forced = _select_profilers_for_package(...)   # per-package list

    for profiler_name in selected:
        profiler = load_profiler(CONFIG_ENV, ...)           # config + profiler
        package  = load_package(CONFIG_ENV, entry,
                                profiler.entry.env_file)    # config+prof+package
        ...eight steps...
"""))

A(p("Why the package is loaded twice", H2))
A(p("This is the one genuinely non-obvious line in the file. The package is loaded "
    "once <i>alone</i> to read <font face='Courier' size='8.6'>PACKAGE_PROFILERS</font> "
    "- you cannot know which profilers apply until you have read the package - and "
    "then again <i>on top of each profiler's</i> "
    "<font face='Courier' size='8.6'>.env</font>. That second layering is what "
    "delivers the precedence rule from Section 5. The environment is genuinely "
    "different for every (package, profiler) pair, so it must be rebuilt for every "
    "pair.", Body))

A(p("The eight steps", H2))
A(table(
    ["#", "Step", "Line", "On failure"],
    [["1", "Kind compatible?", "417",
      "<b>Skip.</b> Informational line, recorded as skipped, never affects the exit code"],
     ["2", "Required binaries present?", "430",
      "Record failed, exit code 3, continue to the next pair"],
     ["3", "Init needed? (once per package)", "458",
      "Skip that package's runs, exit code 4"],
     ["4", "Build target + allocate run id", "472",
      "Usage error, exit code 2 - a malformed package is not a run failure"],
     ["5", "Dry run?", "479",
      "Print the resolved command and continue - nothing is created"],
     ["6", "Execute", "493",
      "Status recorded as failed or timeout, exit code 1"],
     ["7", "Write meta.json, update 'latest'", "503", "-"],
     ["8", "Aggregate the exit code", "507",
      "<font face='Courier' size='8'>max()</font> - the highest category wins"]],
    [7 * mm, 45 * mm, 11 * mm, 105 * mm]))

A(p("Three details carry requirements that look bigger than their code", H2))
A(code("""
exit_code = max(exit_code, EXIT_RUN_FAILED)     # "report the highest code"
init_done: Dict[str, bool] = {}                 # "init at most once per invocation"
if not profiler.supports(package.kind): continue  # "skips never fail the build"
"""))
A(p("A dict, a <font face='Courier' size='8.6'>max()</font> and a "
    "<font face='Courier' size='8.6'>continue</font>. No state machine, no scheduler. "
    "The run-everything-then-aggregate behaviour falls out of never breaking the "
    "loop.", Body))

A(p("How the loop body became its own function", H2))
A(p("The eight steps above used to live inline in "
    "<font face='Courier' size='8.6'>cmd_run</font>, making it 174 lines. The "
    "defence for that was \"the ordering of these steps is fragile and only "
    "visible when they are adjacent\" - which is true, but does not argue for "
    "keeping them <i>inside the loop</i>. Extracting them into "
    "<font face='Courier' size='8.6'>_run_pair</font> keeps all eight adjacent "
    "and leaves <font face='Courier' size='8.6'>cmd_run</font> as a readable "
    "71-line loop.", Body))
A(p("The real defect was not length. Six different branches each had to remember "
    "to do <i>two</i> things - append a record <b>and</b> raise the exit code - "
    "interleaved with the logic, so a branch could silently do one and forget the "
    "other. <font face='Courier' size='8.6'>_run_pair</font> returns "
    "<font face='Courier' size='8.6'>(record, exit-category)</font> from every "
    "path, so the two can no longer drift apart.", Body))

A(p("Profiler selection, resolved per package", H2))
A(p("The rules, in order, from <font face='Courier' size='8.6'>_select_profilers_for_package</font>:", Body))
A(table(
    ["Given", "Result"],
    [["<font face='Courier' size='8'>--profiler &lt;name&gt;</font>",
      "Exactly those, <b>forced</b>. A profiler not in the package's list still runs, "
      "warns on stderr, and is marked <font face='Courier' size='8'>\"forced\": true</font> "
      "in meta.json"],
     ["<font face='Courier' size='8'>--profiler all</font>", "The package's PACKAGE_PROFILERS"],
     ["...and that is empty", "DEFAULT_PROFILERS from config.env"],
     ["...and that is empty too", "Every discovered profiler"]],
    [40 * mm, 128 * mm]))
A(p("Because this resolves <i>per package</i>, "
    "<font face='Courier' size='8.6'>--package all --profiler all</font> is not a "
    "cartesian product. A package listing two profilers gets two runs while its "
    "neighbour listing three gets three.", Body))

A(PageBreak())

# ============================================================ 8. INIT
A(p("SECTION 8", Kick))
A(p("Package init", H1))
A(p("initstate.py - build once, and know when that stops being true", Cap))

A(p("Some packages need a one-time build before they can be profiled: a virtualenv, a "
    "compile step, <font face='Courier' size='8.6'>uv sync --frozen</font>. Running "
    "that before every profiler would multiply the cost by the number of profilers; "
    "caching it forever would silently profile stale code. The fingerprint is the "
    "compromise.", Body))

A(code("""
fingerprint = sha256( packages/<pkg>/.env
                    + packages/<pkg>/package.sh
                    + contents of each PACKAGE_INIT_FINGERPRINT file )
"""))

A(p("The decision table", H2))
A(table(
    ["Condition", "Action", "Why"],
    [["package.sh defines no package_init", "skip", "Nothing to do"],
     ["<font face='Courier' size='8'>--force-init</font>", "run", "Explicit override"],
     ["No stamp file", "run", "Fresh container"],
     ["Fingerprint changed", "run", "Lockfile or entry point moved"],
     ["Previous status was not ok", "run", "<b>A failed build is never cached as done</b>"],
     ["Otherwise", "skip", "Already built, inputs unchanged"]],
    [56 * mm, 16 * mm, 96 * mm]))

A(p("Three decisions worth explaining", H2))

A(p("A missing fingerprint file is a hard error", H3))
A(p("If <font face='Courier' size='8.6'>PACKAGE_INIT_FINGERPRINT</font> lists a path "
    "that does not exist, the harness stops with a message naming it. The tempting "
    "alternative - hash it as empty - means a typo produces a fingerprint that never "
    "changes again, so init silently never re-runs. The failure would surface weeks "
    "later as \"why is CI profiling last month's code\". Loud beats convenient.", Body))

A(p("The log goes to .state/, never the run directory", H3))
A(p("Covered in Section 2: if the build log lived under "
    "<font face='Courier' size='8.6'>output/</font>, pruning would cost a rebuild.", Body))

A(p("A failed init writes a stamp too", H3))
A(p("It records <font face='Courier' size='8.6'>\"status\": \"failed\"</font>. The next "
    "run sees a non-ok status and retries. Writing nothing would also work, but "
    "writing the failure keeps the log and the exit code together for whoever is "
    "debugging it.", Body))

A(p("Verified behaviour", H2))
A(p("Measured, not assumed - deleting <font face='Courier' size='8.6'>.state/</font> "
    "and then touching a fingerprinted source file produces exactly this sequence of "
    "init counts across five consecutive runs:", Body))
A(code("""
fresh .state/          -> 1 init
run again              -> 0
touch a source file    -> 1
run again              -> 0
--force-init           -> 1
"""))

A(PageBreak())

# ============================================================ 9. HARNESS
A(p("SECTION 9", Kick))
A(p("The bash harness", H1))
A(p("Where Python stops and the workload begins", Cap))

A(p("<font face='Courier' size='8.6'>runner.execute</font> writes "
    "<font face='Courier' size='8.6'>target.sh</font>, then spawns <b>one</b> bash "
    "process running a fixed script. One process, not several, because the hooks share "
    "shell state and because a single process is a single process group to kill.", Body))

A(code("""
. "$PROFILING_ROOT/lib/common.sh"      # helpers: run_artifact, require_bin, ...
. "$RUN_DIR/target.sh"                 # TARGET_ARGV, TARGET_PYTHON_ARGV, ...
. "$PROFILING_PACKAGE_SH"              # package hooks   (if the file exists)
. "$PROFILING_PROFILER_SH"             # profiler hooks  (if the file exists)

declare -F package_command  && package_command      # escape hatch may override
printf '%s\\0' "${TARGET_ARGV[@]}" > "$PROFILING_ARGV_FILE"   # report the truth

declare -F profiler_command || { profiling_error "no profiler_command"; exit 78; }

cmd=(); profiler_command                # build it
printf '%s\\0' "${cmd[@]}" > "$COMMAND_FILE"
[ "$PROFILING_RESOLVE_ONLY" = 1 ] && exit 0   # <-- --dry-run stops here

__profiling_post_run() { declare -F package_post_run && package_post_run; }
trap __profiling_post_run EXIT          # <-- fires even on failure or kill

cd "$PACKAGE_WORKDIR" || exit 77

declare -F package_pre_run && package_pre_run || exit $?

"${cmd[@]}"                             # <-- THE WORKLOAD RUNS HERE
__profiling_status=$?

[ $__profiling_status -eq 0 ] && declare -F profiler_post && profiler_post

exit "$__profiling_status"              # the workload's status, unmodified
"""))

A(p("Four properties this arrangement buys", H2))
A(table(
    ["Property", "How"],
    [["<font face='Courier' size='8'>package_post_run</font> runs even when the workload dies",
      "It is an EXIT trap, not a line after the call. Verified for a failing workload, "
      "a failing pre_run, and a timeout kill"],
     ["The workload's exit status survives untouched",
      "the resolved command's status is captured "
      "immediately and is what the script exits with. A pre_run that returns 42 "
      "produces <font face='Courier' size='8'>exit_code: 42</font> in meta.json"],
     ["<font face='Courier' size='8'>profiler_post</font> only runs on success",
      "Guarded on the captured status. Post-processing a profile that was never "
      "written produces confusing errors"],
     ["Every hook is optional",
      "<font face='Courier' size='8'>declare -F</font> before each call, so "
      "<font face='Courier' size='8'>package.sh</font> may be an empty file"]],
    [52 * mm, 116 * mm]))

A(p("Reading the argv back", H3))
A(p("The harness writes the final <font face='Courier' size='8.6'>TARGET_ARGV</font> to "
    "a NUL-separated file that Python reads afterwards. This exists so that "
    "<font face='Courier' size='8.6'>meta.json</font> records the command that "
    "<i>actually ran</i> - including any "
    "<font face='Courier' size='8.6'>package_command</font> rewrite - rather than the "
    "one Python guessed before the hooks had their say. The file is deleted once read, "
    "and dotfiles at the top of a run directory are excluded from the artifact list "
    "regardless.", Body))

A(p("--dry-run and the honest limit on exactness", H2))
A(p("\"Print the exact command each profiler would execute\" is not achievable for an "
    "arbitrary bash function without that function's cooperation - the command only "
    "exists once the hook has expanded its variables. Rather than approximate it, the "
    "contract adds an optional hook:", Body))
A(code("""
profiler_command() { cmd=(py-spy record --rate "$PYSPY_RATE" ...); }

# --dry-run prints "${cmd[@]}".  The real run executes "${cmd[@]}".
# One builder, one harness, resolved once -- they cannot drift.
"""))
A(p("Both paths share one argv builder, so the printed command provably cannot drift "
    "from the executed one. All four shipped profilers implement it. A third-party "
    "profiler that does not gets a clearly-labelled fallback naming the wrapper, "
    "rather than a plausible-looking guess.", Body))
A(p("<font face='Courier' size='8.6'>--dry-run</font> creates no directories and starts "
    "no workload. It writes <font face='Courier' size='8.6'>target.sh</font> to a "
    "temporary directory, prints the run directory it <i>would</i> have used, and "
    "exits 0 even when the run would have failed.", Body))

A(PageBreak())

# ============================================================ 10. PROCESS
A(p("SECTION 10", Kick))
A(p("Process control", H1))
A(p("Tee, timeout, and not leaking processes", Cap))

A(p("Tee-ing output", H2))
A(p("Two daemon threads copy the child's stdout and stderr to the console and to "
    "<font face='Courier' size='8.6'>stdout.log</font> / "
    "<font face='Courier' size='8.6'>stderr.log</font> simultaneously, line by line "
    "with an explicit flush. You watch the workload live <i>and</i> get the capture. "
    "Verified at 50,000 lines with a matching checksum against a direct run, and with "
    "multi-byte UTF-8 output.", Body))

A(p("Timeout", H2))
A(p("The child is spawned with "
    "<font face='Courier' size='8.6'>start_new_session=True</font>, giving it its own "
    "process group so that a timeout can take the profiler <i>and</i> everything it "
    "spawned down together. On expiry the group is signalled and the run is recorded "
    "as <font face='Courier' size='8.6'>\"status\": \"timeout\"</font>.", Body))

A(spacer(2))
A(callout("That same isolation caused a real bug",
          "Because the child is in its own session, a signal sent to the harness does "
          "<b>not</b> reach it. SIGINT, SIGTERM and SIGHUP each left py-spy and the "
          "workload running in the container - a cancelled CI job would leak a profiler "
          "burning CPU. All three are now handled explicitly and kill the process group "
          "on the way out."))

A(p("And underneath it, a second one", H2))
A(p("The first fix still leaked. Escalation to SIGKILL was conditioned on the "
    "<i>direct child</i> still being alive - but GNU <font face='Courier' size='8.6'>time</font> "
    "sets SIGTERM to <font face='Courier' size='8.6'>SIG_IGN</font>, and an ignored "
    "disposition survives <font face='Courier' size='8.6'>exec</font>. So "
    "<font face='Courier' size='8.6'>time -- sleep 600</font> left a "
    "<font face='Courier' size='8.6'>sleep</font> that shrugged off the very signal "
    "that killed its parent bash, and SIGKILL never came.", Body))
A(p("The fix keys escalation on whether the <i>process group</i> is empty, probed with "
    "signal 0:", Body))
A(code("""
_signal_group(pgid, SIGTERM)

proc.wait(timeout=term_grace)   # reap the child FIRST - see below

if _group_alive(pgid):          # anything left ignored SIGTERM
    _signal_group(pgid, SIGKILL)
    while time.monotonic() < deadline and _group_alive(pgid):
        time.sleep(0.05)
"""))
A(p("The <font face='Courier' size='8.6'>proc.wait()</font> before the probe is not "
    "decoration. An un-reaped zombie is <i>still a member of its process group</i>, so "
    "probing first always reported the group alive and stalled every kill for the full "
    "grace period - a 3-second timeout was taking 10 seconds of wall clock. Reaping "
    "first brings it to about 3.", Body))
A(p("The roughly 2 seconds that remain are a deliberate courtesy window: a workload "
    "that handles SIGTERM gets a chance to flush partial results before SIGKILL "
    "lands.", Body))
A(p("Verified by process-group id across SIGINT, SIGTERM, SIGHUP and the timeout path, "
    "for both a plain exec workload and py-spy's two-process attach mode. Zero "
    "survivors in every case.", Body))

A(PageBreak())

# ============================================================ 11. OUTPUT
A(p("SECTION 11", Kick))
A(p("Output artifacts", H1))
A(p("report.py - what a run leaves behind", Cap))

A(code("""
output/
  <package>/<profiler>/<run-id>/
      meta.json      what happened
      target.sh      the exact command, re-sourceable by hand
      stdout.log     tee'd
      stderr.log     tee'd
      <artifacts>    profile.svg, trace.json, out.lprof, time.txt, ...
  <package>/<profiler>/latest -> <run-id>       relative symlink
  summary.json                                  last invocation, overwritten
"""))

A(p("<font face='Courier' size='8.6'>run-id</font> is "
    "<font face='Courier' size='8.6'>YYYYmmdd-HHMMSS-&lt;4 hex&gt;</font>. The "
    "<font face='Courier' size='8.6'>RUN_ID</font> environment variable overrides it "
    "wholesale so CI can use a build number; injected values are sanitised down to "
    "<font face='Courier' size='8.6'>[A-Za-z0-9._-]</font>, so a value like "
    "<font face='Courier' size='8.6'>../../etc/passwd</font> cannot escape the output "
    "tree.", Body))

A(p("meta.json", H2))
A(code("""
{
  "package": "my-tool", "profiler": "py-spy", "run_id": "20260729-141230-a3f1",
  "status": "ok",              # ok | failed | timeout | skipped
  "exit_code": 0, "duration_s": 12.4,
  "started_at": "...", "finished_at": "...",
  "argv": ["..."],             # what actually ran, post package_command
  "kind": "python-module", "workdir": "...",
  "forced": false,             # true if --profiler overrode PACKAGE_PROFILERS
  "flamegraph": "profile.svg", # null unless declared AND the file exists
  "artifacts": ["profile.svg", "stdout.log", "stderr.log"],
  "host": "...", "git_sha": "..."
}
"""))
A(p("<font face='Courier' size='8.6'>git_sha</font> is best-effort: if git is missing "
    "or this is not a repository, the field is <i>omitted</i> rather than the run "
    "failing. <font face='Courier' size='8.6'>flamegraph</font> is null unless the "
    "profiler declares <font face='Courier' size='8.6'>PROFILER_FLAMEGRAPH=1</font> "
    "<i>and</i> the file is actually on disk - declaring one is not the same as "
    "producing one.", Body))

A(p("summary.json", H3))
A(p("One file per invocation holding the arguments, per-status counts and the array of "
    "run records - so a downstream regression gate reads one file instead of globbing "
    "a tree.", Body))

A(p("A bug worth recording here", H2))
A(p("Re-running a <i>pinned</i> <font face='Courier' size='8.6'>RUN_ID</font> used to "
    "mix artifacts across attempts. Re-running "
    "<font face='Courier' size='8.6'>build-7</font> with flamegraphs disabled produced "
    "a fresh <font face='Courier' size='8.6'>profile.json</font> beside the previous "
    "attempt's <font face='Courier' size='8.6'>profile.svg</font>, and "
    "<font face='Courier' size='8.6'>meta.json</font> claimed both. The run directory "
    "is now cleared first, guarded by a containment check against the output root, and "
    "the clearing is announced rather than silent.", Body))

A(PageBreak())

# ============================================================ 12. RETENTION
A(p("SECTION 12", Kick))
A(p("Retention", H1))
A(p("retention.py - deleting things carefully", Cap))

A(p("<font face='Courier' size='8.6'>--remove-output</font> is a terminal action: "
    "prune, print what went, exit. <font face='Courier' size='8.6'>--keep N</font> "
    "keeps N runs <b>per (package, profiler) pair</b>, not N in total - so "
    "<font face='Courier' size='8.6'>--keep 3</font> on a package with four profilers "
    "leaves twelve directories. That is what you want when comparing a profiler's "
    "history against itself.", Body))

A(p("This module deletes files, so it is the most defensive code in the tree:", Body))
A(table(
    ["Guard", "Behaviour"],
    [["Output directory sanity", "Refuses if PROFILING_OUTPUT_DIR is unset, empty, "
      "relative, the filesystem root, or suspiciously shallow"],
     ["Containment re-check", "Every candidate is re-verified to resolve inside the "
      "output root immediately before deletion"],
     ["Unknown directory names", "Directories whose names do not match the run-id "
      "pattern are <b>left untouched</b>, never guessed at"],
     ["Dry run", "<font face='Courier' size='8'>--dry-run</font> lists exactly what "
      "would go and deletes nothing"],
     ["Symlink repair", "<font face='Courier' size='8'>latest</font> is re-pointed at "
      "the newest survivor, or removed when none remains"]],
    [40 * mm, 128 * mm]))

A(p("The consequence of the third guard is worth stating plainly: a run created with "
    "a custom <font face='Courier' size='8.6'>RUN_ID</font> opts <i>out</i> of "
    "automatic pruning, because the harness cannot tell it apart from a directory "
    "someone put there on purpose. Those must be deleted by hand.", Body))

A(spacer(2))
A(callout("The bug this module actually had",
          "Survivors were computed as "
          "<font face='Courier' size='8.4'>runs[len(runs) - keep:]</font>. When "
          "<font face='Courier' size='8.4'>keep</font> exceeds the run count that index "
          "goes negative: <font face='Courier' size='8.4'>--keep 5</font> against 3 runs "
          "sliced <font face='Courier' size='8.4'>runs[-2:]</font> and <b>deleted the "
          "oldest run it had been told to preserve</b>. It only misfires while "
          "count &lt; keep &lt; 2x count, so it would have looked like random data loss. "
          "The fix is <font face='Courier' size='8.4'>runs[-keep:]</font>, which clamps."))

A(PageBreak())

# ============================================================ 13. EXIT
A(p("SECTION 13", Kick))
A(p("Exit codes", H1))
A(p("One number that tells CI what happened", Cap))

A(table(
    ["Code", "Meaning"],
    [["0", "All runs succeeded. <b>Skips do not affect this.</b>"],
     ["1", "At least one workload failed or timed out"],
     ["2", "Usage error: unknown package or profiler, missing --profiler, bad flag, "
      "malformed .env"],
     ["3", "A required profiler binary is missing"],
     ["4", "A package init failed"]],
    [16 * mm, 152 * mm], mono_cols=(0,)))

A(p("The harness runs everything and then aggregates - it never stops at the first "
    "failure - and reports the <b>highest</b> applicable code. If one package's init "
    "fails (4) while another package's workload fails (1), the process exits 4.", Body))

A(p("Why skips are not failures", H2))
A(p("A skip means a profiler cannot handle a package's kind - asking py-spy to sample "
    "a non-Python executable, say. That is a statement about compatibility, not about "
    "the code under test. Making it a failure would mean "
    "<font face='Courier' size='8.6'>--package all --profiler all</font> could never "
    "go green, which would make the most useful invocation the least usable one. Skips "
    "are recorded in <font face='Courier' size='8.6'>summary.json</font> with a reason, "
    "so nothing is hidden.", Body))

A(p("Why a malformed package is exit 2 and not exit 1", H3))
A(p("Exit 1 means \"the code you are profiling has a problem\". A package "
    "<font face='Courier' size='8.6'>.env</font> with a bad "
    "<font face='Courier' size='8.6'>PACKAGE_KIND</font> is a problem with the "
    "<i>harness configuration</i>, and a CI gate should treat those differently: one "
    "is a regression, the other is a broken pipeline.", Body))

A(PageBreak())

# ============================================================ 14. PROFILERS
A(p("SECTION 14", Kick))
A(p("The four profilers", H1))
A(p("And the one that needed real work", Cap))

A(table(
    ["Profiler", "Kinds", "Flamegraph", "Notes"],
    [["time", "all three, incl. exec", "no",
      "GNU time -v: wall clock, CPU, peak RSS. Cheap enough to always run"],
     ["py-spy", "python", "<b>yes</b>", "Sampling. Needs ptrace. See below"],
     ["viztracer", "python", "no",
      "Deterministic timeline. Large artifacts, heavy overhead"],
     ["line-profiler", "python", "no", "Per-line timings via kernprof. Needs configuring"]],
    [26 * mm, 30 * mm, 20 * mm, 92 * mm], mono_cols=(0,)))

A(p("Flag spellings were verified against the installed tools - py-spy 0.4.2, "
    "viztracer 1.1.1, line_profiler 5.0.2 - rather than taken from documentation.", Body))

A(p("py-spy: the exit status problem", H2))
A(p("py-spy's exit status describes <b>py-spy</b>, not the program it ran, and the two "
    "are uncorrelated. Running one command ten times each:", Body))
A(code("""
child exits 0  ->  py-spy exits  0 1 1 1 1 0 1 1 1 1
child exits 3  ->  py-spy exits  1 0 1 1 1 1 1 0 0 1
"""))
A(p("The 1s on a <i>succeeding</i> child come from the flamegraph renderer reporting "
    "\"No stack counts found\" - a function of how long the workload ran, not of "
    "whether it worked. Since "
    "the run's status <i>is</i> the "
    "workload's status, trusting py-spy's number would report broken workloads as "
    "successful and healthy ones as broken, at random.", Body))
A(p("So the profiler starts the workload itself and attaches py-spy to the resulting "
    "pid. The shell then owns the process and "
    "<font face='Courier' size='8.6'>wait</font> yields its exact code. The cost is "
    "that sampling begins a few milliseconds late.", Body))
A(p("The obvious alternative - keep launch mode and wrap the workload in a shell that "
    "records <font face='Courier' size='8.6'>$?</font> - was measured and rejected: the "
    "extra fork makes py-spy miss the Python process entirely on <b>4 of 8</b> "
    "sub-second runs. Losing half the profiles is a far worse trade than a few "
    "milliseconds.", Body))

A(p("Interpreting a non-zero py-spy status", H3))
A(p("A non-zero status is still used, but only to tell its failure modes apart, and "
    "never by trusting the number:", Body))
A(table(
    ["What happened", "How it is detected", "Outcome"],
    [["Attached, collected no samples", "It still wrote an output file",
      "Warning; the workload's status stands"],
     ["Could not attach, workload still running",
      "No output file, and the workload was alive when py-spy quit",
      "<b>Run fails</b> - ptrace denied or similar"],
     ["Could not attach, workload already gone",
      "No output file, workload already finished",
      "Warning; the workload's status stands"]],
    [46 * mm, 62 * mm, 60 * mm]))
A(p("That last row is the attach-window race, and it is deliberately not a failure. A "
    "workload finishing in a few milliseconds occasionally beats the attach; failing "
    "on it would make fast packages flaky in CI. Distinguishing the last two rows is "
    "only possible while it happens, which is why the profiler waits on py-spy first "
    "and then checks the workload's state in "
    "<font face='Courier' size='8.6'>/proc</font> - a finished-but-unreaped child is "
    "still a signalable pid, so <font face='Courier' size='8.6'>kill -0</font> cannot "
    "answer the question.", Body))

A(p("viztracer: artifact size", H2))
A(p("Stock defaults produced <b>106 MB</b> of JSON and 17 seconds of overhead from a "
    "0.2-second workload. Capping the circular event buffer brings that to 22 MB and "
    "2.6 seconds. The buffer is circular, so overflowing it costs the earliest events, "
    "not the run.", Body))

A(p("line-profiler: the one that is not zero-config", H2))
A(p("Without <font face='Courier' size='8.6'>LINE_PROFILER_TARGETS</font> (or "
    "<font face='Courier' size='8.6'>@profile</font> decorators) it produces an empty "
    "report. The harness warns - never fails - when a package selects it without "
    "configuring it. The warning is driven by a "
    "<font face='Courier' size='8.6'>PROFILER_WARN_IF_UNSET</font> declaration in the "
    "profiler's own <font face='Courier' size='8.6'>.env</font>, so the core knows "
    "nothing about line-profiler by name and the next tool with the same problem needs "
    "no core change.", Body))

A(PageBreak())

# ============================================================ 15. SIZE
A(p("SECTION 15", Kick))
A(p("Is this too much code?", H1))
A(p("An honest accounting", Cap))

A(p("2336 lines total, 1839 excluding comments and blanks. That is a fair thing to "
    "challenge. Here is where it actually goes.", Body))

A(table(
    ["Module", "Lines", "What it owns"],
    [["cli.py", "720", "Parser (70), three terminal commands (116), _run_pair (113), "
      "cmd_run (71), dry-run printing, helpers, signals"],
     ["runner.py", "583", "One bash harness as a string constant (~55), execute (96), "
      "target building (70), dry-run resolution (57), process-group control (60)"],
     ["discovery.py", "236", "Scanning (40), typed accessors (110), selection (35)"],
     ["report.py", "228", "meta.json (60), listings (40), table renderer (25), summary (40)"],
     ["initstate.py", "207", "Fingerprint (40), decision table (30), execution (60)"],
     ["retention.py", "175", "Safety checks (30), planning (50), applying (40)"],
     ["envfile.py", "200", "load_layers entry point, sourcing (50), parsing (40), precedence (25)"]],
    [26 * mm, 14 * mm, 128 * mm], mono_cols=(0, 1)))

A(p("Roughly a third is features, not core", H2))
A(p("Retention (135), init fingerprinting (163), dry-run resolution (~120), JSON "
    "listings and table rendering (~90), summary and meta writing (~90). That is about "
    "600 lines - a third of the total - and none of it is optional if the tool is to "
    "have those features. The actual \"run a thing under a profiler\" path is closer to "
    "400 lines: discover, layer the environment, build the argv, spawn bash, record the "
    "result.", Body))

A(p("About 70 lines are bug fixes", H2))
A(p("Signal handling, the SIGTERM-to-SIGKILL group escalation, and the run-directory "
    "reset. Not elegant, but each closes a leak that was demonstrated rather than "
    "imagined.", Body))

A(p("What was redundant, and is now gone", H2))
A(p("Two things this document previously listed as slop have been removed. "
    "<font face='Courier' size='8.6'>discovery._load_env</font> and "
    "<font face='Courier' size='8.6'>cli.load_global_env</font> were the same "
    "function differing only in which exception they raised - and both exceptions "
    "were caught on one line and produced the same exit code. They are now one "
    "<font face='Courier' size='8.6'>envfile.load_layers</font>.", Body))
A(p("And <font face='Courier' size='8.6'>_print_dry_run</font> used to call two "
    "resolvers backed by two nearly identical bash scripts, spawning two "
    "subprocesses for one printed line. They are now one "
    "<font face='Courier' size='8.6'>resolve_dry_run</font> and one subprocess.", Body))
A(p("The bigger change is in <font face='Courier' size='8.6'>cmd_run</font>: its "
    "inner loop body - the eight steps for one (package, profiler) pair - is now "
    "<font face='Courier' size='8.6'>_run_pair</font>, and "
    "<font face='Courier' size='8.6'>cmd_run</font> fell from 174 lines to 71. See "
    "Section 7.", Body))

A(p("Where the length is defensible", H2))
A(p("<font face='Courier' size='8.6'>cmd_run</font>'s 174 lines look like the problem "
    "and are not, for the reason given in Section 7: the ordering of its steps is the "
    "fragile part, and ordering is only visible when the steps are adjacent.", Body))
A(p("<font face='Courier' size='8.6'>envfile.py</font>'s 132 lines look like a lot for "
    "\"source a file\", but the precedence rule and the error attribution are the "
    "subtle parts, and both were arrived at by fixing observed problems.", Body))

A(PageBreak())

# ============================================================ 16. TESTING
A(p("SECTION 16", Kick))
A(p("Testing", H1))
A(p("111 checks across five suites", Cap))

A(table(
    ["Suite", "Checks", "Covers"],
    [["acceptance", "22", "The specification's own acceptance list"],
     ["bughunt", "21", "Quoting torture, hook ordering and failure, package_command, "
      "PACKAGE_TIMEOUT=0, malformed .env, unknown profiler, a real directory named 'latest'"],
     ["bughunt2", "16", "All four environment layers individually, exit-code aggregation, "
      "50k-line tee integrity by checksum, unicode, signal cleanup by process group"],
     ["compliance", "33", "File tree, stdlib-only imports, every contract variable reaching "
      "hooks, cwd, hook ordering, init-once, meta.json fields, run-id format, run ordering"],
     ["compliance2", "19", "Every config.env override, retention safety refusals, "
      "terminal-action behaviour, both py-spy failure classifications"]],
    [26 * mm, 15 * mm, 127 * mm], mono_cols=(0, 1)))

A(p("Run twice end to end with no flakes. Two habits proved worth keeping:", Body))

A(p("Verify the test before believing the failure", H3))
A(p("Three early \"failures\" were the tests being wrong, not the code - a "
    "<font face='Courier' size='8.6'>grep -c</font> counting two lines instead of one, "
    "and a glob traversing the <font face='Courier' size='8.6'>latest</font> symlink so "
    "two runs looked like four. Each was checked before anything was changed.", Body))

A(p("Beware of measurement artifacts", H3))
A(p("<font face='Courier' size='8.6'>pgrep</font> and "
    "<font face='Courier' size='8.6'>ps | grep</font> match the measuring shell's own "
    "command line. This produced a phantom \"py-spy leaks 2 processes\" reading. The "
    "trustworthy numbers came from probing by process-group id instead.", Body))

A(PageBreak())

# ============================================================ 17. DEVIATIONS
A(p("SECTION 17", Kick))
A(p("Deviations and judgement calls", H1))
A(p("Where the implementation departs from the specification, and why", Cap))

A(p("1. py-spy uses attach mode, not the spec's literal command", H3))
A(p("The specification shows "
    "<font face='Courier' size='8.6'>py-spy record -- \"${TARGET_ARGV[@]}\"</font>. "
    "That form makes the workload's exit status unobservable, as Section 14 shows with "
    "measurements. The specification's own instruction to verify flag spellings against "
    "installed versions and adjust reads as licence for this, but it is a departure "
    "from the literal text and is documented in both the profiler and the README.", Body))

A(p("2. PATH-like variables are the one exception to layer-4 precedence", H3))
A(p("Strict precedence would make "
    "<font face='Courier' size='8.6'>PYTHONPATH=\"$MY_SRC:$PYTHONPATH\"</font> in a "
    "package a no-op, contradicting the specification's own example. Section 5 gives "
    "the rule; it is called out in the README.", Body))

A(p("3. --dry-run exactness needs the profiler's cooperation", H3))
A(p("Achieved through an optional "
    "<font face='Courier' size='8.6'>profiler_dry_run</font> hook rather than by "
    "guessing. All shipped profilers implement it; third-party ones degrade to a "
    "labelled fallback instead of printing something that might be wrong.", Body))

A(p("4. viztracer gains a tracer_entries cap", H3))
A(p("Beyond the specification's <font face='Courier' size='8.6'>VIZTRACER_MAX_DEPTH</font>, "
    "because depth alone left 106 MB artifacts. The specification asks for conservative "
    "defaults and permits adjusting knobs, so this is within its intent.", Body))

A(p("Open item", H2))
A(p("The redundant dry-run harness described in Section 15 is known and unfixed. It is "
    "a contained change - delete "
    "<font face='Courier' size='8.6'>_RESOLVE_HARNESS</font> and "
    "<font face='Courier' size='8.6'>resolve_argv</font>, have "
    "<font face='Courier' size='8.6'>dry_run_command</font> return both values in one "
    "pass - and would want the 111 checks re-run afterwards.", Body))

A(spacer(6))
A(callout("The through-line",
          "Two ideas carry this design. <b>Environment layering</b> means a package can "
          "tune a profiler for itself without either knowing about the other. <b>The "
          "target contract</b> means a profiler can be a prefix wrapper or an "
          "interpreter replacement without the core knowing which. Everything else - "
          "discovery, retention, init caching, exit codes - is bookkeeping around those "
          "two. If you understand Sections 5 and 6, the rest reads itself.", "ok"))

doc.build(F)
print("wrote", OUT)
