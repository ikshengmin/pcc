"""Source class semantics only: no claim to allocate, initialize or run objects."""
import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, analyze_class_sources, compile_source
from pcc.frontends.java.source_class_model import ReceiverType
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner


VALID = [
    ("empty-default", "class Record{}"),
    ("public-default", "public class Record{}"),
    ("primitive-fields", "class Record{boolean b;byte v;short s;char c;int i;long j;}"),
    ("explicit-constructor", "class Record{int value;Record(int n){this.value=n;}int get(){return value;}}"),
    ("implicit-receiver", "class Record{int value;Record(int n){value=n;}int get(){return this.value;}}"),
    ("parameter-shadows-field", "class Record{int value;Record(int value){this.value=value;}int add(int value){return this.value+value;}}"),
    ("instance-field-initializers", "class Record{int first=7;int next=first+35;int get(){return next;}}"),
    ("explicit-forward-default-read", "class Record{int first=this.next+7;int next=35;int get(){return first;}}"),
    ("explicit-self-default-read", "class Record{int value=this.value+42;int get(){return value;}}"),
    ("constructor-chain", "class Record{int value;Record(){this(42);}Record(int n){this.value=n;}int get(){return value;}}"),
    ("constructor-widening", "class Record{long value;Record(){this(42);}Record(long n){value=n;}long get(){return value;}}"),
    ("constructor-most-specific", "class Record{Record(){this(42);}Record(int n){}Record(long n){}}"),
    ("explicit-super", "class Record{int value;Record(){super();value=42;}}"),
    ("constructor-static-argument", "class Record{Record(){this(seed());}Record(int n){}static int seed(){return 42;}}"),
    ("constructor-parameter-argument", "class Record{Record(int n){this((long)n);}Record(long n){}}"),
    ("constructor-return", "class Record{Record(){return;}}"),
    ("instance-call", "class Record{int value;int add(int n){return n+value;}int get(){return add(42);}}"),
    ("explicit-receiver-call", "class Record{int add(int n){return n+1;}int get(){return this.add(41);}}"),
    ("receiver-call-statement", "class Record{int value;void clear(){value=0;}int get(){this.clear();return value;}}"),
    ("static-call-from-instance", "class Record{static int add(int n){return n+1;}int get(){return add(41);}}"),
    ("static-constant-from-instance", "class Record{static final int K=42;int get(){return K;}}"),
    ("static-constant-through-this", "class Record{static final int K=42;int get(){return this.K;}}"),
    ("constant-name-narrowing", "class Record{static final int K=42;byte first=K;byte next=Record.K;}"),
    ("primary-constant-explicit-cast", "class Record{static final int K=42;byte value=(byte)this.K;}"),
    ("primary-false-is-not-constant-loop", "class Record{static final boolean NEVER=false;void work(){while(this.NEVER){}}}"),
    ("instance-primitive-compounds", "class Record{byte b;short s;char c;int i;long j;boolean flag;void change(){b+=257;s-=1;c++;i*=3;j<<=65;flag^=true;}}"),
    ("constructor-structured-flow", "class Record{int value;Record(int n){do{value+=n;n--;}while(n>0);switch(n){case 0:value+=1;break;default:value=7;}}}"),
    ("uncalled-instance-body", "class Record{static int run(){return 42;}int other(int n){return n+7;}}"),
    ("method-same-name-as-class", "class Record{Record(){}int Record(){return 42;}}"),
    ("private-constructor", "class Record{private Record(){}static int run(){return 42;}}"),
    ("empty-instance-initializer", "class Record{{}}"),
    ("ordered-fields-and-blocks", "class Record{int a=7;{a+=1;}int b=a+34;{b+=1;}Record(){a+=2;}}"),
    ("initializer-local-scopes", "class Record{int value;{int n=7;value=n;}{int n=35;value+=n;}}"),
    ("initializer-forward-assignment", "class Record{{value=42;}int value;}"),
    ("initializer-qualified-forward-compound", "class Record{{this.value+=42;}int value;}"),
    ("initializer-qualified-forward-increment", "class Record{{this.value++;}int value;}"),
    ("initializer-prior-field-increment", "class Record{int value;{value++;}}"),
    ("initializer-indirect-forward-read", "class Record{int first;{first=get();}int later=42;int get(){return later;}}"),
    ("initializer-constructor-delegation", "class Record{int value=7;{value+=35;}Record(){this(1);}Record(int n){value+=n;}}"),
    ("initializer-nested-control", "class Record{int value;{do{value++;}while(value<3);switch(value){case 3:value=42;break;default:value=7;}}}"),
]


@pytest.mark.parametrize("name,text", VALID, ids=[row[0] for row in VALID])
def test_original_class_source_builds_typed_model_without_any_backend(name, text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "lower", lambda *a: pytest.fail("class model invoked native lowering"))
    original = {"Record.java": text}
    project = analyze_class_sources(original)
    assert original == {"Record.java": text}
    model = project.models["Record"]
    assert model.source is project.classes["Record"]
    assert model.superclass == "java/lang/Object" and not model.runtime_registered
    assert model.constructors and all(c.constructor_target is not None for c in model.constructors)
    assert all(m.owner == "Record" for m in model.source.methods)


def test_primitive_payload_plan_is_not_a_python_header_or_runtime_object():
    project = analyze_class_sources({"Record.java": VALID[2][1]})
    model = project.models["Record"]
    assert [(x.field.token.text, x.offset, x.size, x.alignment) for x in model.fields] == [
        ("b", 0, 1, 1), ("v", 1, 1, 1), ("s", 2, 2, 2),
        ("c", 4, 2, 2), ("i", 8, 4, 4), ("j", 16, 8, 8)]
    assert (model.payload_size, model.payload_alignment) == (24, 8)
    assert not hasattr(model, "address") and not model.runtime_registered


def test_constructor_identity_and_most_specific_chain_are_retained():
    model = analyze_class_sources({"Record.java": VALID[11][1]}).models["Record"]
    constructors = {c.descriptor: c for c in model.constructors}
    assert constructors["()V"].constructor_target is constructors["(I)V"]
    assert constructors["(I)V"].constructor_target == "java/lang/Object::<init>()V"
    assert constructors["(J)V"].constructor_target == "java/lang/Object::<init>()V"
    assert all(not c.implicit for c in constructors.values())


def test_default_constructor_access_and_identity_are_class_owned():
    project = analyze_class_sources({"A.java": "public class A{}", "B.java": "class B{}"})
    a, b = project.models["A"].constructors[0], project.models["B"].constructors[0]
    assert a.implicit and b.implicit and a.owner == "A" and b.owner == "B"
    assert a is not b and a.modifiers == frozenset({"public"}) and not b.modifiers


def test_receiver_field_and_parameter_bindings_do_not_alias():
    model = analyze_class_sources({"Record.java": VALID[5][1]}).models["Record"]
    constructor = model.constructors[0]
    assignment = constructor.body.children[0]
    assert assignment.kind == "field_assign" and assignment.symbol is model.fields[0].field
    assert assignment.children[0].symbol is constructor.locals[0]
    expression = model.source.methods[0].body.children[0].children[0]
    assert expression.children[0].kind == "instance_field"
    assert expression.children[0].symbol is model.fields[0].field
    assert expression.children[1].symbol is model.source.methods[0].locals[0]


def test_imported_static_constants_use_their_actual_owner():
    project = analyze_class_sources({
        "One.java": "package p;import q.Two;public class One{static final int K=7;int value=Two.K;int get(){return value+K;}}",
        "Two.java": "package q;public class Two{public static final int K=35;}"})
    initializer = project.models["p/One"].fields[0].field.initializer
    assert initializer.constant == 35 and initializer.symbol.owner == "q/Two"


def test_primary_static_field_access_is_not_a_constant_expression():
    model = analyze_class_sources({"Record.java": "class Record{static final int K=42;int get(){return this.K;}}"}).models["Record"]
    access = model.source.methods[0].body.children[0].children[0]
    assert access.kind == "receiver_static_field" and access.constant is None
    assert access.dtype == "int" and access.symbol.constant == 42


def test_constructor_initialization_plan_keeps_source_order_and_does_not_duplicate_this_delegation():
    text = "class Record{int a=7;{a+=1;}int b=a+34;{b+=1;}Record(){this(1);}Record(int n){a+=n;}}"
    model = analyze_class_sources({"Record.java": text}).models["Record"]
    a, b = [slot.field for slot in model.fields]
    first, second = model.source.instance_initializers
    assert model.initialization_steps == (a, first, b, second)
    constructors = {c.descriptor: c for c in model.constructors}
    assert constructors["()V"].initialization_steps == ()
    assert constructors["()V"].constructor_target is constructors["(I)V"]
    assert constructors["(I)V"].initialization_steps == model.initialization_steps
    assert first.symbol.body is first and second.symbol.body is second
    assert not model.runtime_registered


INVALID = [
    # Exact original accepted by the old model but rejected by the JDK lane.
    ("class Record{{value+=42;}int value;}", "forward reference"),
    ("class Record{{value<<=1;}int value;}", "forward reference"),
    ("class Record{{flag|=true;}boolean flag;}", "forward reference"),
    ("class Record{static final int K=42;byte b=this.K;}", "incompatible assignment"),
    ("class Record{static final int K=42;int run(){switch(42){case this.K:return 1;default:return 0;}}}", "constant"),
    ("class Record{static final boolean YES=true;int run(){while(this.YES){}}}", "missing return"),
    ("class Record{static final boolean YES=true;int run(){int x;if(this.YES)x=42;return x;}}", "not definitely assigned"),
    ("class Record{Record(){}Record(){}}", "duplicate constructor"),
    ("class Record{static Record(){}}", "invalid constructor modifier"),
    ("class Record{final Record(){}}", "invalid constructor modifier"),
    ("class Record{int get(){return 1;}static int get(){return 2;}}", "duplicate method signature"),
    ("class Record{int value;int value;}", "duplicate field"),
    ("class Record{int first=next;int next=42;}", "forward or self"),
    ("class Record{int value=value+1;}", "forward or self"),
    ("class Record{static final int K=K+1;}", "forward or self"),
    ("class Record{int value=true;}", "integer value required"),
    ("class Record{int value;static int get(){return value;}}", "static context"),
    ("class Record{int value;static int get(){return this.value;}}", "static context"),
    ("class Record{int value;static void set(){value=42;}}", "static context"),
    ("class Record{int value;int get(){return Record.value;}}", "class qualifier"),
    ("class Record{int get(){return 42;}static int run(){return get();}}", "static context"),
    ("class Record{int get(){return 42;}int run(){return Record.get();}}", "class qualifier"),
    ("class Record{int get(){return this.missing;}}", "undefined instance field"),
    ("class Record{void set(){this.missing=42;}}", "undefined instance field"),
    ("class Record{int get(){return this.missing();}}", "unresolved receiver method"),
    ("class Record{Record(){this();}}", "recursive constructor"),
    ("class Record{Record(){this(1);}Record(int n){this();}}", "recursive constructor"),
    ("class Record{Record(){this(true);}}", "no applicable"),
    ("class Record{Record(){super(1);}}", "no-argument constructor"),
    ("class Record{Record(){int n=1;super();}}", "must be first"),
    ("class Record{Record(){if(true)super();}}", "must be first"),
    ("class Record{void method(){super();}}", "must be first"),
    ("class Record{int value;Record(){this(value);}Record(int n){}}", "before constructor invocation"),
    ("class Record{Record(){this(this.get());}Record(int n){}int get(){return 42;}}", "before constructor invocation"),
    ("class Record{Record(){return 42;}}", "void method"),
    ("class Record{int unused(){int n;return n;}}", "not definitely assigned"),
    ("class Record{Record(){int n;int value=n;}}", "not definitely assigned"),
    ("class Record{int value;void set(){this.value=true;}}", "integer value required"),
    ("class Record{boolean value;void set(){value++;}}", "integer field"),
    ("class Record{byte value;void set(){value=128;}}", "incompatible assignment"),
    ("class Record{{int n=value;}int value;}", "forward reference"),
    ("class Record{{value=value+1;}int value;}", "forward reference"),
    ("class Record{{value++;}int value;}", "forward reference"),
    ("class Record{{return;}}", "return is not permitted"),
    ("class Record{{if(false)return;}}", "return is not permitted"),
    ("class Record{{while(true){}}}", "must be able to complete normally"),
    ("class Record{{break;}}", "enclosing loop"),
    ("class Record{{super();}}", "must be first"),
    ("class Record{{int n;int value=n;}}", "not definitely assigned"),
]


@pytest.mark.parametrize("text,message", INVALID)
def test_invalid_original_class_model_is_not_accepted(text, message):
    with pytest.raises(JavaError, match=message):
        analyze_class_sources({"Record.java": text})


@pytest.mark.parametrize("text,message", [
    ("class Record{final int value=42;}", "definite-unassignment"),
    ("class Record{static int value=42;}", "class initialization"),
    ("class Record{int hashCode(){return 42;}}", "inherited-method"),
    ("class Record{int[] values;}", "array fields"),
    ("class Record{int get(){return new Record();}}", "unsupported Java expression"),
    ("class Record extends Other{}", "inheritance"),
    ("class Record{static {}}", "static initializer execution"),
])
def test_unimplemented_class_semantics_remain_explicit(text, message):
    with pytest.raises(UnsupportedJava, match=message):
        analyze_class_sources({"Record.java": text})


@pytest.mark.parametrize("text", [dict(VALID)[name] for name in (
    "explicit-constructor", "uncalled-instance-body", "private-constructor")])
def test_model_does_not_grant_native_object_compilation(text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *a: pytest.fail("unqualified runtime-bearing class reached IR"))
    with pytest.raises(UnsupportedJava, match="static source methods"):
        compile_source(text)
