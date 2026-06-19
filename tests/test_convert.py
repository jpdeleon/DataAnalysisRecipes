import unittest
from pathlib import Path

from scripts.convert import normalize_cross_references, normalize_display_math, sanitize


class SanitizeTests(unittest.TestCase):
    def test_and_definition_does_not_redefine_line_breaks(self) -> None:
        source = Path("straightline/straightline.tex")
        result = sanitize(source.read_text(encoding="utf-8"), source)

        self.assertNotIn(r"\renewcommand{\\}", result)
        self.assertNotIn(r"\footnotesize{and}", result)
        self.assertNotIn(r"\begin{array}{c} $b$", result)

    def test_expands_multicolumn_inside_math_arrays(self) -> None:
        source = Path("straightline/straightline.tex")
        result = sanitize(source.read_text(encoding="utf-8"), source)

        self.assertNotIn(r"\multicolumn{2}{c}{\cdots}", result)
        self.assertNotIn(r"\multicolumn{4}{c}{\cdots}", result)
        self.assertIn(r"\cdots & \cdots", result)
        self.assertIn(r"\cdots & \cdots & \cdots & \cdots", result)

    def test_preserves_labels_for_pandoc(self) -> None:
        source = Path("straightline/straightline.tex")
        result = sanitize(source.read_text(encoding="utf-8"), source)

        self.assertIn(r"\label{prob:standard}", result)
        self.assertIn(r"\label{fig:standard}", result)
        self.assertIn(r"\label{table:data_allerr}", result)


class NormalizeCrossReferencesTests(unittest.TestCase):
    def test_uses_problem_label_as_heading_id(self) -> None:
        markdown = (
            "#### Problem\N{NO-BREAK SPACE}: {#problem-1}\n\n"
            '[]{#prob:standard label="prob:standard"} Problem text.\n'
        )

        self.assertEqual(
            normalize_cross_references(markdown),
            "(prob:standard)=\n#### Problem\N{NO-BREAK SPACE}:\n\nProblem text.\n",
        )

    def test_removes_generated_id_from_unlabelled_problem(self) -> None:
        markdown = "#### Problem\N{NO-BREAK SPACE}: {#problem-3}\n\nProblem text.\n"

        self.assertEqual(
            normalize_cross_references(markdown),
            "#### Problem\N{NO-BREAK SPACE}:\n\nProblem text.\n",
        )

    def test_simplifies_pandoc_reference_links(self) -> None:
        markdown = (
            'Figure [\\[fig:standard\\]](#fig:standard)'
            '{reference-type="ref" reference="fig:standard"}.\n'
        )

        self.assertEqual(
            normalize_cross_references(markdown),
            "Figure [fig:standard](#fig:standard).\n",
        )

    def test_converts_other_label_spans_to_myst_targets(self) -> None:
        markdown = '*Caption.*[]{#fig:standard label="fig:standard"}\n'

        self.assertEqual(
            normalize_cross_references(markdown),
            '*Caption.*\n\n(fig:standard)=\n\n',
        )

    def test_moves_figure_target_before_image(self) -> None:
        markdown = (
            "![image](figs/ex2.png)\n\n"
            '*Caption.*[]{#fig:standard label="fig:standard"}\n'
        )

        self.assertEqual(
            normalize_cross_references(markdown),
            "(fig:standard)=\n"
            "![image](figs/ex2.png)\n\n"
            "*Caption.*\n",
        )


class NormalizeDisplayMathTests(unittest.TestCase):
    def test_moves_display_delimiters_onto_separate_lines(self) -> None:
        markdown = "Before $$x + y$$ after.\n"

        self.assertEqual(
            normalize_display_math(markdown),
            "Before\n\n$$\n\nx + y\n\n$$\n\nafter.\n",
        )

    def test_leaves_fenced_code_unchanged(self) -> None:
        markdown = "```python\nvalue = '$$not math$$'\n```\n"

        self.assertEqual(normalize_display_math(markdown), markdown)

    def test_unwraps_complete_ams_environment(self) -> None:
        markdown = "Before $$\\begin{equation}\nx = 1\n\\end{equation}$$ after.\n"

        self.assertEqual(
            normalize_display_math(markdown),
            "Before\n\n\\begin{equation}\nx = 1\n\\end{equation}\n\nafter.\n",
        )


if __name__ == "__main__":
    unittest.main()
