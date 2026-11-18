"""Original declaration registration is not a substitute for a native test run."""
import hashlib

import pytest

from scripts import ci_go_math_bits_project as proj
from scripts.go_package_test_entry import build_entry


def build(body):
    return build_entry("m/p", [("export_test.go", "package p\n")], [
        ("p_test.go", 'package p_test\nimport t "testing"\n' + body),
    ])


def test_registered_methods_outputs_and_real_mainstart():
    src, found = build('''
type Rec struct {}
func (r Rec) TestMethod(t *t.T) {}
func Testable(t *t.T) {}
func TestOK(t *t.T) {}
func BenchmarkOne(b *t.B) {}
func FuzzX(f *t.F) {}
func TestMain(t *t.T) {}
func ExampleP() {
    println(`// Output: not the marker
}`)
    // Unordered output:
    // two
    // one
}
func ExampleEmpty() {
    // Output:
}
''')
    assert [x[0] for x in found["tests"]] == ["TestOK", "TestMain"]
    assert [x[0] for x in found["benchmarks"]] == ["BenchmarkOne"]
    assert [x[0] for x in found["fuzzTargets"]] == ["FuzzX"]
    assert found["examples"] == [("ExampleP", "_xtest", "two\none\n", True), ("ExampleEmpty", "_xtest", "", False)]
    assert '_ "m/p"' in src and '_xtest "m/p_test"' in src
    assert "testing.MainStart(testdeps.TestDeps{}, tests, benchmarks, fuzzTargets, examples)" in src
    assert "os.Exit(m.Run())" in src
    assert "func Test" not in src and "func Example" not in src


@pytest.mark.parametrize("body,reason", [
    ("func TestA(x int) {}", "invalid testing parameter"),
    ("func TestMain(m *t.M) {}", "custom TestMain"),
    ("func TestΩ(t *t.T) {}", "non-ASCII"),
    ("func ExampleDir() {\n // Output:\n //foo:bar\n}\n", "directive"),
    ("func ExampleDir() {\n // Output:\n //extern x\n}\n", "directive"),
    ("func TestA(t *t.T) int { return 1 }", "signature"),
    ("func TestA(t *t.T) {}\nfunc TestA(t *t.T) {}", "duplicate"),
    ('func ExampleRaw() {\n println(`// Output: fake`)\n}\n', "example output"),
    ('func ExampleRaw() {\n // Output: not trailing\n println("later")\n}\n', "example output"),
])
def test_outside_supported_entry_fails_closed(body, reason):
    with pytest.raises(ValueError, match=reason):
        build(body)


@pytest.mark.parametrize("mutation", ("ok", "output", "exit", "path", "owner-changed", "symlink"))
def test_independent_go_tokens_and_ownership(tmp_path, mutation):
    src, _ = build('func ExampleX() {\n // Output: expected\n}\n')
    cache = tmp_path / "gocache"
    cache.mkdir()
    a = tmp_path / "owned-testmain.go"
    a.write_text(src)
    d = hashlib.sha256(a.read_bytes()).hexdigest()
    source = cache / "official-testmain"
    if mutation == "path":
        source = tmp_path / "not-from-cache.go"
    text = src
    if mutation == "output":
        text = text.replace("expected", "forged")
    if mutation == "exit":
        text = text.replace("m.Run()", "0")
    if mutation == "symlink":
        source.symlink_to(a)
    else:
        source.write_text(text + "\n// formatting does not affect complete Go token comparison\n")
    if mutation == "owner-changed":
        a.write_text(text+"\n")
    graph = [{"Name": "main", "ImportPath": "math/bits.test", "GoFiles": [str(source)]}]
    if mutation == "ok":
        value = proj.reference_test_entry(graph, cache, a, d)
        assert value["status"] == "PASS" and value["owned_sha256"] == d
        assert value["official_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    else:
        with pytest.raises(ValueError):
            proj.reference_test_entry(graph, cache, a, d)
