"""Primitive operations of ordinary Python programs, one loop each.

The same source runs under CPython and compiled by pcc, so
scripts/pcc_perf.py can report, per primitive, what one operation costs in
CPython, in compiled code with each collector, and how four workers scale.
A request of the handler benchmark is a few dozen of these; a primitive
that costs more compiled than interpreted, or more under one collector than
another, is where that request's time goes.

Every function runs its operation ``count`` times and returns a value that
depends on every iteration.
"""

import json
import time


class Point:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

    def total(self) -> int:
        return self.x + self.y


def add(a: int, b: int) -> int:
    return a + b


def scaled(value: int, factor: int = 2) -> int:
    return value * factor


def countdown(count: int):
    while count > 0:
        yield count
        count -= 1


def op_loop(count: int) -> int:
    total = 0
    index = 0
    while index < count:
        total += index
        index += 1
    return total


def op_range(count: int) -> int:
    total = 0
    for index in range(count):
        total += index
    return total


def op_call(count: int) -> int:
    total = 0
    index = 0
    while index < count:
        total = add(total, index)
        index += 1
    return total


def op_kwcall(count: int) -> int:
    total = 0
    index = 0
    while index < count:
        total += scaled(index, factor=3)
        index += 1
    return total


def op_method(count: int) -> int:
    point = Point(1, 2)
    total = 0
    index = 0
    while index < count:
        total += point.total()
        index += 1
    return total


def op_attr(count: int) -> int:
    point = Point(1, 2)
    total = 0
    index = 0
    while index < count:
        point.x = index
        total += point.x
        index += 1
    return total


def op_instance(count: int) -> int:
    total = 0
    index = 0
    while index < count:
        point = Point(index, 1)
        total += point.y
        index += 1
    return total


def op_tuple(count: int) -> int:
    total = 0
    index = 0
    while index < count:
        pair = (index, 1)
        total += pair[1]
        index += 1
    return total


def op_list(count: int) -> int:
    total = 0
    index = 0
    while index < count:
        items = [index, 1, 2]
        total += items[1]
        index += 1
    return total


def op_dict(count: int) -> int:
    total = 0
    index = 0
    while index < count:
        row = {"name": "Ada", "plan": "pro"}
        total += len(row)
        index += 1
    return total


def op_dict_get(count: int) -> int:
    row = {"name": 1, "plan": 2, "notifications": 3, "profile": 4}
    total = 0
    index = 0
    while index < count:
        total += row["plan"]
        index += 1
    return total


def op_dict_set(count: int) -> int:
    row = {"name": 1, "plan": 2}
    index = 0
    while index < count:
        row["plan"] = index
        index += 1
    return len(row)


def op_list_append(count: int) -> int:
    total = 0
    index = 0
    while index < count:
        items = []
        inner = 0
        while inner < 16:
            items.append(inner)
            inner += 1
        total += len(items)
        index += 16
    return total


def op_list_iter(count: int) -> int:
    items = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]
    total = 0
    index = 0
    while index < count:
        for item in items:
            total += item
        index += 16
    return total


def op_str_concat(count: int) -> int:
    name = "Ada"
    plan = "pro"
    total = 0
    index = 0
    while index < count:
        text = name + ":" + plan
        total += len(text)
        index += 1
    return total


def op_int_str(count: int) -> int:
    total = 0
    index = 0
    while index < count:
        total += len(str(index))
        index += 1
    return total


def op_fstring(count: int) -> int:
    name = "Ada"
    total = 0
    index = 0
    while index < count:
        text = f"{name}:{index}"
        total += len(text)
        index += 1
    return total


def op_encode(count: int) -> int:
    text = '{"notifications": ["Your report is ready"], "profile": {"name": "Ada", "plan": "pro"}}'
    expected = text.encode()
    total = 0
    index = 0
    while index < count:
        body = text.encode()
        if body == expected:
            total += 1
        index += 1
    return total


def op_json(count: int) -> int:
    total = 0
    index = 0
    while index < count:
        data = {"profile": {"name": "Ada", "plan": "pro"},
                "notifications": ["Your report is ready"]}
        total += len(json.dumps(data, sort_keys=True))
        index += 1
    return total


def op_closure(count: int) -> int:
    total = 0
    index = 0
    while index < count:
        def bump(value: int) -> int:
            return value + index
        total += bump(1)
        index += 1
    return total


def op_try(count: int) -> int:
    total = 0
    index = 0
    while index < count:
        try:
            total += index
        finally:
            total += 1
        index += 1
    return total


def op_raise(count: int) -> int:
    total = 0
    index = 0
    while index < count:
        try:
            raise ValueError("primitive")
        except ValueError:
            total += 1
        index += 1
    return total


def op_generator(count: int) -> int:
    total = 0
    for value in countdown(count):
        total += value
    return total


def op_clock(count: int) -> int:
    total = 0
    index = 0
    while index < count:
        if time.perf_counter() > 0.0:
            total += 1
        index += 1
    return total


OPS = {
    "loop": op_loop,
    "range": op_range,
    "call": op_call,
    "kwcall": op_kwcall,
    "method": op_method,
    "attr": op_attr,
    "instance": op_instance,
    "tuple": op_tuple,
    "list": op_list,
    "dict": op_dict,
    "dict_get": op_dict_get,
    "dict_set": op_dict_set,
    "list_append": op_list_append,
    "list_iter": op_list_iter,
    "str_concat": op_str_concat,
    "int_str": op_int_str,
    "fstring": op_fstring,
    "encode": op_encode,
    "json": op_json,
    "closure": op_closure,
    "try": op_try,
    "raise": op_raise,
    "generator": op_generator,
    "clock": op_clock,
}
