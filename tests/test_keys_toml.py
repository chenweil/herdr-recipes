"""解析 config/keys.toml 并断言 prefix 绑定的语义。

每个测试都是合同：改了 keys.toml 必须同步更新这里。
"""

import pathlib
import unittest

try:
    import tomllib
except ImportError:  # Python < 3.11
    raise unittest.SkipTest("keys.toml tests need Python 3.11+ (tomllib)")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
KEYS_TOML = REPO_ROOT / "config" / "keys.toml"

# hopen.sh 布局代号按 prefix+alt/ctrl+1..9 的键位顺序排列
LAYOUT_CODES_BY_INDEX = [
    "12",   # 1
    "21",   # 2
    "22",   # 3
    "13",   # 4
    "31",   # 5
    "111",  # 6
    "11",   # 7
    "221",  # 8
    "122",  # 9
]


def _load_bindings():
    """Return a list of (key, command) tuples in source order.

    The TOML shape is [[keys.command]] blocks, each with `key` and `command`
    string fields. We keep source order so callers can detect duplicates.
    """
    with KEYS_TOML.open("rb") as fh:
        parsed = tomllib.load(fh)
    bindings = parsed.get("keys", {}).get("command", [])
    return [(b["key"], b["command"]) for b in bindings]


def _binding_map(bindings):
    return {k: cmd for k, cmd in bindings}

def _token_after(cmd, marker):
    """Return the token that follows `marker` in cmd, or None if absent.

    Substring containment is unsafe for layout codes: `12` is a substring
    of `122`, `22` of `221`, `11` of `111`. Pinning the exact token keeps
    the test honest when someone accidentally swaps two layout codes.
    """
    idx = cmd.find(marker)
    if idx == -1:
        return None
    rest = cmd[idx + len(marker):].lstrip()
    if not rest:
        return None
    return rest.split()[0]


class ManagedKeyBindingsTests(unittest.TestCase):
    """合同：keys.toml 中 prefix 绑定的完整映射必须可被解析。"""

    @classmethod
    def setUpClass(cls):
        cls.bindings = _load_bindings()
        cls.by_key = _binding_map(cls.bindings)

    def test_keys_toml_is_parsable_as_toml(self):
        """最起码，keys.toml 必须是合法 TOML，且 [[keys.command]] 列表非空。"""
        self.assertGreater(
            len(self.bindings), 0,
            "no [[keys.command]] blocks parsed from keys.toml",
        )

    def test_no_duplicate_keys_in_keys_toml(self):
        """同一 key 出现两次会导致 herdr 启动失败；必须唯一。"""
        seen = {}
        for key, _ in self.bindings:
            self.assertNotIn(key, seen, f"duplicate key {key!r} in keys.toml")
            seen[key] = True

    def test_prefix_alt_1_to_9_map_to_nine_layouts(self):
        """prefix+alt+1..9 依次打开 12 21 22 13 31 111 11 221 122。"""
        for n, code in enumerate(LAYOUT_CODES_BY_INDEX, start=1):
            with self.subTest(n=n):
                key = f"prefix+alt+{n}"
                self.assertIn(key, self.by_key, f"missing binding: {key}")
                cmd = self.by_key[key]
                self.assertEqual(
                    _token_after(cmd, "hopen.sh"), code,
                    f"{key} should invoke hopen.sh {code}, got: {cmd}",
                )
                # alt 路径不应带 --no-agents
                self.assertNotIn("--no-agents", cmd,
                                 f"{key} (alt = with agents) should not use --no-agents")

    def test_prefix_ctrl_1_to_9_use_same_codes_with_no_agents(self):
        """prefix+ctrl+1..9 与 alt 同样代号并加 --no-agents。"""
        for n, code in enumerate(LAYOUT_CODES_BY_INDEX, start=1):
            with self.subTest(n=n):
                key = f"prefix+ctrl+{n}"
                self.assertIn(key, self.by_key, f"missing binding: {key}")
                cmd = self.by_key[key]
                self.assertEqual(
                    _token_after(cmd, "hopen.sh"), code,
                    f"{key} should invoke hopen.sh {code}, got: {cmd}",
                )
                self.assertIn("--no-agents", cmd,
                              f"{key} (ctrl = bare) should pass --no-agents")

    def test_prefix_1_to_6_route_through_pane_switch_script(self):
        """prefix+1..6 走 herdr-pane-switch.py。"""
        for n in range(1, 7):
            with self.subTest(n=n):
                key = f"prefix+{n}"
                self.assertIn(key, self.by_key, f"missing binding: {key}")
                cmd = self.by_key[key]
                self.assertIn("herdr-pane-switch.py", cmd,
                              f"{key} should call herdr-pane-switch.py")
                # The pane index must be the last token; assertIn would
                # false-pass for n=3 because "python3" contains "3".
                self.assertEqual(
                    cmd.split()[-1], str(n),
                    f"{key} should pass pane index {n} as last token, got: {cmd}",
                )

    def test_no_unexpected_alt_or_ctrl_bindings_outside_1_to_9(self):
        """alt/ctrl 只在 1..9 内；超出范围的 prefix+alt+0 或 +10 不应存在。"""
        for n in (0, 10):
            for mod in ("alt", "ctrl"):
                key = f"prefix+{mod}+{n}"
                self.assertNotIn(key, self.by_key,
                                 f"{key} should not be a binding")

    def test_header_documents_alt_ctrl_range_as_1_to_9(self):
        """keys.toml 头部注释必须说明 alt/ctrl 范围是 1..9，否则 install 升级时
        用户的旧手工 +8/+9 不会被清掉。"""
        head = KEYS_TOML.read_text().split("\n", 20)[:20]
        joined = "\n".join(head)
        self.assertIn(
            "1..9", joined,
            "keys.toml header should document alt/ctrl range as 1..9",
        )
        self.assertNotIn(
            "1..7", joined,
            "keys.toml header should not still say 1..7 after #8",
        )


if __name__ == "__main__":
    unittest.main()
