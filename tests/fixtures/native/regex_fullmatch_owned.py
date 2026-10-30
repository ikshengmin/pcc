"""ASCII fullmatch contract shared with the CPython semantic oracle."""
import gc
import re
import threading


def make_pattern(prefix):
    return prefix + '(a|ab)'


def make_text(prefix):
    gc.collect()
    return prefix + 'ab'


def fail_text():
    gc.collect()
    raise ValueError('second argument')


def make_receiver():
    return re.compile('(a|ab)')


class Bound:
    def __index__(self):
        gc.collect()
        return 1


def check():
    assert re.fullmatch(make_pattern(''), make_text('')).group() == 'ab'
    assert make_receiver().fullmatch(make_text('')).group() == 'ab'
    try:
        re.fullmatch(make_pattern(''), fail_text())
    except ValueError as error:
        assert str(error) == 'second argument'
    else:
        raise AssertionError('argument exception lost')
    assert re.compile('a').fullmatch('zaq', Bound(), 2).group() == 'a'
    pattern = re.compile('(?P<left>a|ab)(c)?')
    match = pattern.fullmatch('ab')
    assert match.group() == 'ab'
    assert match.group('left') == 'ab'
    assert match.group(2) is None
    assert match.groups('missing') == ('ab', 'missing')
    assert match.groupdict('missing') == {'left': 'ab'}
    assert match.span() == (0, 2)
    assert match.start(2) == -1
    assert match.end(2) == -1
    assert re.compile('a').fullmatch('zaq', 1, 2).span() == (1, 2)
    assert re.compile('').fullmatch('ab', 2, 1) is None
    assert re.compile('a.b').fullmatch('a\x00b').group() == 'a\x00b'
    assert re.compile('a*?').fullmatch('aaa').group() == 'aaa'
    assert re.fullmatch('(a|ab)', 'ab').group() == 'ab'
    gc.collect()
    assert match.group(1) == 'ab'


def worker():
    for index in range(12):
        check()


worker()
first = threading.Thread(target=worker)
second = threading.Thread(target=worker)
first.start()
second.start()
first.join()
second.join()
check()
print('REGEX_FULLMATCH_OK')
