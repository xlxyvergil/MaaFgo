"""Release 发布边界：签名身份、调试标志、ABI 和版本不得混淆。"""
import importlib.util
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_android_release", ROOT / "tools/check_android_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseVerificationTests(unittest.TestCase):
    def setUp(self):
        self.digest = release.certificate_digest(release.CERTIFICATE)
        self.signers = f"Verifies\nNumber of signers: 1\nV2 Signer: certificate SHA-256 digest: {self.digest}\n"
        self.badging = ("package: name='com.aliothmoon.maafw.maafgo' versionCode='1200' "
                        "versionName='2.0.11-beta.2' platformBuildVersionName='16'\n"
                        "native-code: 'arm64-v8a'\n")

    def validate(self, signers=None, badging=None, tag="v2.0.11-beta.2"):
        return release.validate_metadata(self.signers if signers is None else signers,
                                         self.badging if badging is None else badging,
                                         self.digest, "arm64-v8a", tag)

    def test_signed_release_accepts_beta_and_stable_versions(self):
        self.assertFalse(self.validate()["debuggable"])
        stable = self.badging.replace("2.0.11-beta.2", "2.0.11")
        self.assertEqual(self.validate(badging=stable, tag="v2.0.11")["version_name"], "2.0.11")

    def test_certificate_digest_accepts_uppercase_colon_form(self):
        formatted = ":".join(self.digest[i:i + 2] for i in range(0, len(self.digest), 2)).upper()
        self.assertEqual(self.validate(signers=self.signers.replace(self.digest, formatted))["certificate_sha256"],
                         self.digest)

    def test_old_and_multi_scheme_signer_formats_are_supported(self):
        legacy = self.signers.replace("V2 Signer:", "Signer #1")
        self.assertEqual(self.validate(signers=legacy)["certificate_sha256"], self.digest)
        multiple_schemes = self.signers + f"V3 Signer: certificate SHA-256 digest: {self.digest}\n"
        self.assertEqual(self.validate(signers=multiple_schemes)["certificate_sha256"], self.digest)

    def test_unrelated_signer_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "正式证书"):
            self.validate(signers=self.signers.replace(self.digest, "0" * 64))

    def test_missing_and_additional_signers_are_rejected(self):
        multiple = self.signers.replace("Number of signers: 1", "Number of signers: 2")
        multiple += f"Signer #2 certificate SHA-256 digest: {self.digest}\n"
        for signers in ("Verifies\n", multiple, self.signers.replace("Number of signers: 1\n", "")):
            with self.subTest(signers=signers), self.assertRaisesRegex(ValueError, "正式证书"):
                self.validate(signers=signers)

    def test_debuggable_apk_is_rejected_even_with_the_right_certificate(self):
        with self.assertRaisesRegex(ValueError, "debuggable"):
            self.validate(badging=self.badging + "application-debuggable\n")

    def test_wrong_and_multiple_abis_are_rejected(self):
        for native in ("'x86_64'", "'arm64-v8a' 'x86_64'", ""):
            with self.subTest(native=native), self.assertRaisesRegex(ValueError, "原生架构"):
                self.validate(badging=self.badging.replace("'arm64-v8a'", native))

    def test_tag_version_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "不一致"):
            self.validate(tag="v2.0.11")

    def test_missing_manifest_metadata_is_rejected(self):
        for badging in ("", "native-code: 'arm64-v8a'\n", self.badging.replace("versionCode='1200'", "versionCode='0'")):
            with self.subTest(badging=badging), self.assertRaises(ValueError):
                self.validate(badging=badging)


class AndroidWorkflowTriggerTests(unittest.TestCase):
    @staticmethod
    def trigger_block(path):
        workflow = path.read_text(encoding="utf-8")
        # 仅提取已有工作流的顶层 on 区块，不解析脚本或 GitHub 表达式。
        match = re.search(r"^on:\n(?P<triggers>(?:[ \t].*\n|\n)*)", workflow, re.MULTILINE)
        if match is None:
            raise AssertionError(f"Missing workflow trigger block: {path}")
        return match.group("triggers")

    def test_apk_retains_pr_manual_and_reusable_triggers_without_branch_push(self):
        block = self.trigger_block(ROOT / ".github/workflows/android.yml")
        events = set(re.findall(r"^  ([a-z_]+):", block, re.MULTILINE))
        self.assertEqual(events, {"pull_request", "workflow_dispatch", "workflow_call"})

    def test_unified_release_still_builds_android_only_for_v_tags(self):
        workflow = (ROOT / ".github/workflows/install.yml").read_text(encoding="utf-8")
        block = self.trigger_block(ROOT / ".github/workflows/install.yml")
        self.assertRegex(block, r'push:\n    tags:\n      - "v\*"')
        self.assertNotIn("    branches:", block.split("  pull_request:", 1)[0])
        job = workflow.split("  build-android:\n", 1)[1].split("\n  changelog:", 1)[0]
        self.assertIn("if: startsWith(github.ref, 'refs/tags/v')", job)
        self.assertIn("uses: ./.github/workflows/android.yml", job)
        self.assertIn("release: true", job)


if __name__ == "__main__":
    unittest.main()
