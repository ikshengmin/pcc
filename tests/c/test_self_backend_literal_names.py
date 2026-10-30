"""SSA identifiers must remain distinct from similarly spelled constants."""
import os
import platform
import subprocess

import pytest

from pcc.backend.self_backend_analysis import is_local_value_ref
from pcc.backend.self_backend_parse import decode_ssa_name, decode_value_token


@pytest.mark.parametrize("name", ["null", "poison", "undef", "false", "true", "zeroinitializer"])
@pytest.mark.parametrize("quoted", [False, True])
def test_literal_spelled_ssa_names_keep_their_reference_namespace(name, quoted):
    token = '%"' + name + '"' if quoted else '%' + name
    assert decode_ssa_name(token) == '%' + name
    assert decode_value_token(token) == '%' + name
    assert decode_value_token(name) == name
    assert is_local_value_ref(decode_value_token(token))
    assert not is_local_value_ref(decode_value_token(name))


def test_native_null_named_condition_preserves_both_edges(tmp_path):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        pytest.skip("native execution targets Darwin AArch64")
    from pcc.backend.macho_exec import link_executable
    from pcc.backend.owned_object_emit import emit_owned_object

    source = '''target triple = "arm64-apple-darwin"
define i32 @choose(ptr %input) {
entry:
  %null = icmp eq ptr %input, null
  br i1 %null, label %empty, label %present
empty:
  ret i32 1
present:
  ret i32 2
}
define i32 @main() {
entry:
  %first = call i32 @choose(ptr null)
  %second = call i32 @choose(ptr inttoptr (i64 17 to ptr))
  %total = add i32 %first, %second
  %status = sub i32 %total, 3
  ret i32 %status
}
'''
    binary = tmp_path / "ssa_literal_name"
    binary.write_bytes(link_executable([emit_owned_object(source, "arm64-apple-darwin")]))
    binary.chmod(0o755)
    result = subprocess.run([str(binary)], capture_output=True, timeout=10,
                            env=dict(os.environ, PATH="/nonexistent"))
    assert (result.returncode, result.stdout, result.stderr) == (0, b"", b"")
