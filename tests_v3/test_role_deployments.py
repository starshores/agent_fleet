"""Role deployment boundaries with synthetic packages and temporary paths."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from masteragent.errors import PlatformError
from masteragent.paths import PathSafetyError, SOURCE_ROOT
from masteragent.runtime import CAPABILITIES, build_prompt
from masteragent.service import Platform


class RoleDeploymentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ma3-role-deployments-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.default = self.directory("default")
        self.review = self.directory("review")
        self.edit = self.directory("edit")
        self.package = self.root / "package.json"
        self.package.write_text(json.dumps({
            "schema_version": 1, "platform_api": 1, "package_id": "synthetic", "version": "1.0.0",
            "roles": [{"role_id": role, "display_name": role, "instructions": "synthetic instructions",
                       "tools": ["text"]} for role in ("reviewer", "editor")],
            "knowledge": {"reference": "synthetic knowledge"},
            "tools": {"text": {"adapter": "echo", "description": "text capability"}},
            "workflows": {"review": {"roles": ["reviewer", "editor"], "description": "synthetic workflow"}},
        }), encoding="utf-8")
        self.state = self.root / "state"
        self.platform = Platform(self.state)

    def directory(self, name):
        path = self.root / name
        path.mkdir(parents=True)
        return path

    def mapping(self):
        return {"reviewer": {"workspace": self.review, "access": "read-only"},
                "editor": {"workspace": self.edit, "access": "read-write"}}

    def install(self, **kwargs):
        return self.platform.install("a", self.package, self.default, **kwargs)

    def assert_code(self, code, callback, *args, **kwargs):
        with self.assertRaises(PlatformError) as caught:
            callback(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_legacy_install_expands_to_explicit_none_bindings(self):
        result = self.install()
        self.assertEqual(result["workspace"], str(self.default))
        expected = {role: {"workspace": str(self.default), "access": "none"}
                    for role in ("reviewer", "editor")}
        self.assertEqual(self.platform.view("a")["role_deployments"], expected)
        self.platform.activate("a")
        for role in expected:
            context = self.platform.context("a", role)
            self.assertEqual(context["workspace"], str(self.default))
            self.assertEqual(context["workspace_access"], "none")
        self.assertEqual(self.platform.deployment_workspaces(), [self.default])

    def test_role_context_has_only_its_own_binding(self):
        self.install(role_deployments=self.mapping())
        self.platform.activate("a")
        reviewer = self.platform.bind("a", "reviewer")
        editor = self.platform.bind("a", "editor")
        for actor, path, access, other in ((reviewer, self.review, "read-only", self.edit),
                                          (editor, self.edit, "read-write", self.review)):
            context = self.platform.context_for(actor)
            self.assertEqual(context["workspace"], str(path))
            self.assertEqual(context["workspace_access"], access)
            self.assertNotIn(str(other), json.dumps(context))
        self.assertEqual(set(self.platform.deployment_workspaces()), {self.default, self.review, self.edit})

    def test_partial_override_preserves_other_role_default(self):
        self.install(role_deployments={"reviewer": self.mapping()["reviewer"]})
        self.platform.activate("a")
        self.assertEqual(self.platform.context("a", "reviewer")["workspace"], str(self.review))
        editor = self.platform.context("a", "editor")
        self.assertEqual((editor["workspace"], editor["workspace_access"]), (str(self.default), "none"))

    def test_same_installation_can_explicitly_share_exact_root(self):
        bindings = self.mapping()
        bindings["editor"]["workspace"] = self.review
        self.install(role_deployments=bindings)
        self.platform.activate("a")
        self.assertEqual(self.platform.context("a", "reviewer")["workspace"],
                         self.platform.context("a", "editor")["workspace"])
        self.assertEqual(self.platform.context("a", "editor")["workspace_access"], "read-write")

    def test_nested_role_roots_are_not_implicit_sharing(self):
        bindings = self.mapping()
        bindings["editor"]["workspace"] = self.directory("review/nested")
        with self.assertRaises(PathSafetyError) as caught:
            self.install(role_deployments=bindings)
        self.assertEqual(caught.exception.code, "workspace_overlap")
        self.assertEqual(self.platform.status()["installations"], [])

    def test_unknown_roles_and_invalid_binding_shapes_leave_no_installation(self):
        invalid = [[], {"unknown": {"workspace": self.review, "access": "none"}},
                   {"reviewer": str(self.review)}, {"reviewer": {"workspace": self.review}},
                   {"reviewer": {"workspace": self.review, "access": "admin"}},
                   {"reviewer": {"workspace": self.review, "access": ["read-only"]}},
                   {"reviewer": {"workspace": self.review, "access": "none", "runtime": "x"}}]
        for binding in invalid:
            with self.subTest(binding=binding):
                self.assert_code("invalid_role_deployments", self.install, role_deployments=binding)
                self.assertEqual(self.platform.status()["installations"], [])

    def test_every_supplied_path_is_validated_before_installation(self):
        for invalid in ("relative", "", self.root / "missing", self.state, SOURCE_ROOT):
            bindings = self.mapping()
            bindings["editor"]["workspace"] = invalid
            with self.subTest(path=invalid), self.assertRaises(PathSafetyError):
                self.install(role_deployments=bindings)
            self.assertEqual(self.platform.status()["installations"], [])

    def test_cross_installation_default_and_override_cannot_overlap_any_root(self):
        self.install(role_deployments=self.mapping())
        other = self.directory("other")
        nested = self.directory("edit/nested")
        cases = [(self.review, None), (nested, None),
                 (other, {"reviewer": {"workspace": self.edit, "access": "none"}}),
                 (other, {"editor": {"workspace": self.default, "access": "read-only"}})]
        for default, bindings in cases:
            with self.subTest(default=default, bindings=bindings), self.assertRaises(PathSafetyError) as caught:
                self.platform.install("b", self.package, default, role_deployments=bindings)
            self.assertEqual(caught.exception.code, "workspace_overlap")
        self.assertEqual(len(self.platform.status()["installations"]), 1)

    def test_uninstall_preserves_reserved_roots_and_bindings(self):
        self.install(role_deployments=self.mapping())
        before = self.platform.view("a")["role_deployments"]
        self.platform.uninstall("a")
        self.assertEqual(self.platform.view("a")["role_deployments"], before)
        self.assertEqual(set(self.platform.deployment_workspaces()), {self.default, self.review, self.edit})
        with self.assertRaises(PathSafetyError):
            self.platform.install("b", self.package, self.review)

    def test_deleted_role_workspace_blocks_activation_and_current_use(self):
        self.install(role_deployments=self.mapping())
        self.edit.rmdir()
        with self.assertRaises(PathSafetyError):
            self.platform.activate("a")
        self.edit.mkdir()
        self.platform.activate("a")
        actor = self.platform.bind("a", "editor")
        task = self.platform.submit("a", "editor", "synthetic task")
        self.edit.rmdir()
        for operation, args in ((self.platform.context_for, (actor,)),
                                (self.platform.claim, (actor, task["task_id"], "text"))):
            with self.subTest(operation=operation.__name__), self.assertRaises(PathSafetyError):
                operation(*args)
        self.assertEqual(self.platform.attempts("a"), [])

    def test_a_b_a_reactivation_preserves_mapping_but_rejects_old_actor_and_task(self):
        self.install(role_deployments=self.mapping())
        self.platform.activate("a")
        original = self.platform.bind("a", "reviewer")
        task = self.platform.submit("a", "reviewer", "retained task")
        self.platform.deactivate("a")
        other = self.directory("other")
        self.platform.install("b", self.package, other)
        self.platform.activate("b")
        actor_b = self.platform.bind("b", "reviewer")
        self.assertEqual(self.platform.context_for(actor_b)["workspace"], str(other))
        self.assert_code("invalid_actor", self.platform.context_for, original)
        self.platform.deactivate("b")
        self.platform.activate("a")
        current = self.platform.bind("a", "reviewer")
        context = self.platform.context_for(current)
        self.assertEqual(context["workspace"], str(self.review))
        self.assertEqual(context["generation"], 2)
        self.assert_code("invalid_actor", self.platform.context_for, original)
        self.assert_code("invalid_actor", self.platform.context_for, actor_b)
        self.assert_code("stale_task", self.platform.claim, current, task["task_id"], "text")

    def test_bindings_do_not_mutate_package_or_enable_runtime_file_tools(self):
        before = self.package.read_bytes()
        self.install(role_deployments=self.mapping())
        self.assertEqual(self.package.read_bytes(), before)
        view = self.platform.view("a")
        self.assertNotIn(str(self.root), json.dumps(view["package"]))
        self.platform.activate("a")
        context = self.platform.context("a", "editor")
        prompt = build_prompt(context, {"text": "synthetic task"}, "text")
        self.assertNotIn(str(self.edit), prompt)
        self.assertNotIn("read-write", prompt)
        self.assertFalse(CAPABILITIES["business_file_tools"])

    def test_reinstall_cannot_mutate_existing_bindings(self):
        self.install(role_deployments=self.mapping())
        before = self.platform.view("a")["role_deployments"]
        self.assert_code("installation_exists", self.install)
        self.assertEqual(self.platform.view("a")["role_deployments"], before)

    def test_missing_binding_fails_closed_instead_of_falling_back(self):
        self.install(role_deployments=self.mapping())
        with closing(sqlite3.connect(self.platform.db_path)) as db:
            db.execute("DELETE FROM role_deployments WHERE role_id='editor'")
            db.commit()
        self.assert_code("deployment_corrupt", self.platform.activate, "a")
        self.assert_code("deployment_corrupt", self.platform.deployment_workspaces)

    def test_readonly_missing_state_does_not_create_or_enumerate_workspaces(self):
        missing = self.root / "absent-state"
        readonly = Platform(missing, readonly=True)
        self.assertEqual(readonly.deployment_workspaces(), [])
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
