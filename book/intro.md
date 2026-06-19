# Data Analysis Recipes

*Chapters from David W. Hogg's (so-far non-existent) book on data analysis,
converted from LaTeX to a web-readable [Jupyter Book](https://jupyterbook.org).*

These are working notes and chapters on data analysis, statistical inference, and
the practice of fitting models to data — written over many years by David W. Hogg
and collaborators (Jo Bovy, Dustin Lang, and others). The most complete and widely
cited chapter is **Fitting a model to data**, the famous "straight line" tutorial.

```{admonition} About this edition
:class: note
This site is a mechanical conversion of the original LaTeX sources into MyST
markdown. Some chapters are early drafts or stubs, some cross-references and
figures from the print edition are missing on the web, and endnotes appear as
footnotes. For the canonical typeset PDFs, build the LaTeX sources directly or
see the upstream repository.
```

## License

Copyright the authors. All rights reserved. If you have interest in using or
re-using any of this content, get in touch with Hogg. See the repository `README`
for details.

## How this book is built

The HTML you are reading is generated from `.tex` files by a small pipeline:

1. `scripts/convert.py` sanitises each chapter's LaTeX and runs **pandoc**
   (bundled via `pypandoc-binary`) to produce MyST markdown, rendering PDF
   figures to PNG along the way.
2. **Jupyter Book** builds the markdown into a static HTML site.
3. A GitHub Actions workflow publishes that site to **GitHub Pages**.

To reproduce locally:

```bash
uv sync
uv run dar-convert     # .tex -> book/chapters/*.md  (+ _toc.yml)
uv run jupyter-book build book
# open book/_build/html/index.html
```
