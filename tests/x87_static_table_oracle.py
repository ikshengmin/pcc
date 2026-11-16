"""Fail-closed checks for actual x87 table population and static literal ownership.

This is an IR contract, not native execution. The surrounding strict gate still
verifies the complete module and emits/inspects the real target object.
"""
import ast
import re

from tests.x86_64_x87_source import X87_CASES

_SUFFIX = 'pcc_backend_x86_64_encode'
_REF = r'[%@][\w.$]+'


def require_x87_static_tables(source, section, initialization):
    expected = {'_X87_FIXED': dict(X87_CASES[:10]),
                '_X87_STACK': {tuple(key.split(' ', 1)): value
                               for key, value in X87_CASES[10:]}}
    actual = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in expected:
                assert target.id not in actual, 'duplicate source table'
                actual[target.id] = ast.literal_eval(node.value)
    assert actual == expected, 'production x87 keys/opcodes changed'
    # Exact current IR grammar. Unknown forms fail closed rather than allowing
    # an unrelated constructor call to stand in for the actual table value.
    globals_ = {}
    for match in re.finditer(r'^(@(?:\.py(?:str|bytes)\.obj\.|\.pcc\.static\.tuple\.|\.modvar\.)[\w.$]+) = ([^\n]*)$', section, re.M):
        assert match[1] not in globals_, 'duplicate global'
        globals_[match[1]] = match[2]
    literal_init = '_pcc_py_static_literals_' + _SUFFIX
    registrations = list(re.finditer(r'^define[^\n]*@' + literal_init
                                    + r'\([^\n]*\)[^\n]*\{\n(.*?)^}', section, re.M | re.S))
    assert len(registrations) == 1, 'missing/duplicate literal registration body'
    registration = registrations[0][1]
    init_call = re.search(r'\bcall void (?:\(\) )?@' + literal_init + r'\(\)', initialization)
    assert init_call, 'unreached literal registration body'

    def literal(symbol, tag):
        line = globals_.get(symbol, '')
        tail = r', i64 -1, i64 -1' if tag == 4 else ''
        fields = 'i64, i32, i32, i64' + (', i64, i64' if tag == 4 else '')
        match = re.fullmatch(r'internal global \{ ' + fields + r', \[(\d+) x i8\] \} '
                             r'\{ i64 1, i32 ' + str(tag) + r', i32 1, i64 (\d+)'
                             + tail + r', \[(\d+) x i8\] c"((?:\\[0-9A-Fa-f]{2})*)" \}', line)
        assert match, 'invalid static literal header: ' + symbol
        raw = bytes.fromhex(match[4].replace('\\', ''))
        assert len(raw) == int(match[1]) == int(match[3]) == int(match[2]) + 1
        assert raw[-1:] == b'\0', 'missing literal NUL'
        # The normal owned program registers process-lifetime literal owners.
        # Do not accept an unregistered global or a call outside this init.
        calls = re.findall(r'\bcall i64 (?:\(ptr\) )?@pcc_gc_pointer_register_static_literal\(ptr '
                           + re.escape(symbol) + r'\)', registration)
        assert len(calls) == 1, 'literal registration missing/duplicate: ' + symbol
        return raw[:-1].decode('utf-8') if tag == 4 else raw[:-1]

    def key_value(ref):
        if ref.startswith('@'):
            return literal(ref, 4)
        match = re.search(r'^  ' + re.escape(ref) + r' = call ptr (?:\(ptr, i64\) )?'
                          r'@py_tuple_from_static_items\(ptr (' + _REF + r'), i64 2\)$', initialization, re.M)
        assert match, 'unknown tuple key construction'
        gep = re.search(r'^  ' + re.escape(match[1]) + r' = getelementptr inbounds \[2 x ptr\], ptr '
                        r'(@[\w.$]+), i32 0, i32 0$', initialization, re.M)
        assert gep, 'missing tuple key storage'
        refs = re.fullmatch(r'internal constant \[2 x ptr\] \[ptr bitcast \(ptr (@[\w.$]+) to ptr\), '
                            r'ptr bitcast \(ptr (@[\w.$]+) to ptr\)\]', globals_.get(gep[1], ''))
        assert refs, 'invalid tuple key array'
        return (literal(refs[1], 4), literal(refs[2], 4))

    for table, wanted in expected.items():
        symbol = '@.modvar.' + _SUFFIX + '.' + table
        assert re.fullmatch(r'(?:internal )?global ptr null', globals_.get(symbol, ''))
        stores = list(re.finditer(r'@pcc_gc_store_root\(ptr ' + re.escape(symbol)
                                 + r', ptr (' + _REF + r')\)', initialization))
        assert len(stores) == 1, 'table not initialized exactly once'
        dictionary = stores[0][1]
        creates = list(re.finditer(re.escape(dictionary) + r' = call ptr (?:\(i64\) )?'
                                  r'@py_dict_new_presized\(i64 ' + str(len(wanted)) + r'\)', initialization))
        assert len(creates) == 1 and init_call.end() < creates[0].start() < stores[0].start()
        inserts = list(re.finditer(r'@py_dict_set\(ptr ' + re.escape(dictionary)
                                  + r', ptr (' + _REF + r'), ptr (' + _REF + r')\)', initialization))
        assert len(inserts) == len(wanted), 'missing/extra table entries'
        observed = {}
        for insert in inserts:
            assert creates[0].end() < insert.start() < stores[0].start(), 'write outside initialization'
            key = key_value(insert[1])
            assert key not in observed, 'duplicate table key'
            observed[key] = literal(insert[2], 17)
        assert observed == wanted, 'incorrect table keys/opcodes'
        flags = list(re.finditer(r'store i1 1, ptr ' + re.escape(symbol) + r'_initialized\b', initialization))
        assert len(flags) == 1 and stores[0].end() < flags[0].start(), 'invalid initialization flag'
        publications = list(re.finditer(r'@py_module_attr_set\([^\n]*@\.pyattr\.' + table
                                       + r'(?=,)[^\n]*ptr ' + re.escape(dictionary) + r'\)', initialization))
        assert len(publications) == 1 and flags[0].end() < publications[0].start(), 'table not published after initialization'
