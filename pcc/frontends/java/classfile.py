"""Owned, bounded Java class-file reader (JVMS 25, sections 4.1--4.7).

Implemented independently from the public specification, not a JVM library.
Reading a class is not a claim that all of its methods can be compiled.
"""
from __future__ import annotations

from dataclasses import dataclass


class JavaError(ValueError):
    """Invalid input or an explicitly unsupported Java AOT capability."""


class UnsupportedJava(JavaError):
    """A valid Java feature outside the current closed-world AOT slice."""


class Reader:
    def __init__(self, data: bytes, context: str = "class"):
        self.data = data
        self.pos = 0
        self.context = context

    def take(self, size: int) -> bytes:
        if size < 0 or size > len(self.data) - self.pos:
            raise JavaError(f"{self.context}: truncated input at byte {self.pos}")
        result = self.data[self.pos:self.pos + size]
        self.pos += size
        return result

    def uint(self, size: int) -> int:
        return int.from_bytes(self.take(size), "big")

    def u1(self) -> int:
        return self.uint(1)

    def u2(self) -> int:
        return self.uint(2)

    def u4(self) -> int:
        return self.uint(4)

    def end(self) -> None:
        if self.pos != len(self.data):
            raise JavaError(f"{self.context}: trailing bytes at byte {self.pos}")


def modified_utf8(data: bytes) -> str:
    units = []
    reader = Reader(data, "CONSTANT_Utf8")
    while reader.pos < len(data):
        first = reader.u1()
        if 0 < first < 128:
            value = first
        elif 0xC0 <= first <= 0xDF:
            second = reader.u1()
            if second & 0xC0 != 0x80:
                raise JavaError("invalid modified UTF-8 continuation")
            value = ((first & 31) << 6) | (second & 63)
            if value < 128 and not (first == 0xC0 and second == 0x80):
                raise JavaError("overlong modified UTF-8 sequence")
        elif 0xE0 <= first <= 0xEF:
            second, third = reader.u1(), reader.u1()
            if second & 0xC0 != 0x80 or third & 0xC0 != 0x80:
                raise JavaError("invalid modified UTF-8 continuation")
            value = ((first & 15) << 12) | ((second & 63) << 6) | (third & 63)
            if value < 2048:
                raise JavaError("overlong modified UTF-8 sequence")
        else:
            raise JavaError("invalid modified UTF-8 leading byte")
        units.append(value.to_bytes(2, "big"))
    return b"".join(units).decode("utf-16-be", "surrogatepass")


def _name(name: str, *, method: bool = False) -> None:
    if method and name in ("<init>", "<clinit>"):
        return
    forbidden = ".;[/" + ("<>" if method else "")
    if not name or any(c in forbidden or c == "\x00" for c in name):
        raise JavaError(f"invalid member name {name!r}")


def _class_name(name: str) -> None:
    if name.startswith("["):
        descriptor(name, method=False)
        return
    if not name or any(not part for part in name.split("/")):
        raise JavaError("invalid class name")
    for part in name.split("/"):
        _name(part)


def descriptor(text: str, *, method: bool = True) -> tuple[tuple[str, ...], str]:
    """Parse full JVM descriptors; admission to the int slice happens later."""
    def field(pos: int, allow_void: bool = False) -> tuple[str, int]:
        start = pos
        while pos < len(text) and text[pos] == "[":
            pos += 1
        dimensions = pos - start
        if dimensions > 255 or pos >= len(text):
            raise JavaError(f"invalid descriptor {text!r}")
        kind = text[pos]
        if kind == "L":
            end = text.find(";", pos)
            if end < 0 or end == pos + 1:
                raise JavaError(f"invalid descriptor {text!r}")
            if text[pos + 1] == "[":
                raise JavaError(f"invalid object descriptor {text!r}")
            _class_name(text[pos + 1:end])
            pos = end + 1
        elif kind in "BCDFIJSZ" or (kind == "V" and allow_void and not dimensions):
            pos += 1
        else:
            raise JavaError(f"invalid descriptor {text!r}")
        return text[start:pos], pos

    if not method:
        result, pos = field(0)
        if pos != len(text):
            raise JavaError(f"invalid field descriptor {text!r}")
        return (), result
    if not text.startswith("("):
        raise JavaError(f"invalid method descriptor {text!r}")
    args = []
    pos = 1
    while pos < len(text) and text[pos] != ")":
        arg, pos = field(pos)
        args.append(arg)
    if pos >= len(text):
        raise JavaError(f"invalid method descriptor {text!r}")
    result, pos = field(pos + 1, True)
    if pos != len(text) or sum(2 if a in ("J", "D") else 1 for a in args) > 255:
        raise JavaError(f"invalid method descriptor {text!r}")
    return tuple(args), result


@dataclass(frozen=True)
class Constant:
    tag: int
    values: tuple


@dataclass(frozen=True)
class Attribute:
    name: str
    data: bytes


@dataclass(frozen=True)
class Code:
    max_stack: int
    max_locals: int
    bytecode: bytes
    handlers: tuple[tuple[int, int, int, int], ...]
    attributes: tuple[Attribute, ...]


@dataclass(frozen=True)
class Method:
    flags: int
    name: str
    descriptor: str
    code: Code | None


@dataclass(frozen=True)
class ClassFile:
    major: int
    minor: int
    flags: int
    name: str
    super_name: str
    interfaces: tuple[str, ...]
    field_count: int
    constants: tuple[Constant | None, ...]
    methods: tuple[Method, ...]

    def constant(self, index: int, *tags: int) -> Constant:
        if not 0 < index < len(self.constants):
            raise JavaError(f"constant-pool index {index} out of range")
        entry = self.constants[index]
        if entry is None or (tags and entry.tag not in tags):
            raise JavaError(f"constant-pool index {index} has wrong tag (expected {tags})")
        return entry

    def utf8(self, index: int) -> str:
        return self.constant(index, 1).values[0]

    def class_name(self, index: int) -> str:
        return self.utf8(self.constant(index, 7).values[0])

    def method_ref(self, index: int) -> tuple[str, str, str]:
        entry = self.constant(index, 10)
        pair = self.constant(entry.values[1], 12)
        return self.class_name(entry.values[0]), self.utf8(pair.values[0]), self.utf8(pair.values[1])


def _attributes(reader: Reader, cls: ClassFile) -> tuple[Attribute, ...]:
    return tuple(Attribute(cls.utf8(reader.u2()), reader.take(reader.u4()))
                 for _ in range(reader.u2()))


def _code(data: bytes, cls: ClassFile) -> Code:
    reader = Reader(data, "Code")
    stack, locals_ = reader.u2(), reader.u2()
    size = reader.u4()
    if not 0 < size < 65536:
        raise JavaError("Code length must be between 1 and 65535")
    bytecode = reader.take(size)
    handlers = tuple(tuple(reader.u2() for _ in range(4)) for _ in range(reader.u2()))
    for start, end, handler, catch in handlers:
        if not 0 <= start < end <= size or not 0 <= handler < size:
            raise JavaError("invalid exception-handler range")
        if catch:
            cls.constant(catch, 7)
    attrs = _attributes(reader, cls)
    if sum(a.name == "StackMapTable" for a in attrs) > 1:
        raise JavaError("duplicate StackMapTable attribute")
    reader.end()
    return Code(stack, locals_, bytecode, handlers, attrs)


def parse_class(data: bytes) -> ClassFile:
    if len(data) > 16 * 1024 * 1024:
        raise JavaError("class exceeds the 16 MiB AOT input limit")
    reader = Reader(data)
    if reader.u4() != 0xCAFEBABE:
        raise JavaError("invalid class-file magic")
    minor, major = reader.u2(), reader.u2()
    if major < 45 or major > 69:
        raise UnsupportedJava(f"unsupported class-file version {major}.{minor}; supported majors 45..69")
    if minor == 65535:
        raise UnsupportedJava("preview class files are unsupported")
    if minor != 0 and not (major == 45 and minor <= 3):
        raise JavaError(f"invalid minor version {major}.{minor}")
    count = reader.u2()
    if count == 0:
        raise JavaError("constant_pool_count must be nonzero")
    pool = [None]
    while len(pool) < count:
        tag = reader.u1()
        if tag == 1:
            values = (modified_utf8(reader.take(reader.u2())),)
        elif tag in (3, 4):
            raw = reader.take(4)
            values = (int.from_bytes(raw, "big", signed=tag == 3),)
        elif tag in (5, 6):
            values = (reader.uint(8),)
        elif tag in (7, 8, 16, 19, 20):
            values = (reader.u2(),)
        elif tag in (9, 10, 11, 12, 17, 18):
            values = (reader.u2(), reader.u2())
        elif tag == 15:
            values = (reader.u1(), reader.u2())
        else:
            raise JavaError(f"unknown constant-pool tag {tag}")
        minimum = 55 if tag == 17 else 53 if tag in (19, 20) else 51 if tag in (15, 16, 18) else 45
        if major < minimum:
            raise JavaError(f"constant-pool tag {tag} requires class version {minimum}")
        pool.append(Constant(tag, values))
        if tag in (5, 6):
            if len(pool) >= count:
                raise JavaError("long/double constant lacks its reserved second slot")
            pool.append(None)
    cls = ClassFile(major, minor, 0, "", "", (), 0, tuple(pool), ())
    for entry in pool[1:]:
        if entry is None:
            continue
        tag, values = entry.tag, entry.values
        if tag in (7, 8, 16, 19, 20):
            value = cls.utf8(values[0])
            if tag == 7:
                _class_name(value)
            if tag == 16:
                descriptor(value)
        elif tag in (9, 10, 11):
            cls.constant(values[0], 7)
            pair = cls.constant(values[1], 12)
            name, desc = cls.utf8(pair.values[0]), cls.utf8(pair.values[1])
            _name(name, method=tag != 9)
            _, result = descriptor(desc, method=tag != 9)
            if tag != 9 and (name == "<clinit>" or (name == "<init>" and (tag == 11 or result != "V"))):
                raise JavaError("invalid special method reference")
        elif tag == 12:
            name, desc = cls.utf8(values[0]), cls.utf8(values[1])
            _name(name, method=desc.startswith("("))
            descriptor(desc, method=desc.startswith("("))
        elif tag in (17, 18):
            pair = cls.constant(values[1], 12)
            name, desc = cls.utf8(pair.values[0]), cls.utf8(pair.values[1])
            _name(name, method=True)
            if name in ("<init>", "<clinit>"):
                raise JavaError("invalid dynamic constant name")
            descriptor(desc, method=tag == 18)
        elif tag == 15:
            kind, index = values
            if not 1 <= kind <= 9:
                raise JavaError("invalid method-handle reference kind")
            tags = (9,) if kind <= 4 else (11,) if kind == 9 else (10, 11) if kind in (6, 7) and major >= 52 else (10,)
            target = cls.constant(index, *tags)
            if kind >= 5:
                name = cls.utf8(cls.constant(target.values[1], 12).values[0])
                if (kind == 8 and name != "<init>") or (kind != 8 and name in ("<init>", "<clinit>")):
                    raise JavaError("invalid method-handle target name")
    flags, this_index, super_index = reader.u2(), reader.u2(), reader.u2()
    name = cls.class_name(this_index)
    super_name = cls.class_name(super_index) if super_index else ""
    if name.startswith("[") or super_name.startswith("["):
        raise JavaError("class or superclass must not be an array")
    if not super_index and name != "java/lang/Object" and not flags & 0x8000:
        raise JavaError("only Object may have no superclass")
    interfaces = tuple(cls.class_name(reader.u2()) for _ in range(reader.u2()))
    field_count = reader.u2()
    fields = set()
    for _ in range(field_count):
        reader.u2()
        field_name, desc = cls.utf8(reader.u2()), cls.utf8(reader.u2())
        _name(field_name)
        descriptor(desc, method=False)
        if (field_name, desc) in fields:
            raise JavaError("duplicate field")
        fields.add((field_name, desc))
        _attributes(reader, cls)
    methods = []
    seen = set()
    for _ in range(reader.u2()):
        method_flags = reader.u2()
        method_name, desc = cls.utf8(reader.u2()), cls.utf8(reader.u2())
        _name(method_name, method=True)
        visibility = method_flags & 0x0007
        if method_name != "<clinit>" and visibility not in (0, 1, 2, 4):
            raise JavaError("method access flags public/private/protected are mutually exclusive")
        if method_flags & 0x0400 and method_flags & (0x0002 | 0x0008 | 0x0010 | 0x0020 | 0x0100):
            raise JavaError("abstract method has incompatible access flags")
        args, result = descriptor(desc)
        if method_name == "<init>" and (result != "V" or method_flags & 0x0008):
            raise JavaError("invalid constructor declaration")
        if method_name == "<clinit>" and (desc != "()V" or (major >= 51 and not method_flags & 0x0008)):
            raise JavaError("invalid class initializer declaration")
        if sum(2 if a in ("J", "D") else 1 for a in args) + (not method_flags & 8) > 255:
            raise JavaError("method exceeds parameter slot limit")
        if (method_name, desc) in seen:
            raise JavaError("duplicate method")
        seen.add((method_name, desc))
        attrs = _attributes(reader, cls)
        codes = [attr for attr in attrs if attr.name == "Code"]
        abstract_native = bool(method_flags & (0x0400 | 0x0100))
        if len(codes) != (0 if abstract_native else 1):
            raise JavaError("method must have exactly one Code attribute unless abstract/native")
        code = _code(codes[0].data, cls) if codes else None
        methods.append(Method(method_flags, method_name, desc, code))
    attrs = _attributes(reader, cls)
    bootstraps = [a for a in attrs if a.name == "BootstrapMethods"]
    if len(bootstraps) > 1:
        raise JavaError("duplicate BootstrapMethods attribute")
    bootstrap_count = 0
    if bootstraps:
        bootstrap = Reader(bootstraps[0].data, "BootstrapMethods")
        bootstrap_count = bootstrap.u2()
        for _ in range(bootstrap_count):
            cls.constant(bootstrap.u2(), 15)
            for _ in range(bootstrap.u2()):
                cls.constant(bootstrap.u2(), 3, 4, 5, 6, 7, 8, 15, 16, 17)
        bootstrap.end()
    for entry in pool:
        if entry is not None and entry.tag in (17, 18) and entry.values[0] >= bootstrap_count:
            raise JavaError("dynamic constant bootstrap index out of range")
    reader.end()
    return ClassFile(major, minor, flags, name, super_name, interfaces,
                     field_count, tuple(pool), tuple(methods))
