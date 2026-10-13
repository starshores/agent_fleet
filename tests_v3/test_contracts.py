"""Contract checks run solely on synthetic manifests and temporary directories."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from masteragent.contracts import BusinessPackage, ContractError, load_package, parse_package


EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "v3"


def manifest() -> dict:
    return json.loads((EXAMPLES / "business-a.json").read_text(encoding="utf-8"))


class PackageContractTests(unittest.TestCase):
    def assert_invalid(self, value, code):
        with self.assertRaises(ContractError) as caught:
            parse_package(value)
        self.assertEqual(code, caught.exception.code)
        self.assertTrue(str(caught.exception))

    def test_two_unrelated_teams_share_role_id_without_platform_roles(self):
        first = load_package(EXAMPLES / "business-a.json")
        second = load_package(EXAMPLES / "business-b.json")
        self.assertIsInstance(first, BusinessPackage)
        self.assertNotEqual(first.package_id, second.package_id)
        self.assertNotEqual(first.get_role("reviewer"), second.get_role("reviewer"))
        self.assertTrue(set(first.tools).isdisjoint(second.tools))
        self.assertTrue(set(first.workflows).isdisjoint(second.workflows))
        self.assertNotIn("Master", {role["role_id"] for role in first.roles + second.roles})
        self.assertNotIn("BETA_WORKSHOP_ONLY", json.dumps(first.manifest))
        self.assertNotIn("ALPHA_CATALOGUE_ONLY", json.dumps(second.manifest))

    def test_digest_is_canonical_utf8_and_order_independent(self):
        value = manifest()
        value["knowledge"]["catalogue-policy"] = "测试资料"
        first = parse_package(value)
        second = parse_package(dict(reversed(list(value.items()))))
        expected = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        self.assertEqual(expected, first.digest)
        self.assertEqual(first.digest, second.digest)
        value["version"] = "1.0.1"
        self.assertNotEqual(first.digest, parse_package(value).digest)

    def test_snapshot_is_immutable_and_detached_from_input(self):
        value = manifest()
        package = parse_package(value)
        digest = package.digest
        value["roles"][0]["instructions"] = "Changed caller input"
        value["knowledge"].clear()
        self.assertNotEqual(value["roles"][0]["instructions"], package.roles[0]["instructions"])
        with self.assertRaises(FrozenInstanceError):
            package.version = "4.0.0"
        with self.assertRaises(TypeError):
            package.manifest["version"] = "4.0.0"
        with self.assertRaises(TypeError):
            package.roles[0]["tools"].append("extra")
        with self.assertRaises(TypeError):
            package.knowledge.clear()
        detached = package.get_role("reviewer")
        detached["tools"].clear()
        self.assertTrue(package.get_role("reviewer")["tools"])
        self.assertEqual(digest, package.digest)
        self.assertEqual(package.manifest, json.loads(json.dumps(package.manifest)))
        self.assertEqual(package.digest, parse_package(deepcopy(package.manifest)).digest)
        self.assertEqual(package.package_id, asdict(package)["package_id"])

    def test_minimal_team_needs_no_tools_knowledge_or_workflows(self):
        value = manifest()
        value["roles"] = [value["roles"][0]]
        value["roles"][0]["tools"] = []
        value["tools"] = {}
        value["knowledge"] = {}
        value["workflows"] = {}
        self.assertEqual(1, len(parse_package(value).roles))

    def test_arbitrary_business_prose_is_not_a_host_configuration(self):
        value = manifest()
        value["roles"][0]["instructions"] = "Explain the term session_id and a sample C:\\example path in a lesson."
        self.assertEqual(value["roles"][0]["instructions"], parse_package(value).roles[0]["instructions"])

    def test_unknown_role_is_actionable(self):
        with self.assertRaises(ContractError) as caught:
            parse_package(manifest()).get_role("absent")
        self.assertEqual("role_not_found", caught.exception.code)

    def test_schema_versions_reject_booleans_numbers_and_unsupported_values(self):
        for field, code in (("schema_version", "unsupported_schema"), ("platform_api", "unsupported_platform_api")):
            for invalid in (True, False, 1.0, "1", None, 0, 2):
                with self.subTest(field=field, invalid=invalid):
                    value = manifest()
                    value[field] = invalid
                    self.assert_invalid(value, code)

    def test_missing_and_unknown_fields_are_not_defaulted(self):
        for field in manifest():
            with self.subTest(missing=field):
                value = manifest()
                del value[field]
                self.assert_invalid(value, "missing_field")
        for field in ("cwd", "credentials", "session_id", "state", "default_business"):
            with self.subTest(unknown=field):
                value = manifest()
                value[field] = "forbidden deployment/runtime field"
                self.assert_invalid(value, "unknown_field")

    def test_nested_unknown_fields_are_rejected(self):
        for section, index in (("roles", 0), ("tools", "catalogue-echo"), ("workflows", "catalogue-review")):
            with self.subTest(section=section):
                value = manifest()
                value[section][index]["cwd"] = "deployment detail"
                self.assert_invalid(value, "unknown_field")

    def test_identifiers_cannot_escape_or_alias_filesystem_names(self):
        for invalid in ("../business", "a/b", "a\\b", ".", "..", "A", "with space", "con", "aux", "com1", "lpt9", "a.", "a" * 65):
            with self.subTest(identifier=invalid):
                value = manifest()
                value["package_id"] = invalid
                self.assert_invalid(value, "invalid_identifier")

    def test_semantic_versioning(self):
        for valid in ("0.0.0", "1.2.3", "1.0.0-rc.1", "1.0.0+build.001", "2.0.0-alpha.12+build"):
            value = manifest()
            value["version"] = valid
            self.assertEqual(valid, parse_package(value).version)
        for invalid in ("1", "1.0", "v1.0.0", "01.0.0", "1.0.0-01", "1.0.0-rc..1", "1.0.0 "):
            with self.subTest(version=invalid):
                value = manifest()
                value["version"] = invalid
                self.assert_invalid(value, "invalid_version")

    def test_empty_roles_rejected(self):
        value = manifest()
        value["roles"] = []
        self.assert_invalid(value, "empty_collection")

    def test_duplicate_and_casefold_role_identifiers_rejected(self):
        for duplicate in ("reviewer", "REVIEWER"):
            value = manifest()
            value["roles"][1]["role_id"] = duplicate
            self.assert_invalid(value, "identifier_collision")

    def test_casefold_dictionary_collisions_rejected(self):
        for section in ("tools", "knowledge", "workflows"):
            value = manifest()
            key = next(iter(value[section]))
            value[section][key.upper()] = deepcopy(value[section][key])
            self.assert_invalid(value, "identifier_collision")

    def test_unknown_and_duplicate_role_tool_grants_rejected(self):
        for refs, code in ((["missing"], "invalid_reference"), (["catalogue-echo", "catalogue-echo"], "duplicate_reference"), (["catalogue-echo", "CATALOGUE-ECHO"], "duplicate_reference")):
            value = manifest()
            value["roles"][0]["tools"] = refs
            self.assert_invalid(value, code)

    def test_workflow_references_must_be_declared_unique_and_nonempty(self):
        for refs, code in ((["missing"], "invalid_reference"), (["reviewer", "reviewer"], "duplicate_reference"), ([], "empty_collection")):
            value = manifest()
            value["workflows"]["catalogue-review"]["roles"] = refs
            self.assert_invalid(value, code)

    def test_arbitrary_adapter_identifier_is_portable(self):
        value = manifest()
        value["tools"]["catalogue-echo"]["adapter"] = "custom-redactor-v2"
        self.assertEqual("custom-redactor-v2", parse_package(value).tools["catalogue-echo"]["adapter"])

    def test_malformed_adapter_identifiers_rejected(self):
        for invalid in ("../shell", "module.function", "Uppercase", "con"):
            with self.subTest(adapter=invalid):
                value = manifest()
                value["tools"]["catalogue-echo"]["adapter"] = invalid
                self.assert_invalid(value, "invalid_identifier")

    def test_wrong_container_and_text_types_rejected(self):
        for invalid in ([], None, "text", 1):
            self.assert_invalid(invalid, "invalid_type")
        for field, invalid in (("roles", {}), ("tools", []), ("knowledge", []), ("workflows", []), ("package_id", None), ("version", 1)):
            value = manifest()
            value[field] = invalid
            self.assert_invalid(value, "invalid_type")
        for field, invalid in (("instructions", 7), ("display_name", None), ("tools", "catalogue-echo")):
            value = manifest()
            value["roles"][0][field] = invalid
            self.assert_invalid(value, "invalid_type")
        value = manifest()
        value["knowledge"]["catalogue-policy"] = {"secret": "not text"}
        self.assert_invalid(value, "invalid_type")
        value = manifest()
        value["roles"][0]["instructions"] = "\ud800"
        self.assert_invalid(value, "invalid_text")

    def test_duplicate_json_keys_rejected_at_every_depth(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "package.json"
            for raw in ('{"schema_version":1,"schema_version":1}', '{"tools":{"echo":{"adapter":"echo","adapter":"uppercase"}}}'):
                path.write_text(raw, encoding="utf-8")
                with self.assertRaises(ContractError) as caught:
                    load_package(path)
                self.assertEqual("duplicate_key", caught.exception.code)

    def test_missing_malformed_and_nonstandard_json_fail_without_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "absent.json"
            with self.assertRaises(ContractError) as caught:
                load_package(path)
            self.assertEqual("package_read_error", caught.exception.code)
            for raw in ("{", '{"schema_version":NaN}', '{"schema_version":Infinity}'):
                path.write_text(raw, encoding="utf-8")
                with self.assertRaises(ContractError) as caught:
                    load_package(path)
                self.assertEqual("invalid_json", caught.exception.code)
            path.write_bytes(b"\xff")
            with self.assertRaises(ContractError) as caught:
                load_package(path)
            self.assertEqual("package_read_error", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
