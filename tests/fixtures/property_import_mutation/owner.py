"""Ordinary descriptor owners imported by the native mutation oracle."""


class Cell:
    __slots__ = ('_value', 'writes')

    def __init__(self, value):
        self._value = value
        self.writes = 0

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value, token=2):
        self._value = value
        self.writes += token

    @value.deleter
    def value(self, token=3):
        self._value = None
        self.writes += token


class ReadOnly:
    @property
    def value(self):
        return 7
