"""Direct source calls whose final ...T has a proven call-bounded backing.

Only the original formal can be borrowed. Values, globals, raw addresses,
closures and opaque callees never acquire an alias to its descriptor or storage.
The regular whole-program call-edge/semantic validation still checks each body.
"""
from .diagnostics import GoDiagnostic, UnsupportedGo
from .records import underlying
from .types import SliceType, is_integer, is_float, is_error_interface


def checked_span(dtype, token):
    q = underlying(dtype)
    if (not isinstance(q, SliceType) or not
            (is_integer(q.element) or is_float(q.element) or underlying(q.element) == 'bool'
             or is_error_interface(q.element))):
        raise UnsupportedGo(token, 'go.unsupported.slice-storage',
                            'variadic elements require qualified scalar or nil-only error storage')
    return q


def borrow(expr):
    if not (expr.kind == 'name' and getattr(expr.symbol, 'variadic_formal', False)
            or expr.kind == 'variadic_view' and expr.args[0].variadic_use):
        raise UnsupportedGo(expr.token, 'go.unsupported.slice-storage',
                            'only a direct variadic parameter has proven borrowed backing storage')
    checked_span(expr.type, expr.token)
    expr.variadic_use = True


def reserve(function, payload_bytes, token):
    """Admission for all compiler-created pack arrays and span descriptors.

    Ordinary Go locals, existing range induction slots and backend spills are
    not part of this distinct bound. Generic source instances start afresh.
    """
    required = 24 + payload_bytes
    if function.variadic_alloc_sites >= 256 or function.variadic_frame_bytes + required > 16*1024:
        raise UnsupportedGo(token, 'go.unsupported.limit-function-arity',
                            'variadic call frame exceeds bounded pack/view storage')
    function.variadic_alloc_sites += 1
    function.variadic_frame_bytes += required


def check_call(analyzer, expr, function, values, parameters):
    from .sema import coerce
    own = len(parameters)-1
    span = checked_span(parameters[-1][1], expr.token)
    if analyzer.function is None:
        raise UnsupportedGo(expr.token, 'go.unsupported.slice-storage',
                            'variadic pack has no checked caller frame')
    if len(values)<own or (expr.spread is not None and len(values)!=own+1):
        raise GoDiagnostic(expr.token, 'argument count mismatch for variadic function '+function.token.text)
    for arg, (_, typ) in zip(values[:own],parameters[:own]):
        coerce(arg,typ)
    tail = values[own:]
    if expr.spread is not None:
        arg = tail[0]
        if arg.type != 'untyped nil':
            if arg.type != span:
                raise GoDiagnostic(arg.token, 'variadic expansion requires '+str(span))
            borrow(arg)
            expr.variadic_mode = 'forward'
            return
        expr.variadic_mode = 'nil-spread'
        tail = []
    else:
        expr.variadic_mode = 'pack'
    # Also bound cumulative caller backing, including 3-word descriptors.
    # Every admitted element is at most one machine word on this target.
    if len(tail)>256:
        raise UnsupportedGo(expr.token,'go.unsupported.limit-function-arity',
                            'variadic call frame exceeds bounded pack storage')
    reserve(analyzer.function,8*len(tail),expr.token)
    for arg in tail:
        coerce(arg,span.element)
    expr.pack_count=len(tail)
    analyzer.function.pack_sites.append((expr,span))


def entry_storage(lower, source, ir, ir_type):
    stores={}
    for i,(expr,span) in enumerate(source.pack_sites):
        desc=lower.builder.alloca(ir_type(span),name='rest_span_'+str(i))
        count=expr.pack_count
        # Allocate once per source call site and activation, not once per loop
        # iteration. No caller backing can escape its active direct call.
        data=(lower.builder.alloca(ir.ArrayType(ir_type(span.element),count), name='rest_data_'+str(i))
              if count else None)
        stores[id(expr)]=(desc,data)
    return stores


def packed(lower, expr, arguments, ir, ir_type):
    if expr.variadic_mode=='forward':
        return arguments  # exact original descriptor/pointer, not a copy of elements
    span=expr.symbol.params[-1][1]
    desc, data=lower.pack_slots[id(expr)]
    count=expr.pack_count
    d=ir.IntType(32);i64=ir.IntType(64)
    fixed=len(expr.symbol.params)-1
    values=arguments[fixed:]
    assert len(values)==count
    if data is None:
        ptr=ir.Constant(ir_type(span.element).as_pointer(),None)
    else:
        ptr=lower.builder.gep(data,[ir.Constant(d,0),ir.Constant(i64,0)])
        for i,v in enumerate(values):
            lower.builder.store(v,lower.builder.gep(ptr,[ir.Constant(i64,i)]))
    for i,v in enumerate((ptr,ir.Constant(i64,count),ir.Constant(i64,count))):
        lower.builder.store(v,lower.builder.gep(desc,[ir.Constant(d,0),ir.Constant(d,i)]))
    return arguments[:fixed]+[lower.builder.load(desc)]


def clear_span(lower, expr, ir, ir_type):
    # Uses the original array, including when this formal was forwarded from
    # another call. Nil and other zero lengths perform no memory access.
    b=lower.builder
    span=lower.expression(expr)
    ptr=b.extract_value(span,0); length=b.extract_value(span,1)
    start=b.block
    check=lower.function.append_basic_block('rest_clear_check')
    write=lower.function.append_basic_block('rest_clear_element')
    done=lower.function.append_basic_block('rest_clear_done')
    b.branch(check);b.position_at_end(check)
    i=b.phi(ir.IntType(64))
    i.add_incoming(ir.Constant(ir.IntType(64),0),start)
    b.cbranch(b.icmp_unsigned('<',i,length),write,done)
    b.position_at_end(write)
    b.store(lower.zero(expr.type.element),b.gep(ptr,[i]))
    next_i=b.add(i,ir.Constant(ir.IntType(64),1))
    b.branch(check);i.add_incoming(next_i,write)
    b.position_at_end(done)
