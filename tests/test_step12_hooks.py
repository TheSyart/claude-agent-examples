import ast
import pathlib
import unittest
from typing import Any


SOURCE = pathlib.Path(__file__).resolve().parents[1] / "build-agent-example" / "code" / "step12_hooks.py"


def load_tool_policy_parts():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    wanted = {"HookDecision", "Hook", "HookRegistry", "ToolPolicyHook"}
    nodes = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.ClassDef)
            and node.name in wanted
        )
    ]
    names = {node.name for node in nodes}
    missing = wanted - names
    if missing:
        raise AssertionError(f"missing definitions: {sorted(missing)}")

    ns = {"Any": Any}
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(SOURCE), "exec"), ns)
    return ns["ToolPolicyHook"], ns["HookDecision"], ns["HookRegistry"]


def load_registered_hook_names():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "register"
            and node.args
        ):
            continue
        hook_call = node.args[0]
        if isinstance(hook_call, ast.Call) and isinstance(hook_call.func, ast.Name):
            names.append(hook_call.func.id)
    return names


def load_initial_tool_schema_names():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    for node in tree.body:
        if not (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "TOOLS" for target in node.targets)
            and isinstance(node.value, ast.List)
        ):
            continue

        names = []
        for item in node.value.elts:
            if (
                isinstance(item, ast.Subscript)
                and isinstance(item.value, ast.Name)
                and item.value.id == "_TOOL_SCHEMAS"
                and isinstance(item.slice, ast.Constant)
            ):
                names.append(item.slice.value)
        return names
    raise AssertionError("step12_hooks.py must define initial TOOLS list")


def load_execute_main_basic_tool_names():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    func = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "execute_main_tool"
        ),
        None,
    )
    if func is None:
        raise AssertionError("step12_hooks.py must define execute_main_tool()")

    names = set()
    for node in ast.walk(func):
        if not (
            isinstance(node, ast.Compare)
            and len(node.ops) == 1
            and isinstance(node.ops[0], ast.In)
            and len(node.comparators) == 1
            and isinstance(node.comparators[0], (ast.Tuple, ast.List, ast.Set))
        ):
            continue
        for item in node.comparators[0].elts:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                names.add(item.value)
    return names


def load_is_blocking_tool_result():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    func = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "is_blocking_tool_result"
        ),
        None,
    )
    if func is None:
        raise AssertionError("step12_hooks.py must define is_blocking_tool_result()")
    ns = {}
    module = ast.Module(body=[func], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(SOURCE), "exec"), ns)
    return ns["is_blocking_tool_result"]


def top_level_function_names():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }


def source_text():
    return SOURCE.read_text(encoding="utf-8")


class Step12HooksTests(unittest.TestCase):
    def test_step12_does_not_keep_special_shell_write_helpers(self):
        names = top_level_function_names()
        removed_helpers = {
            "find_" + "command_write_target",
            "find_" + "sensitive_command_write",
        }

        self.assertTrue(names.isdisjoint(removed_helpers))

    def test_step12_registers_five_representative_hooks(self):
        self.assertEqual(
            load_registered_hook_names(),
            [
                "LoggingHook",
                "ToolPolicyHook",
                "ToolAuditHook",
                "OutputFormattingHook",
                "StopQualityGateHook",
            ],
        )

    def test_system_prompt_routes_file_writes_to_write_file(self):
        self.assertIn(
            "创建或覆盖本地文件必须调用 write_file",
            source_text(),
        )
        self.assertIn(
            "如果工具返回的实际路径与皇上原始路径不同",
            source_text(),
        )

    def test_main_agent_exposes_basic_file_tools(self):
        tool_names = load_initial_tool_schema_names()

        self.assertIn("read_file", tool_names)
        self.assertIn("write_file", tool_names)
        self.assertIn("glob", tool_names)
        self.assertIn("grep", tool_names)

    def test_main_tool_dispatches_basic_file_tools(self):
        dispatched = load_execute_main_basic_tool_names()

        self.assertIn("read_file", dispatched)
        self.assertIn("write_file", dispatched)
        self.assertIn("glob", dispatched)
        self.assertIn("grep", dispatched)

    def test_tool_policy_blocks_write_file_to_sensitive_file(self):
        ToolPolicyHook, HookDecision, _ = load_tool_policy_parts()

        decision = ToolPolicyHook().before_tool_call(
            {
                "name": "write_file",
                "input": {"path": ".env", "content": "API_KEY=123"},
            }
        )

        self.assertIsInstance(decision, HookDecision)
        self.assertEqual(decision.action, "deny")
        self.assertIn(".env", decision.reason)

    def test_tool_policy_rewrites_write_file_demo_production_path(self):
        ToolPolicyHook, HookDecision, _ = load_tool_policy_parts()

        decision = ToolPolicyHook().before_tool_call(
            {
                "name": "write_file",
                "input": {"path": "demo_production/report.txt", "content": "ok"},
            }
        )

        self.assertIsInstance(decision, HookDecision)
        self.assertEqual(decision.action, "allow")
        self.assertEqual(
            decision.updated_input["path"],
            "sandbox/demo_production/report.txt",
        )

    def test_tool_policy_asks_for_high_sensitivity_run_command(self):
        ToolPolicyHook, HookDecision, _ = load_tool_policy_parts()

        decision = ToolPolicyHook().before_tool_call(
            {
                "name": "run_command",
                "input": {"command": 'git commit --dry-run -m "hook permission test"'},
            }
        )

        self.assertIsInstance(decision, HookDecision)
        self.assertEqual(decision.action, "ask")
        self.assertIn("提交代码变更", decision.reason)

    def test_tool_policy_allows_plain_run_command(self):
        ToolPolicyHook, _, _ = load_tool_policy_parts()

        decision = ToolPolicyHook().before_tool_call(
            {
                "name": "run_command",
                "input": {"command": "echo hello"},
            }
        )

        self.assertIsNone(decision)

    def test_registry_marks_updated_input_reason_on_ctx(self):
        ToolPolicyHook, _, HookRegistry = load_tool_policy_parts()
        registry = HookRegistry()
        registry.register(ToolPolicyHook())
        ctx = {
            "name": "write_file",
            "input": {"path": "demo_production/report.txt", "content": "ok"},
        }

        decision = registry.emit("before_tool_call", ctx, tool_matcher="write_file")

        self.assertIsNone(decision)
        self.assertEqual(ctx["input"]["path"], "sandbox/demo_production/report.txt")
        self.assertIn("_hook_updated_reason", ctx)

    def test_hook_denial_tool_result_is_blocking(self):
        is_blocking_tool_result = load_is_blocking_tool_result()

        self.assertTrue(is_blocking_tool_result("[HookDecision: 拒绝] 敏感文件写入已拦截"))
        self.assertTrue(is_blocking_tool_result("[HookDecision: 需要确认] 需要确认"))
        self.assertFalse(is_blocking_tool_result("normal output"))


if __name__ == "__main__":
    unittest.main()
