import tempfile
import unittest
from pathlib import Path

from scripts.update_repositories import (
    END_MARKER,
    START_MARKER,
    classify_repositories,
    render_section,
    update_readme,
    validate_config,
)


def repository(
    name,
    *,
    description=None,
    topics=None,
    fork=False,
    archived=False,
    disabled=False,
):
    return {
        "name": name,
        "html_url": f"https://github.com/HD-2004/{name}",
        "description": description,
        "topics": topics or [],
        "fork": fork,
        "archived": archived,
        "disabled": disabled,
        "updated_at": "2026-07-29T00:00:00Z",
    }


class RepositoryUpdaterTests(unittest.TestCase):
    def setUp(self):
        self.config = validate_config(
            {
                "github_username": "HD-2004",
                "match_fields": ["name", "description", "topics"],
                "sort_by": "name",
                "exclude": {
                    "names": ["HD-2004"],
                    "forks": False,
                    "archived": True,
                    "disabled": True,
                },
                "categories": [
                    {
                        "id": "learning",
                        "title": "Learning",
                        "keywords": ["learning", "workshop"],
                    },
                    {
                        "id": "projects",
                        "title": "Projects",
                        "keywords": ["project"],
                        "default": True,
                    },
                ],
            }
        )

    def test_classifies_names_descriptions_and_topics(self):
        repositories = [
            repository("learning-terraform"),
            repository("AgentForge_Workshop", fork=True),
            repository("notes", topics=["learning"]),
            repository("MLN111-Project"),
            repository("SignalScout", description="Autonomous risk agent"),
        ]

        classified = classify_repositories(repositories, self.config)

        self.assertEqual(
            [repo["name"] for repo in classified["learning"]],
            ["AgentForge_Workshop", "learning-terraform", "notes"],
        )
        self.assertEqual(
            [repo["name"] for repo in classified["projects"]],
            ["MLN111-Project", "SignalScout"],
        )

    def test_excludes_profile_archived_and_disabled_repositories(self):
        repositories = [
            repository("HD-2004"),
            repository("old-project", archived=True),
            repository("disabled-project", disabled=True),
            repository("active-project"),
        ]

        classified = classify_repositories(repositories, self.config)

        self.assertEqual(
            [repo["name"] for repo in classified["projects"]],
            ["active-project"],
        )

    def test_updates_only_the_generated_readme_section(self):
        classified = classify_repositories(
            [repository("learning-python"), repository("portfolio")],
            self.config,
        )
        generated = render_section(classified, self.config)
        original = (
            f"# Profile\n\nBefore\n{START_MARKER}\nold\n"
            f"{END_MARKER}\n\nAfter\n"
        )

        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(original, encoding="utf-8")

            changed = update_readme(readme, generated)
            result = readme.read_text(encoding="utf-8")

        self.assertTrue(changed)
        self.assertIn("Before", result)
        self.assertIn("After", result)
        self.assertIn("learning-python", result)
        self.assertIn("portfolio", result)
        self.assertNotIn("\nold\n", result)

    def test_second_update_is_idempotent(self):
        classified = classify_repositories(
            [repository("learning-python")],
            self.config,
        )
        generated = render_section(classified, self.config)

        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                f"{START_MARKER}\nold\n{END_MARKER}",
                encoding="utf-8",
            )
            self.assertTrue(update_readme(readme, generated))
            self.assertFalse(update_readme(readme, generated))


if __name__ == "__main__":
    unittest.main()
