"""Convert the straight-line LaTeX chapter into MyST markdown for Jupyter Book.

The chapter is a standalone LaTeX ``article`` document that uses a set of custom
macros (``hogg_style.tex``) and packages pandoc cannot follow (endnotes and
deluxetable). This script sanitises the source, expands the shared macros, renders
referenced PDF figures to PNG, runs pandoc, and writes a MyST markdown page plus a
generated ``_toc.yml``. Worked-exercise Python sources are included next to their
figures in collapsible code blocks.

Run with:  uv run dar-convert    (or)    uv run python -m scripts.convert
"""
from __future__ import annotations

import io
import re
import shutil
import sys
import tokenize
from pathlib import Path

import pypandoc

REPO = Path(__file__).resolve().parent.parent
BOOK = REPO / "book"
CHAPTERS_DIR = BOOK / "chapters"

# ---------------------------------------------------------------------------
# The only LaTeX source published by the Jupyter Book.
# ---------------------------------------------------------------------------
CHAPTERS: list[tuple[str, str, str]] = [
    ("straightline/straightline.tex", "straightline", "Fitting a model to data"),
]

# Figure stems in straightline.tex identify the source for each worked solution.
# Multiple figures can share a module; each module is rendered only at its first
# relevant figure.
FIGURE_CODE: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"ex[12]$"), "ex1.py"),
    (re.compile(r"ex3$"), "ex3.py"),
    (re.compile(r"exMix[12][abc]$"), "exMix1.py"),
    (re.compile(r"ex9[ab]?$"), "ex9.py"),
    (re.compile(r"ex10[ab]$"), "ex10.py"),
    (re.compile(r"ex12$"), "ex12.py"),
    (re.compile(r"ex13[ab]$"), "ex13.py"),
    (re.compile(r"ex14$"), "ex14.py"),
    (re.compile(r"ex15$"), "ex15.py"),
    (re.compile(r"ex16$"), "ex16.py"),
    (re.compile(r"ex17$"), "ex17.py"),
)

# ---------------------------------------------------------------------------
# Compatibility preamble: safe defaults for macros that pandoc cannot resolve
# from the (stripped) custom packages. Real \newcommand defs from the chapter
# body still override these where present; our forced \note redefinition is
# re-applied after macro extraction so endnotes become footnotes.
# ---------------------------------------------------------------------------
COMPAT_PREAMBLE = r"""
\newcommand{\sectionname}{Section}
\newcommand{\documentname}{Chapter}
\newcommand{\documentnames}{Chapters}
\newcommand{\equationname}{equation}
\newcommand{\figurename}{Figure}
\newcommand{\tablename}{Table}
\newcommand{\problemname}{Problem}
\newcommand{\notename}{note}
\newcommand{\foreign}[1]{\emph{#1}}
\newcommand{\notenglish}[1]{\emph{#1}}
\newcommand{\project}[1]{\emph{#1}}
\newcommand{\code}[1]{\texttt{#1}}
\newcommand{\acronym}[1]{#1}
\newcommand{\affil}[1]{#1}
\newcommand{\etal}{et al.}
\newcommand{\eg}{e.g.}
\newcommand{\vs}{vs.}
\newcommand{\aposteriori}{a~posteriori}
\newcommand{\apriori}{a~priori}
\newcommand{\adhoc}{ad~hoc}
\newcommand{\arxiv}[1]{arXiv:#1}
\newcommand{\doi}[1]{doi:#1}
\newcommand{\isbn}[1]{ISBN:#1}
\newcommand{\chaptertitle}{}
\newcommand{\githash}{}
\newcommand{\gitdate}{}
\newcommand{\note}[1]{\footnote{#1}}
"""

# Single-argument commands whose whole call (incl. argument) should be removed.
DROP_WITH_ARG = ["footnotetext", "markright", "thispagestyle", "pagestyle",
                 "pagenumbering", "bibliographystyle", "setcitestyle"]
# Two-argument commands to drop entirely.
DROP_WITH_TWO_ARGS = ["markboth", "setlength"]
# Bare commands (no args) to drop.
DROP_BARE = ["footnotemark", "raggedbottom", "raggedright", "clearpage", "newpage",
             "frenchspacing", "theendnotes", "numberparagraphs", "maketitle",
             "tableofcontents", "noindent", "bigskip", "medskip", "smallskip"]


def _find_matching_brace(s: str, open_idx: int) -> int:
    """Return index of the brace matching the ``{`` at ``open_idx`` (or -1)."""
    depth = 0
    for i in range(open_idx, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def strip_command(text: str, name: str, nargs: int = 1) -> str:
    """Remove ``\\name{..}`` (and following ``{..}`` groups for nargs>1)."""
    pattern = re.compile(r"\\" + name + r"\s*\*?\s*(?=\{)")
    out, idx = [], 0
    while True:
        m = pattern.search(text, idx)
        if not m:
            out.append(text[idx:])
            break
        out.append(text[idx:m.start()])
        pos = m.end()
        for _ in range(nargs):
            if pos < len(text) and text[pos] == "{":
                close = _find_matching_brace(text, pos)
                if close == -1:
                    break
                pos = close + 1
            else:
                break
        idx = pos
    return "".join(out)


def strip_environment(text: str, env: str, preserve_labels: bool = False) -> str:
    """Remove an environment, optionally retaining its cross-reference labels."""
    pattern = re.compile(
        r"\\begin\{" + env + r"\*?\}.*?\\end\{" + env + r"\*?\}", re.DOTALL)

    def replacement(match: re.Match[str]) -> str:
        labels = ""
        if preserve_labels:
            labels = "\n".join(re.findall(r"\\label\{[^}]+\}", match.group(0)))
            if labels:
                labels += "\n"
        return f"\n{labels}*(table omitted in web edition)*\n"

    return pattern.sub(replacement, text)


def expand_multicolumn(text: str) -> str:
    """Replace table-only ``\\multicolumn`` with MathJax-compatible cells.

    The chapter uses this command only for ellipsis rows in arrays. MathJax
    does not implement LaTeX's table spanning primitive, so repeat the cell
    contents to retain the declared array width and the intended visual cue.
    """
    pattern = re.compile(
        r"\\multicolumn\s*\{(\d+)\}\s*\{[^{}]*\}\s*\{([^{}]*)\}"
    )

    def replacement(match: re.Match[str]) -> str:
        count = int(match.group(1))
        contents = match.group(2)
        return " & ".join(contents for _ in range(count))

    return pattern.sub(replacement, text)


def extract_text_macros(style_path: Path) -> str:
    """Inline the macro definitions from hogg_style, preserving multi-line bodies.

    Removes only what pandoc cannot parse — the ``\\makeatletter`` block (internal
    ``@`` commands and the figure-caption redefinition) and layout/package lines —
    while keeping ``\\newcommand``/``\\newenvironment`` definitions intact, including
    multi-line ones such as ``\\exampleplot`` that wrap ``\\includegraphics``.
    """
    if not style_path.exists():
        return ""
    raw = style_path.read_text(encoding="utf-8", errors="replace")
    raw = re.sub(r"\\makeatletter.*?\\makeatother", "", raw, flags=re.DOTALL)
    drop_prefixes = ("\\usepackage", "\\RequirePackage", "\\IfFileExists",
                     "\\setlength", "\\linespread", "\\frenchspacing",
                     "\\pagestyle", "\\documentclass", "\\input")
    kept = [ln for ln in raw.splitlines()
            if not ln.lstrip().startswith(drop_prefixes)]
    return "\n".join(kept)


def sanitize(tex: str, source: Path) -> str:
    """Turn a raw chapter .tex into pandoc-friendly LaTeX."""
    # Drop comments (keep escaped \%).
    tex = re.sub(r"(?<!\\)%.*", "", tex)

    # Inline the shared style macros wherever it is \input.
    style = extract_text_macros(REPO / "hogg_style.tex")
    # lambda replacement: the macro text contains backslashes that re.sub would
    # otherwise mis-read as escape sequences in a plain replacement string.
    tex = re.sub(r"\\input\{[^}]*hogg_style[^}]*\}", lambda _m: style, tex)
    # Drop any remaining \input (deluxetable, endnotes, etc. are handled via compat).
    tex = re.sub(r"\\input\{[^}]*\}", "", tex)

    # Remove preamble noise that pandoc chokes on.
    tex = re.sub(r"\\documentclass\[[^\]]*\]\{[^}]*\}", "", tex)
    tex = re.sub(r"\\documentclass\{[^}]*\}", "", tex)
    tex = re.sub(r"\\usepackage(\[[^\]]*\])?\{[^}]*\}", "", tex)
    tex = re.sub(r"\\bibliography\{[^}]*\}", "", tex)
    tex = re.sub(r"\\linespread\{[^}]*\}", "", tex)
    tex = re.sub(r"\\makeatletter.*?\\makeatother", "", tex, flags=re.DOTALL)
    # Remove the chapter's own \note/\endnote redefinitions; compat \note wins.
    tex = re.sub(r"\\(re)?newcommand\{\\note\}.*", "", tex)
    tex = re.sub(r"\\def\\enotesize.*", "", tex)
    tex = re.sub(r"\\renewcommand\{\\thefootnote\}.*", "", tex)
    tex = re.sub(r"\\renewcommand\{\\sectionmark\}.*", "", tex)
    tex = re.sub(r"\\renewcommand\{\\MakeUppercase\}.*", "", tex)
    # Remove this definition before rewriting author separators below.  Rewriting
    # the command name itself would turn it into ``\renewcommand{\\}{...}`` and
    # make every matrix row break expand to ``\footnotesize and``.
    tex = re.sub(r"\\renewcommand\{\\and\}.*", "", tex)
    # \and (author separator) is only valid inside \author{}; turn into a break.
    tex = re.sub(r"\\and(?![a-zA-Z])", lambda _m: r"\\", tex)

    for name in DROP_WITH_TWO_ARGS:
        tex = strip_command(tex, name, 2)
    for name in DROP_WITH_ARG:
        tex = strip_command(tex, name, 1)
    for name in DROP_BARE:
        tex = re.sub(r"\\" + name + r"(?![a-zA-Z])", "", tex)
    # A DROP_BARE token may have lived inside its own \newcommand{\token}{} def,
    # leaving an invalid \newcommand{}{...}; drop any such empty-name definitions.
    tex = re.sub(r"\\(re)?newcommand\s*\{\s*\}\s*(\[\d\])?\s*\{[^}]*\}", "", tex)

    # deluxetable / table environments pandoc cannot model.
    for env in ("deluxetable", "table"):
        tex = strip_environment(tex, env, preserve_labels=True)

    # ``\\multicolumn`` is a TeX table primitive, not an amsmath/MathJax
    # command. The remaining uses are ellipsis rows inside matrix arrays.
    tex = expand_multicolumn(tex)

    # Pre-expand the hogg_style figure helpers to plain \includegraphics, and
    # unwrap figure/center floats so images render inline instead of becoming
    # empty <figure> shells. Captions are kept as italic paragraphs.
    tex = re.sub(r"\\exampleplottwo\{([^}]*)\}\{([^}]*)\}",
                 lambda m: f"\\includegraphics{{{m.group(1)}}}\n\n"
                           f"\\includegraphics{{{m.group(2)}}}", tex)
    tex = re.sub(r"\\exampleplot\{([^}]*)\}",
                 lambda m: f"\\includegraphics{{{m.group(1)}}}", tex)
    tex = re.sub(r"\\begin\{figure\}(\[[^\]]*\])?", "", tex)
    tex = re.sub(r"\\begin\{center\}", "", tex)
    tex = tex.replace(r"\end{figure}", "").replace(r"\end{center}", "")
    tex = re.sub(r"\\caption\{", r"\\par\\textit{", tex)

    # The title is a starred section; demote to a normal heading pandoc keeps.
    tex = tex.replace(r"\section*", r"\section")
    tex = tex.replace(r"\subsection*", r"\subsection")

    # A few historical equations contain redundant ``$...$`` inline delimiters
    # inside an already-active display environment.  They are invalid in
    # MathJax and make Pandoc preserve literal dollar signs in the output.
    math_environment = re.compile(
        r"\\begin\{(equation|align|eqnarray)\*?\}.*?"
        r"\\end\{\1\*?\}",
        re.DOTALL,
    )
    tex = math_environment.sub(
        lambda match: re.sub(r"(?<!\\)\$(.*?)(?<!\\)\$", r"\1", match.group(0)),
        tex,
    )

    return tex


def assemble(tex: str) -> str:
    """Prepend documentclass + compat defs, keeping the chapter's own preamble.

    The chapter preamble holds the inlined hogg_style macros (e.g. ``\\exampleplot``,
    which wraps ``\\includegraphics``) and the chapter's math macros, so it must be
    fed to pandoc alongside the body — not stripped away.
    """
    head = r"\documentclass{article}" + "\n" + COMPAT_PREAMBLE + "\n"
    if r"\begin{document}" in tex:
        return head + tex
    return head + "\\begin{document}\n" + tex + "\n\\end{document}\n"


def render_pdf_to_png(pdf: Path, out_png: Path) -> bool:
    """Render the first page of a PDF to PNG at ~150 dpi. Returns success."""
    try:
        import pypdfium2 as pdfium
    except Exception:
        return False
    try:
        doc = pdfium.PdfDocument(str(pdf))
        page = doc[0]
        bitmap = page.render(scale=150 / 72)
        bitmap.to_pil().save(out_png)
        return True
    except Exception as exc:  # noqa: BLE001 - best effort, log and skip
        print(f"    ! could not render {pdf.name}: {exc}", file=sys.stderr)
        return False


def resolve_asset(name: str, search_dirs: list[Path]) -> Path | None:
    """Find a figure file by stem, preferring rasterised formats."""
    stem = Path(name).stem
    candidates = []
    for d in search_dirs:
        candidates += [d / f"{stem}.png", d / f"{stem}.jpg", d / name, d / f"{stem}.pdf"]
    for c in candidates:
        if c.exists():
            return c
    return None


def copy_assets(md: str, source: Path, dest_dir: Path) -> str:
    """Copy referenced images next to the page; convert PDFs to PNG. Rewrite paths."""
    search_dirs = [source.parent, source.parent / "pngfigs", source.parent / "code"]
    img_dir = dest_dir / "figs"

    def repl(m: re.Match) -> str:
        alt, target = m.group(1), m.group(2).split()[0].strip('"')
        asset = resolve_asset(target, search_dirs)
        if asset is None:
            return f"*(figure unavailable: `{Path(target).name}`)*"
        img_dir.mkdir(parents=True, exist_ok=True)
        if asset.suffix.lower() == ".pdf":
            out = img_dir / (asset.stem + ".png")
            if not render_pdf_to_png(asset, out):
                return f"*(figure unavailable: `{asset.name}`)*"
        else:
            out = img_dir / asset.name
            shutil.copy2(asset, out)
        return f"![{alt}](figs/{out.name})"

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", repl, md)


def strip_module_docstring(source: str) -> str:
    """Remove a Python module docstring without touching function docstrings.

    Tokenization works for these historical Python 2 sources as well as Python 3,
    unlike parsing them with the current interpreter's ``ast`` module.
    """
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        first = next(
            token for token in tokens
            if token.type not in {
                tokenize.COMMENT,
                tokenize.ENCODING,
                tokenize.NL,
                tokenize.NEWLINE,
            }
        )
    except (StopIteration, tokenize.TokenError, IndentationError):
        return source
    if first.type != tokenize.STRING:
        return source

    lines = source.splitlines(keepends=True)
    start_line, start_col = first.start
    end_line, end_col = first.end
    if start_line == end_line:
        index = start_line - 1
        lines[index] = lines[index][:start_col] + lines[index][end_col:]
    else:
        first_part = lines[start_line - 1][:start_col]
        last_part = lines[end_line - 1][end_col:]
        lines[start_line - 1:end_line] = [first_part + last_part]
    return "".join(lines).lstrip("\r\n")


def code_for_figure(stem: str) -> Path | None:
    """Return the worked-solution source associated with a figure stem."""
    for pattern, filename in FIGURE_CODE:
        if pattern.fullmatch(stem):
            return REPO / "straightline" / "src" / filename
    return None


def insert_code_listings(md: str) -> str:
    """Insert each worked solution before its first corresponding figure."""
    included: set[Path] = set()
    image = re.compile(r"(?m)^!\[[^\]]*\]\(figs/([^)]+)\)$")

    def repl(match: re.Match[str]) -> str:
        stem = Path(match.group(1)).stem
        source_path = code_for_figure(stem)
        if source_path is None or source_path in included or not source_path.exists():
            return match.group(0)
        included.add(source_path)
        code = strip_module_docstring(
            source_path.read_text(encoding="utf-8", errors="replace")
        ).rstrip()
        relative_path = source_path.relative_to(REPO).as_posix()
        listing = (
            "````{admonition} Show solution code\n"
            ":class: dropdown\n\n"
            f"`{relative_path}`\n\n"
            "```python\n"
            f"{code}\n"
            "```\n"
            "````"
        )
        return f"{listing}\n\n{match.group(0)}"

    return image.sub(repl, md)


def normalize_display_math(md: str) -> str:
    """Put Pandoc's ``$$`` delimiters on their own MyST block lines.

    Pandoc's unwrapped CommonMark output commonly places display delimiters in
    the middle of prose.  MyST then treats them as inline delimiters, which can
    shift all subsequent math boundaries and interpret LaTeX ``{{...}}`` as
    substitutions.  Fenced code is deliberately left byte-for-byte unchanged.
    """
    output: list[str] = []
    prose: list[str] = []
    fence_char: str | None = None
    fence_length = 0

    def flush_prose() -> None:
        if not prose:
            return
        text = "".join(prose)
        text = re.sub(r"[ \t]*\$\$[ \t]*", "\n\n$$\n\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        # MyST's amsmath extension handles these environments directly.  If
        # they remain inside ``$$``, Sphinx emits nested display structures
        # such as ``\[\begin{equation}...``, which MathJax rejects.
        text = re.sub(
            r"(?ms)^\$\$\n+(\\begin\{(equation|align|eqnarray)\*?\}.*?"
            r"\\end\{\2\*?\})\n+\$\$$",
            r"\1",
            text,
        )
        output.append(text)
        prose.clear()

    for line in md.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence_char is None:
            if marker:
                flush_prose()
                token = marker.group(1)
                fence_char = token[0]
                fence_length = len(token)
                output.append(line)
            else:
                prose.append(line)
        else:
            output.append(line)
            closing = re.match(
                rf"^ {{0,3}}{re.escape(fence_char)}{{{fence_length},}}[ \t]*(?:\n)?$",
                line,
            )
            if closing:
                fence_char = None
                fence_length = 0
    flush_prose()
    return "".join(output)


def normalize_cross_references(md: str) -> str:
    """Turn Pandoc's reference attributes and label spans into web links."""
    # Figure labels follow captions in LaTeX/Pandoc. Move their target ahead of
    # the image so following section headings do not absorb the target and a
    # figure link scrolls to the actual figure.
    md = re.sub(
        r'(?m)((?:^!\[[^\n]*\]\([^)]+\)\n\n)+)(\*[^\n]*\*)'
        r'\[\]\{#(fig:[^\s}]+)[^}]*\blabel="[^"]*"[^}]*\}',
        lambda match: (
            f'({match.group(3)})=\n'
            f'{match.group(1)}{match.group(2)}'
        ),
        md,
    )

    # Pandoc gives custom problem environments generated IDs and emits their
    # source labels as an empty span on the following line. Put the meaningful
    # LaTeX label directly on the heading so links have stable destinations.
    md = re.sub(
        r"(?m)^(#{1,6}[ \t]+Problem[^\n]*?)[ \t]+\{#problem(?:-\d+)?\}"
        r"\n\n\[\]\{#(prob:[^\s}]+)[^}]*\}[ \t]*",
        lambda match: f"({match.group(2)})=\n{match.group(1)}\n\n",
        md,
    )
    # Unlabelled problems have no incoming references. Remove Pandoc's heading
    # attribute because MyST renders this CommonMark extension as literal text.
    md = re.sub(
        r"(?m)^(#{1,6}[ \t]+Problem[^\n]*?)[ \t]+\{#problem(?:-\d+)?\}$",
        r"\1",
        md,
    )

    # For unnumbered targets Pandoc uses an escaped label as link text, then
    # appends attributes that MyST displays literally. Ordinary fragment links
    # are sufficient because the corresponding anchors are retained below.
    md = re.sub(
        r'\[\\\[([^\]]+)\\\]\]\(#([^)]+)\)'
        r'\{reference-type="ref" reference="[^"]+"\}',
        lambda match: f"[{match.group(1)}](#{match.group(2)})",
        md,
    )
    md = re.sub(
        r'(\[[^\]]+\]\(#[^)]+\))'
        r'\{reference-type="ref" reference="[^"]+"\}',
        r"\1",
        md,
    )

    # Labels attached to captions and omitted tables arrive as empty Pandoc
    # spans. Convert them to MyST's native explicit-target syntax.
    md = re.sub(
        r'\[\]\{#([^\s}]+)[^}]*\blabel="[^"]*"[^}]*\}',
        lambda match: f'\n\n({match.group(1)})=\n',
        md,
    )
    return md


def convert_chapter(src_rel: str, slug: str, title: str) -> bool:
    source = REPO / src_rel
    if not source.exists():
        print(f"  - skip {src_rel} (missing)")
        return False
    print(f"  - {src_rel} -> chapters/{slug}.md")
    tex = source.read_text(encoding="utf-8", errors="replace")
    doc = assemble(sanitize(tex, source))
    try:
        md = pypandoc.convert_text(
            doc, "commonmark_x", format="latex",
            extra_args=["--wrap=none", "--mathjax"])
    except Exception as exc:  # noqa: BLE001
        dbg = REPO / "book" / "_debug" / f"{slug}.tex"
        dbg.parent.mkdir(parents=True, exist_ok=True)
        dbg.write_text(doc, encoding="utf-8")
        print(f"    ! pandoc failed for {slug}: {str(exc)[:200]} (dumped {dbg})",
              file=sys.stderr)
        return False

    dest_dir = CHAPTERS_DIR
    md = copy_assets(md, source, dest_dir)
    md = normalize_display_math(md)
    if slug == "straightline":
        md = insert_code_listings(md)
    md = normalize_cross_references(md)

    # Drop a leading duplicate title heading pandoc may emit; we add our own.
    md = re.sub(r"\A\s*#\s+.*\n", "", md, count=1)
    header = f"# {title}\n\n"
    (dest_dir / f"{slug}.md").write_text(header + md.strip() + "\n", encoding="utf-8")
    return True


def write_toc(converted: list[tuple[str, str]]) -> None:
    lines = ["# Auto-generated by scripts/convert.py — do not edit by hand.",
             "format: jb-book", "root: intro", "chapters:"]
    for slug, _title in converted:
        lines.append(f"  - file: chapters/{slug}")
    (BOOK / "_toc.yml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  wrote _toc.yml ({len(converted)} chapters)")


def main() -> int:
    print(f"pandoc: {pypandoc.get_pandoc_version()}")
    CHAPTERS_DIR.mkdir(parents=True, exist_ok=True)
    converted: list[tuple[str, str]] = []
    for src_rel, slug, title in CHAPTERS:
        if convert_chapter(src_rel, slug, title):
            converted.append((slug, title))
    write_toc(converted)
    print(f"Done: {len(converted)}/{len(CHAPTERS)} chapters converted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
