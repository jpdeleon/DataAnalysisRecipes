# Data Analysis Recipes

*The straight-line fitting chapter from David W. Hogg's (so-far non-existent)
book on data analysis, converted from LaTeX to a web-readable
[Jupyter Book](https://jupyterbook.org).*

**Fitting a model to data** is the widely cited "straight line" tutorial by
David W. Hogg, Jo Bovy, and Dustin Lang.

```{admonition} About this edition
:class: note
This site is a mechanical conversion of the original LaTeX sources into MyST
markdown. Some cross-references and figures from the print edition are missing
on the web, and endnotes appear as footnotes. For the canonical typeset PDF,
build the LaTeX source directly or see the upstream repository.
```

## License

Copyright the authors. All rights reserved. If you have interest in using or
re-using any of this content, get in touch with Hogg. See the repository `README`
for details.

## How this book is built

The HTML you are reading is generated from `.tex` files by a small pipeline:

1. `scripts/convert.py` sanitises `straightline/straightline.tex` and runs
   **pandoc** (bundled via `pypandoc-binary`) to produce MyST markdown, rendering
   PDF figures to PNG and inserting collapsible solution code along the way.
2. **Jupyter Book** builds the markdown into a static HTML site.
3. A GitHub Actions workflow publishes that site to **GitHub Pages**.

To reproduce locally:

```bash
uv sync
uv run dar-convert     # straightline.tex -> MyST (+ figures and solution code)
uv run jupyter-book build book
# open book/_build/html/index.html
```
