"""Unit tests for rhoai_nightly channel selection.

Run with: PYTHONPATH=src python -m unittest discover -s tests -v
"""

import tempfile
import unittest
from pathlib import Path

from rhoai_devtest.rhoai_nightly import _bundle_version_key, _pick_channel_from_configs

FBC_CATALOG = """\
schema: olm.bundle
---
image: registry.redhat.io/rhoai/odh-operator-bundle@sha256:ea2
name: rhods-operator.3.6.0-ea.2
package: rhods-operator
schema: olm.bundle
---
image: registry.redhat.io/rhoai/odh-operator-bundle@sha256:fresh
name: rhods-operator.3.6.0
package: rhods-operator
schema: olm.bundle
---
image: registry.redhat.io/rhoai/odh-operator-bundle@sha256:old
name: rhods-operator.2.25.13
package: rhods-operator
schema: olm.bundle
---
image: registry.redhat.io/other/other-bundle@sha256:other
name: other-operator.9.9.9
package: other-package
schema: olm.bundle
---
entries:
- name: rhods-operator.3.6.0-ea.2
name: beta
package: rhods-operator
schema: olm.channel
---
entries:
- name: rhods-operator.3.6.0
- name: rhods-operator.3.5.3
name: stable-3.6
package: rhods-operator
schema: olm.channel
---
entries:
- name: rhods-operator.3.6.0
name: stable-3.x
package: rhods-operator
schema: olm.channel
---
entries:
- name: rhods-operator.2.25.13
name: stable
package: rhods-operator
schema: olm.channel
"""


class TestBundleVersionKey(unittest.TestCase):
    def test_release_beats_prerelease_of_same_version(self):
        self.assertGreater(
            _bundle_version_key("rhods-operator.3.6.0"),
            _bundle_version_key("rhods-operator.3.6.0-ea.2"),
        )

    def test_numeric_ordering(self):
        self.assertGreater(
            _bundle_version_key("rhods-operator.3.6.0"),
            _bundle_version_key("rhods-operator.3.5.3"),
        )
        self.assertGreater(
            _bundle_version_key("rhods-operator.2.25.13"),
            _bundle_version_key("rhods-operator.2.9.0"),
        )

    def test_newer_prerelease_beats_older_prerelease(self):
        self.assertGreater(
            _bundle_version_key("rhods-operator.3.6.0-ea.3"),
            _bundle_version_key("rhods-operator.3.6.0-ea.2"),
        )


class TestPickChannel(unittest.TestCase):
    def test_prefers_channel_with_newest_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            configs = Path(tmp) / "configs" / "rhods-operator"
            configs.mkdir(parents=True)
            (configs / "catalog.yaml").write_text(FBC_CATALOG)
            channel, head = _pick_channel_from_configs(Path(tmp) / "configs")
            self.assertEqual(channel, "stable-3.6")
            self.assertEqual(head, "rhods-operator.3.6.0")

    def test_no_channels_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            configs = Path(tmp) / "configs" / "rhods-operator"
            configs.mkdir(parents=True)
            (configs / "catalog.yaml").write_text("schema: olm.bundle\n")
            with self.assertRaises(ValueError):
                _pick_channel_from_configs(Path(tmp) / "configs")


if __name__ == "__main__":
    unittest.main()
